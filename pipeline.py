#!/usr/bin/env python3
"""Convert MINOS SNTP files from tape storage, at Fermilab.

Runs next to the tape system, because the SNTP files are what is big and the
conversion is what makes them small -- a data file comes out around 3% of its
input. Output is written to a MINOS data area; moving it onward is a separate
bulk transfer, which keeps a days-long tape job independent of the network
and puts no credentials on a shared machine.

The normal way to run this is against a SAM dataset definition, staged first:

    samweb prestage-dataset --defname=my_definition --parallel=4
    python pipeline.py --defname my_definition /exp/minos/data/users/$USER/archive

This tool does not stage anything itself -- that is `samweb`'s job, done
once, up front. It only watches each file's locality and converts it once
dCache reports it on disk. A /pnfs directory or a list of paths also works,
for files staged some other way:

    python pipeline.py /pnfs/minos/reco_far/elm7/sntp_data/2016-06 \
        /exp/minos/data/users/$USER/archive

Each file moves through:

    pending -> online -> done

`online` means dCache reports the file on disk and it can be read now.
Progress is recorded in a ledger beside the output, so a run over tens of
thousands of files survives being interrupted.

Nothing is copied to local disk. A file on dCache disk is read over XRootD,
which is the protocol meant for that; reading the /pnfs NFS mount directly is
against Fermilab guidance, because heavy traffic on those convenience mounts
stalls the interactive node for everyone.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import branches as manifest
import export

# In order. A file only ever moves forward, or to "failed".
FLOW = ["pending", "online", "done"]
# (state, label) for the progress display
SHOWN = [("online", "on disk"), ("done", "converted")]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def epoch(iso: str) -> float:
    return datetime.fromisoformat(iso).timestamp()


# --------------------------------------------------------------------------
# Ledger
# --------------------------------------------------------------------------


class Ledger:
    """Per-file state, on disk, so an interrupted run can be resumed.

    Written whole and atomically rather than appended to: a few thousand
    records is a small file, and a torn ledger would be worse than a slow one.
    """

    def __init__(self, path: Path):
        self.path = path
        self.records: dict[str, dict] = {}
        if path.exists():
            self.records = json.loads(path.read_text())

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.records, indent=1, sort_keys=True))
        os.replace(tmp, self.path)  # atomic on POSIX

    def add(self, key: str) -> None:
        self.records.setdefault(key, {"state": "pending", "since": now()})

    def set(self, key: str, state: str, **fields) -> None:
        record = self.records.setdefault(key, {})
        record.update(state=state, since=now(), **fields)

    def state(self, key: str) -> str:
        return self.records.get(key, {}).get("state", "pending")

    def in_state(self, *states: str) -> list[str]:
        return [k for k, v in self.records.items() if v.get("state") in states]

    def reached(self, state: str) -> int:
        """How many files have got at least as far as `state`."""
        rank = FLOW.index(state)
        return sum(
            1
            for v in self.records.values()
            if v.get("state") in FLOW and FLOW.index(v["state"]) >= rank
        )


# --------------------------------------------------------------------------
# SAM
# --------------------------------------------------------------------------


def samweb(*args: str) -> str:
    """Run a samweb subcommand and return its stdout.

    samweb's own error messages already say what is wrong -- a typo'd
    definition, no station configured, not logged in -- so they are surfaced
    as-is rather than wrapped in a traceback.
    """
    if shutil.which("samweb") is None:
        raise SystemExit(
            "cannot find the samweb command.\n"
            "    it resolves SAM definitions to files, so nothing works "
            "without it.\n"
            "    run: source ./setup.sh"
        )
    done = subprocess.run(["samweb", *args], capture_output=True, text=True)
    if done.returncode != 0:
        raise SystemExit(
            f"samweb {' '.join(args)} failed:\n"
            f"    {(done.stderr or done.stdout).strip()}\n"
            "    is the session set up? run: source ./setup.sh"
        )
    return done.stdout


def pnfs_rel(url: str) -> Path:
    """The /pnfs sub-path a streaming URL was built from.

    root://door/pnfs/fnal.gov/usr/minos/reco_far/elm7/.../F....root
    -> reco_far/elm7/.../F....root

    Used to mirror a SAM definition's files into the same directory shape
    the directory-based mode produces, without a second samweb call per file.
    """
    path = Path("/" + url.split("://", 1)[-1].split("/", 1)[-1])
    parts = path.parts
    if "usr" in parts:
        return Path(*parts[parts.index("usr") + 2:])
    return Path(path.name)


def resolve_definition(defname: str) -> tuple[list[str], dict[str, str], dict[str, Path]]:
    """Every file in a SAM dataset definition, with a streaming URL for each.

    One samweb call lists the definition; one more per file asks where it
    actually lives, since a SAM filename alone carries no path. This is
    metadata only -- it does not stage anything. Stage the definition first:
    `samweb prestage-dataset --defname=... --parallel=4` (see the README).
    """
    names = [n for n in samweb("list-files", f"defname: {defname}").splitlines() if n.strip()]
    if not names:
        raise SystemExit(
            f"definition {defname!r} has no files, or does not exist.\n"
            f"    check: samweb describe-definition {defname}"
        )
    url_of: dict[str, str] = {}
    rel_of: dict[str, Path] = {}
    for name in names:
        url_of[name] = samweb("get-file-access-url", "--schema=root", name).strip()
        rel_of[name] = pnfs_rel(url_of[name])
    return names, url_of, rel_of


# --------------------------------------------------------------------------
# dCache
# --------------------------------------------------------------------------


def xrootd_url(pnfs: str, door: str) -> str:
    """The streaming URL for a file on the /pnfs NFS mount.

    Two namespaces name the same file. The mount on a gpvm is
    `/pnfs/minos/...`; dCache's own namespace, which the URL forms use, is
    `/pnfs/fnal.gov/usr/minos/...`. A path already in URL form is passed
    through, so a list file may hold either.

    Streaming rather than copying is deliberate. The obvious alternative --
    reading `/pnfs/...` directly -- is explicitly against Fermilab guidance:
    those NFS mounts are a convenience for metadata, and heavy read traffic
    on them stalls the interactive node for everyone. XRootD is the protocol
    meant for this.
    """
    if "://" in pnfs:
        return pnfs
    parts = Path(pnfs).parts
    if len(parts) < 3 or parts[1] != "pnfs":
        return pnfs  # an ordinary local file; uproot opens it directly
    experiment, rest = parts[2], "/".join(parts[3:])
    return f"root://{door}/pnfs/fnal.gov/usr/{experiment}/{rest}"


_xrootd_fs: dict[str, object] = {}


def _online_xrootd(url: str) -> bool:
    """XRootD stat fallback, for anything not on the /pnfs NFS mount --
    a SAM-resolved URL, or a dcap://-form list entry. Confirmed reachable
    with the bindings fsspec-xrootd already installs.
    """
    from urllib.parse import urlsplit

    from XRootD import client
    from XRootD.client.flags import StatInfoFlags

    parts = urlsplit(url)
    server = f"{parts.scheme}://{parts.netloc}"
    fs = _xrootd_fs.get(server)
    if fs is None:
        fs = _xrootd_fs[server] = client.FileSystem(server)
    status, stat = fs.stat(parts.path)
    if not status.ok or stat is None:
        return False
    return not bool(stat.flags & StatInfoFlags.OFFLINE)


def is_online(src: str, url: str) -> bool:
    """Whether the file is on dCache disk now -- readable without delay.

    Prefers the dCache NFS dot-command, a plain metadata read no heavier
    than `ls`: `<dir>/.(get)(<name>)(locality)` returns one of three
    documented values, ONLINE / NEARLINE / ONLINE_AND_NEARLINE -- readable
    now means the string contains ONLINE. Used when `src` is a mounted
    /pnfs path; falls back to an XRootD stat otherwise.

    This performs no staging and issues no request of any kind -- it only
    observes. Getting the file onto disk in the first place is someone
    else's job: `samweb prestage-dataset` for a SAM definition, run once
    ahead of time (see the README).
    """
    parts = Path(src).parts
    if "://" not in src:
        if len(parts) >= 3 and parts[1] == "pnfs":
            dotfile = Path(src).parent / f".(get)({Path(src).name})(locality)"
            try:
                return "ONLINE" in dotfile.read_text()
            except OSError:
                pass  # not there, or /pnfs not mounted -- fall through
        else:
            return True  # an ordinary local file -- nothing to stage
    return _online_xrootd(url)


# --------------------------------------------------------------------------
# Progress
# --------------------------------------------------------------------------


class Progress:
    """Two counters. A live block on a terminal, plain lines in a log.

    A long run belongs in tmux or nohup, and redrawing bars into a log file
    produces something unreadable, so the two cases are handled separately.
    """

    def __init__(self, total: int, live: bool):
        self.total = total
        self.live = live
        self.drawn = 0
        self.bytes_out = 0
        self.note = ""

    def line(self, message: str) -> None:
        """A one-off message that should survive above the live block."""
        if self.live and self.drawn:
            sys.stdout.write(f"\033[{self.drawn}A\033[J")
            self.drawn = 0
        print(message, flush=True)

    def draw(self, ledger: Ledger) -> None:
        counts = {state: ledger.reached(state) for state, _ in SHOWN}
        failed = len(ledger.in_state("failed"))
        if not self.live:
            return
        if self.drawn:
            sys.stdout.write(f"\033[{self.drawn}A")
        rows = []
        for state, label in SHOWN:
            n = counts[state]
            filled = 0 if not self.total else round(10 * n / self.total)
            bar = "#" * filled + "." * (10 - filled)
            rows.append(f"  {label:<10} {bar} {n:>6}/{self.total}")
        rows[-1] += f"   {export.human(self.bytes_out)} written"
        if failed:
            rows.append(f"  {'failed':<10} {'':<10} {failed:>6}")
        rows.append(f"  now: {self.note[:70]}")
        for row in rows:
            sys.stdout.write("\033[K" + row + "\n")
        sys.stdout.flush()
        self.drawn = len(rows)


# --------------------------------------------------------------------------
# The run
# --------------------------------------------------------------------------


def read_inputs(path: Path, pattern: str) -> tuple[list[str], Path]:
    """The files to convert, and the directory their tree is relative to.

    Takes a /pnfs directory -- listing one is metadata only and does not
    touch tape -- or a single .root file, or a text file of paths one per
    line for a curated subset. Returns paths as strings because a list file
    may hold `dcap://` URLs, which are not filesystem paths.
    """
    if path.is_dir():
        found = sorted(str(p) for p in path.glob(pattern))
        return found, path

    if path.suffix == ".root":
        return [str(path)], path.parent

    if not path.exists():
        # Most likely a mistyped /pnfs path. A traceback is a poor answer to
        # that, and /pnfs not being mounted looks identical from here.
        raise SystemExit(
            f"no such file or directory: {path}\n"
            "    expected a /pnfs directory, or a text file listing paths.\n"
            "    if this is a /pnfs path, check it is mounted: ls /pnfs/minos"
        )

    try:
        text = path.read_text()
    except UnicodeDecodeError:
        raise SystemExit(
            f"cannot read {path} as a list of paths: it is not text.\n"
            "    give a /pnfs directory, a single .root file, or a text file\n"
            "    listing paths one per line."
        )

    lines = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            lines.append(line)
    if not lines:
        return [], path
    root = Path(os.path.commonpath(lines)) if len(lines) > 1 else Path(lines[0]).parent
    return lines, root


def run(args) -> int:
    if args.defname:
        sources, url_of, rel_of = resolve_definition(args.defname)
        source_label = f"SAM definition {args.defname!r}"
    else:
        sources, root = read_inputs(args.files, args.pattern)
        url_of = {s: xrootd_url(s, args.door) for s in sources}
        rel_of = {s: Path(s).relative_to(root) for s in sources}
        source_label = str(args.files)

    if not sources:
        where = f"under {args.files} matching {args.pattern!r}" \
            if not args.defname and args.files.is_dir() else f"in {source_label}"
        print(f"No files {where}", file=sys.stderr)
        return 2

    out_dir = args.output.resolve()
    ledger = Ledger(out_dir / ".ledger.json")

    wanted = manifest.load(args.manifest)
    print(f"{len(sources)} file(s) from {source_label}; {len(wanted)} branches "
          f"enabled; format {args.format}")
    print(f"output  {out_dir}")
    print("reading over XRootD; this tool does not stage files itself -- "
          "make sure they are already on disk (samweb prestage-dataset, or "
          "equivalent) before or shortly after starting")

    if args.dry_run:
        print("\nDry run. Would watch and convert:")
        for src in sources[:10]:
            dst = (out_dir / rel_of[src]).with_suffix(export.formats_suffix(args.format))
            print(f"  {src}\n      -> {dst}")
        if len(sources) > 10:
            print(f"  ... and {len(sources) - 10} more")
        print("\nNo requests issued, nothing written.")
        return 0

    # XRootD's defaults are tuned for a healthy link; a dCache door under load
    # can exceed them and drop a read mid-file. Fermilab's own guidance is to
    # raise these. Only set what the caller has not.
    for name, value in (("XRD_STREAMTIMEOUT", "300"), ("XRD_REQUESTTIMEOUT", "3600"),
                        ("XRD_CONNECTIONRETRY", "32"), ("XRD_REDIRECTLIMIT", "255")):
        os.environ.setdefault(name, value)

    out_dir.mkdir(parents=True, exist_ok=True)
    if args.retry_failed:
        stuck = ledger.in_state("failed")
        for src in stuck:
            ledger.set(src, "pending", error="")
        if stuck:
            print(f"retrying {len(stuck)} previously failed file(s)")
    for src in sources:
        ledger.add(src)
    ledger.save()

    convert_args = SimpleNamespace(
        format=args.format, manifest=args.manifest, max_events=args.max_events
    )
    progress = Progress(len(sources), live=sys.stdout.isatty() and not args.plain)
    since_save = 0

    try:
        while True:
            done_now = ledger.reached("done") + len(ledger.in_state("failed"))
            if done_now >= len(sources):
                break
            worked = False

            # 1. which pending files are on disk now. This only observes --
            #    staging happens externally (samweb prestage-dataset ahead of
            #    time, or however the files got there); nothing here issues
            #    a stage request.
            deadline = args.stage_timeout * 3600
            for src in ledger.in_state("pending")[: args.poll_batch]:
                if is_online(src, url_of[src]):
                    ledger.set(src, "online")
                    worked = True
                elif time.time() - epoch(ledger.records[src]["since"]) > deadline:
                    ledger.set(src, "failed",
                               error=f"not online within {args.stage_timeout}h -- "
                                     "is it staged? samweb prestage-dataset")
                    progress.line(f"  GAVE UP {Path(src).name}: not online in "
                                  f"{args.stage_timeout}h")
                    worked = True

            # 2. convert, straight from dCache over XRootD. There is no
            #    local copy: the file is already on dCache disk by now, and
            #    streaming it is both faster to start and the only sanctioned
            #    way to read it from an interactive node.
            for src in ledger.in_state("online")[:1]:
                rel = rel_of[src]
                url = url_of[src]
                dst = (out_dir / rel).with_suffix(export.formats_suffix(args.format))
                dst.parent.mkdir(parents=True, exist_ok=True)

                if not args.no_check:
                    import check_exclusions

                    progress.note = f"checking {rel.name}"
                    progress.draw(ledger)
                    broken = check_exclusions.check_file(url, check_events=args.check_events)
                    if broken:
                        ledger.set(src, "failed", error=f"exclusion checks: {broken[0]}")
                        progress.line(f"  REFUSED {rel}: {broken[0]}")
                        worked = True
                        break

                progress.note = f"converting {rel.name}"
                progress.draw(ledger)
                result = export.convert(url, dst, convert_args)
                if result["ok"]:
                    progress.bytes_out += dst.stat().st_size
                    ledger.set(src, "done", events=result.get("events"),
                               out_bytes=dst.stat().st_size)
                    if result.get("unlisted"):
                        progress.line(f"  note {rel}: {len(result['unlisted'])} branch(es) "
                                      f"not in the manifest, e.g. {result['unlisted'][0]}")
                else:
                    ledger.set(src, "failed", error=result["error"])
                    progress.line(f"  FAILED convert {rel}: {result['error']}")
                worked = True

            since_save += 1
            if worked or since_save > 20:
                ledger.save()
                since_save = 0
            progress.draw(ledger)
            if not worked:
                progress.note = "waiting for tape"
                progress.draw(ledger)
                time.sleep(args.poll_interval)
    except KeyboardInterrupt:
        progress.line("\nInterrupted. Ledger saved; rerun to resume.")
    finally:
        ledger.save()

    progress.note = "finished"
    progress.draw(ledger)
    failed = ledger.in_state("failed")
    progress.line("")
    print(f"{'-' * 60}")
    print(f"done {ledger.reached('done')}, failed {len(failed)}, "
          f"{export.human(progress.bytes_out)} written to {out_dir}")
    for src in failed[:20]:
        print(f"  {src}\n      {ledger.records[src].get('error', '')}")
    if len(failed) > 20:
        print(f"  ... and {len(failed) - 20} more")
    return 1 if failed else 0


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("files", type=Path, nargs="?",
                   help="a /pnfs directory, a single .root file, or a text file of "
                        "paths one per line -- omit this and use --defname instead")
    p.add_argument("--defname", metavar="NAME",
                   help="a SAM dataset definition to process, in place of `files`. "
                        "Stage it first: samweb prestage-dataset --defname=NAME")
    p.add_argument("--pattern", default="**/*.root", metavar="GLOB",
                   help="which files to take from a directory (default %(default)s); "
                        "ignored with --defname")
    p.add_argument("output", type=Path,
                   help="where the converted files go, e.g. "
                        "/exp/minos/data/users/$USER/archive")
    p.add_argument("-f", "--format", choices=("root", "hdf5"), default="root",
                   help="output format (default %(default)s)")
    p.add_argument("--manifest", type=Path, default=manifest.DEFAULT_MANIFEST)
    p.add_argument("--door", default="fndca1.fnal.gov:1094", metavar="HOST:PORT",
                   help="dCache XRootD door to stream through (default %(default)s); "
                        "ignored with --defname, where samweb supplies the URL")
    p.add_argument("--poll-batch", type=int, default=100, metavar="N",
                   help="files checked for locality per pass (default %(default)s)")
    p.add_argument("--poll-interval", type=float, default=30.0, metavar="S",
                   help="seconds to wait when there is nothing to do")
    p.add_argument("--stage-timeout", type=float, default=48.0, metavar="H",
                   help="give up on a file not online within this many hours "
                        "(default %(default)s). This tool does not stage files "
                        "itself, so a file stuck here was never staged, or is "
                        "still waiting behind a slow tape")
    p.add_argument("--max-events", type=int, default=None, help="cap per file, for testing")
    p.add_argument("--retry-failed", action="store_true",
                   help="reset previously failed files to pending and try again. "
                        "Tape and network failures are often transient")
    p.add_argument("--no-check", action="store_true",
                   help="skip the checks on dropped branches")
    p.add_argument("--check-events", type=int, default=5000)
    p.add_argument("--plain", action="store_true",
                   help="plain output even on a terminal")
    p.add_argument("--dry-run", action="store_true",
                   help="resolve the list and print the plan; no requests, "
                        "nothing written")
    args = p.parse_args(argv)
    if bool(args.files) == bool(args.defname):
        p.error("give exactly one of `files` or --defname")
    return args


def main(argv=None) -> int:
    return run(parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Stage MINOS SNTP files from tape and convert them, at Fermilab.

Runs next to the tape system, because the SNTP files are what is big and the
conversion is what makes them small -- a data file comes out around 3% of its
input. Output is written to a MINOS data area; moving it onward is a separate
bulk transfer, which keeps a days-long tape job independent of the network
and puts no credentials on a shared machine.

    python pipeline.py /pnfs/minos/reco_far/elm7/sntp_data/2016-06 \
        /exp/minos/data/users/$USER/archive

Each file moves through:

    pending -> requested -> online -> fetched -> done

`requested` means a prestage request is lodged with dCache; `online` means the
file is on dCache disk and can be fetched cheaply. Progress is recorded in a
ledger beside the output, so a run over tens of thousands of files survives
being interrupted -- and survives the staging area being wiped.

Nothing is copied to local disk. Once a file is on dCache disk it is read
over XRootD, which is the protocol meant for that; reading the /pnfs NFS
mount directly is against Fermilab guidance, because heavy traffic on those
convenience mounts stalls the interactive node for everyone.

On tape efficiency: `--prestage-ahead` is the setting that matters. dCache
can order many outstanding requests by tape volume and mount each tape once,
so keeping a lot of requests in flight is what avoids sending the robot back
to the same tape repeatedly.
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
FLOW = ["pending", "requested", "online", "done"]
# (state, label) for the progress display
SHOWN = [("requested", "requested"), ("online", "on disk"), ("done", "converted")]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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
# dCache
# --------------------------------------------------------------------------


def prestage(src: str, dccp: str) -> subprocess.Popen:
    """Ask dCache to bring a file to disk. Does not copy it, does not block.

    Launched with Popen rather than run() so the request is lodged whether or
    not this particular dccp build waits for the stage to finish.
    """
    return subprocess.Popen(
        [dccp, "-P", src],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def is_online(src: str, dccp: str) -> bool:
    """Whether the file is on dCache disk now. `-t -1` asks without waiting."""
    done = subprocess.run(
        [dccp, "-P", "-t", "-1", src],
        capture_output=True,
        text=True,
        timeout=120,
    )
    return done.returncode == 0


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


# --------------------------------------------------------------------------
# Progress
# --------------------------------------------------------------------------


class Progress:
    """Three counters. A live block on a terminal, plain lines in a log.

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

    Takes either a /pnfs directory -- listing it is metadata only and does
    not touch tape -- or a text file of paths, one per line, for a curated
    subset. Returns paths as strings because a list file may hold `dcap://`
    URLs, which are not filesystem paths.
    """
    if path.is_dir():
        found = sorted(str(p) for p in path.glob(pattern))
        return found, path

    if not path.exists():
        # Most likely a mistyped /pnfs path. A traceback is a poor answer to
        # that, and /pnfs not being mounted looks identical from here.
        raise SystemExit(
            f"no such file or directory: {path}\n"
            "    expected a /pnfs directory, or a text file listing paths.\n"
            "    if this is a /pnfs path, check it is mounted: ls /pnfs/minos"
        )

    lines = []
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            lines.append(line)
    if not lines:
        return [], path
    root = Path(os.path.commonpath(lines)) if len(lines) > 1 else Path(lines[0]).parent
    return lines, root


def run(args) -> int:
    sources, root = read_inputs(args.files, args.pattern)
    if not sources:
        where = f"under {args.files} matching {args.pattern!r}" \
            if args.files.is_dir() else f"listed in {args.files}"
        print(f"No files {where}", file=sys.stderr)
        return 2

    out_dir = args.output.resolve()
    ledger = Ledger(out_dir / ".ledger.json")

    wanted = manifest.load(args.manifest)
    print(f"{len(sources)} file(s); {len(wanted)} branches enabled; format {args.format}")
    print(f"output  {out_dir}")
    print(f"reading over XRootD from {args.door}, prestage-ahead "
          f"{args.prestage_ahead}")

    if args.dry_run:
        print("\nDry run. Would stage and convert:")
        for src in sources[:10]:
            rel = Path(src).relative_to(root)
            dst = (out_dir / rel).with_suffix(export.formats_suffix(args.format))
            print(f"  {src}\n      -> {dst}")
        if len(sources) > 10:
            print(f"  ... and {len(sources) - 10} more")
        print("\nNo tape requests issued, nothing written.")
        return 0

    # Fail on this now rather than on the first file. dccp is not always on
    # PATH -- it may need a UPS setup first -- and a traceback partway into a
    # run is a poor way to find that out.
    if not (shutil.which(args.dccp) or Path(args.dccp).is_file()):
        raise SystemExit(
            f"cannot find the dccp command: {args.dccp!r}\n"
            "    it moves files off tape, so nothing works without it.\n"
            "    on a gpvm it may need a UPS setup first; check with: which dccp"
        )

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
    requests: dict[str, subprocess.Popen] = {}
    since_save = 0

    try:
        while True:
            done_now = ledger.reached("done") + len(ledger.in_state("failed"))
            if done_now >= len(sources):
                break
            worked = False

            # 1. keep requests flowing to dCache -- this is the tape-efficiency knob
            in_flight = len(ledger.in_state("requested"))
            for src in ledger.in_state("pending")[: max(0, args.prestage_ahead - in_flight)]:
                requests[src] = prestage(src, args.dccp)
                ledger.set(src, "requested", requested_at=time.time())
                worked = True
            for src, proc in list(requests.items()):
                if proc.poll() is not None:
                    requests.pop(src, None)  # reap, do not wait

            # 2. which requests have landed. A file that never comes online
            #    would otherwise hold the run open forever, so requests get a
            #    deadline: tape can be slow, but not unboundedly.
            deadline = args.stage_timeout * 3600
            for src in ledger.in_state("requested")[: args.poll_batch]:
                if is_online(src, args.dccp):
                    ledger.set(src, "online")
                    worked = True
                elif time.time() - ledger.records[src].get("requested_at", 0) > deadline:
                    ledger.set(src, "failed",
                               error=f"never came online within {args.stage_timeout}h")
                    progress.line(f"  GAVE UP {Path(src).name}: not staged in "
                                  f"{args.stage_timeout}h")
                    worked = True

            # 3. convert, straight from dCache over XRootD. There is no
            #    local copy: the file is already on dCache disk by now, and
            #    streaming it is both faster to start and the only sanctioned
            #    way to read it from an interactive node.
            for src in ledger.in_state("online")[:1]:
                rel = Path(src).relative_to(root)
                url = xrootd_url(src, args.door)
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
    p.add_argument("files", type=Path,
                   help="a /pnfs directory, or a text file of paths one per line")
    p.add_argument("--pattern", default="**/*.root", metavar="GLOB",
                   help="which files to take from a directory (default %(default)s)")
    p.add_argument("output", type=Path,
                   help="where the converted files go, e.g. "
                        "/exp/minos/data/users/$USER/archive")
    p.add_argument("-f", "--format", choices=("root", "hdf5"), default="root",
                   help="output format (default %(default)s)")
    p.add_argument("--manifest", type=Path, default=manifest.DEFAULT_MANIFEST)
    p.add_argument("--prestage-ahead", type=int, default=500, metavar="N",
                   help="prestage requests kept in flight (default %(default)s). "
                        "The tape-efficiency knob: costs no local disk")
    p.add_argument("--door", default="fndca1.fnal.gov:1094", metavar="HOST:PORT",
                   help="dCache XRootD door to stream through (default %(default)s)")
    p.add_argument("--poll-batch", type=int, default=100, metavar="N",
                   help="requests checked for arrival per pass (default %(default)s)")
    p.add_argument("--poll-interval", type=float, default=30.0, metavar="S",
                   help="seconds to wait when there is nothing to do")
    p.add_argument("--stage-timeout", type=float, default=48.0, metavar="H",
                   help="give up on a file not staged within this many hours "
                        "(default %(default)s). Tape can be slow when busy, but "
                        "a request that never lands would hold the run open")
    p.add_argument("--dccp", default="dccp", help="dccp command (override to test)")
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
                   help="resolve the list and print the plan; no tape requests, "
                        "nothing written")
    return p.parse_args(argv)


def main(argv=None) -> int:
    return run(parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())

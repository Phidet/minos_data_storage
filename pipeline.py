#!/usr/bin/env python3
"""Stage MINOS SNTP files from tape and convert them, at Fermilab.

Runs next to the tape system, because the SNTP files are what is big and the
conversion is what makes them small -- a data file comes out around 3% of its
input. Output is written to a MINOS data area; moving it onward is a separate
bulk transfer, which keeps a days-long tape job independent of the network
and puts no credentials on a shared machine.

    python pipeline.py files.txt /exp/minos/data/users/$USER/archive

Each file moves through:

    pending -> requested -> online -> fetched -> done

`requested` means a prestage request is lodged with dCache; `online` means the
file is on dCache disk and can be fetched cheaply. Progress is recorded in a
ledger beside the output, so a run over tens of thousands of files survives
being interrupted -- and survives the staging area being wiped.

Output stays at Fermilab. Moving it on is a separate bulk transfer (Globus,
or a pull from the far end), which keeps a days-long tape job independent of
the network and puts no credentials on a shared machine.

On tape efficiency: `--prestage-ahead` is the setting that matters. dCache can
order many outstanding requests by tape volume and mount each tape once, so
keeping a lot of requests in flight is what avoids sending the robot back to
the same tape repeatedly. `--scratch-budget` only limits how much sits on
local disk; it should never be the binding constraint on staging.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import branches as manifest
import export

# In order. A file only ever moves forward, or to "failed".
FLOW = ["pending", "requested", "online", "fetched", "done"]
# (state, label) for the progress display
SHOWN = [("requested", "staged"), ("fetched", "fetched"), ("done", "converted")]


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


def fetch(src: str, dst: Path, dccp: str) -> tuple[bool, str]:
    """Copy an online file to local scratch. Disk to disk, so this is quick."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    done = subprocess.run(
        [dccp, src, str(dst)], capture_output=True, text=True
    )
    if done.returncode != 0:
        dst.unlink(missing_ok=True)
        tail = (done.stderr or done.stdout or "").strip().splitlines()
        return False, tail[-1] if tail else f"dccp exited {done.returncode}"

    # An unstaged dCache file reads back as the right size in zeros rather
    # than failing. That is how two 600 MB files arrived holding nothing, so
    # check the ROOT magic rather than trusting the exit status.
    if not dst.exists():
        return False, "dccp reported success but wrote no file"
    with open(dst, "rb") as handle:
        magic = handle.read(4)
    if magic != b"root":
        # Leave nothing behind: over a long run, a bad file kept per failure
        # would quietly eat the scratch budget.
        dst.unlink(missing_ok=True)
        return False, "copied file is not ROOT (unstaged? it reads as zeros)"
    return True, ""


# --------------------------------------------------------------------------
# Progress
# --------------------------------------------------------------------------


class Progress:
    """Four counters. A live block on a terminal, plain lines in a log.

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

    lines = []
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            lines.append(line)
    if not lines:
        return [], path
    root = Path(os.path.commonpath(lines)) if len(lines) > 1 else Path(lines[0]).parent
    return lines, root


def scratch_bytes(directory: Path) -> int:
    return sum(f.stat().st_size for f in directory.rglob("*") if f.is_file())


def run(args) -> int:
    sources, root = read_inputs(args.files, args.pattern)
    if not sources:
        where = f"under {args.files} matching {args.pattern!r}" \
            if args.files.is_dir() else f"listed in {args.files}"
        print(f"No files {where}", file=sys.stderr)
        return 2

    out_dir = args.output.resolve()
    # Staging is transient and budget-capped; the output is the deliverable and
    # grows without limit. Keeping them apart is what lets the budget mean
    # something. The ledger lives with the output, so wiping the staging area
    # costs nothing but the files currently in flight.
    staged_dir = (args.work or out_dir / ".staging").resolve()
    ledger = Ledger(out_dir / ".ledger.json")

    wanted = manifest.load(args.manifest)
    print(f"{len(sources)} file(s); {len(wanted)} branches enabled; format {args.format}")
    print(f"output  {out_dir}")
    print(f"staging {staged_dir}  (prestage-ahead {args.prestage_ahead}, "
          f"budget {args.scratch_budget} GB)")

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

    out_dir.mkdir(parents=True, exist_ok=True)
    staged_dir.mkdir(parents=True, exist_ok=True)
    if args.retry_failed:
        stuck = ledger.in_state("failed")
        for src in stuck:
            ledger.set(src, "pending", error="")
        if stuck:
            print(f"retrying {len(stuck)} previously failed file(s)")
    for src in sources:
        ledger.add(src)
    ledger.save()

    budget = args.scratch_budget * 1024 ** 3
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

            # 3. fetch, if there is room. The budget gates fetching only:
            #    requests keep going out regardless.
            online = ledger.in_state("online")
            used = scratch_bytes(staged_dir) if online else 0
            staged_now = len(ledger.in_state("fetched"))
            # Under budget, or nothing staged at all: a budget smaller than a
            # single file must still make progress rather than deadlock.
            if online and (used < budget or staged_now == 0):
                src = online[0]
                rel = Path(src).relative_to(root)
                local = staged_dir / rel
                progress.note = f"fetching {rel.name}"
                progress.draw(ledger)
                ok, err = fetch(src, local, args.dccp)
                if ok:
                    ledger.set(src, "fetched", bytes=local.stat().st_size)
                else:
                    ledger.set(src, "failed", error=err)
                    progress.line(f"  FAILED fetch  {rel}: {err}")
                worked = True

            # 4. convert
            for src in ledger.in_state("fetched")[:1]:
                rel = Path(src).relative_to(root)
                local = staged_dir / rel
                progress.note = f"converting {rel.name}"
                progress.draw(ledger)
                if not args.no_check:
                    import check_exclusions

                    broken = check_exclusions.check_file(local, check_events=args.check_events)
                    if broken:
                        ledger.set(src, "failed", error=f"exclusion checks: {broken[0]}")
                        progress.line(f"  REFUSED {rel}: {broken[0]}")
                        local.unlink(missing_ok=True)
                        worked = True
                        break
                dst = export.output_for(local, staged_dir, out_dir, args.format)
                result = export.convert(local, dst, convert_args)
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
                local.unlink(missing_ok=True)  # only ever inside our own scratch
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
    p.add_argument("--work", type=Path, default=None, metavar="DIR",
                   help="staging area for files pulled off tape "
                        "(default: <output>/.staging). Point it at local scratch "
                        "if the output area is slow or tight")
    p.add_argument("-f", "--format", choices=("root", "hdf5"), default="root",
                   help="output format (default %(default)s)")
    p.add_argument("--manifest", type=Path, default=manifest.DEFAULT_MANIFEST)
    p.add_argument("--prestage-ahead", type=int, default=500, metavar="N",
                   help="prestage requests kept in flight (default %(default)s). "
                        "The tape-efficiency knob: costs no local disk")
    p.add_argument("--scratch-budget", type=int, default=300, metavar="GB",
                   help="cap on the staging area (default %(default)s). "
                        "Gates fetching only, never prestaging. Does not limit "
                        "the output, which grows freely")
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

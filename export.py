#!/usr/bin/env python3
"""Export MINOS SNTP ROOT files to a slimmed archive.

Takes a file or a directory tree, keeps the branches enabled in
`branches.txt`, and writes HDF5 or ROOT to a mirrored output tree.

    python export.py data/sntp archive/            # whole tree, HDF5
    python export.py one.sntp.root out/ -f root    # one file, ROOT

What is kept is a policy decision recorded in `branches.txt`, not in this
script: the detector's and the simulation's own record, not MINOS's
reconstruction of it. See README.md.

Each file is converted in a subprocess. One file needs a couple of GB
resident and Python does not reliably hand that back between iterations, so
a long in-process loop climbs until it is killed. Interrupted runs resume;
one bad file is reported rather than fatal, and the exit status is non-zero
if any failed.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import branches as manifest

TREE = "NtpSt"

WORKER = r'''
import json, sys
from pathlib import Path
sys.path.insert(0, {root!r})

import awkward as ak
import uproot
import branches as manifest
import formats

src, dst, fmt = Path({src!r}), Path({dst!r}), {fmt!r}
wanted = manifest.load({manifest_path!r})

with uproot.open(src) as f:
    tree = f[{tree!r}]
    keys = tree.keys(recursive=True)
    present = set(keys)
    missing = [b for b in wanted if b not in present]
    if missing:
        raise SystemExit(
            f"{{len(missing)}} branch(es) in the manifest are not in this file, "
            f"e.g. {{missing[:3]}}"
        )

    # And the other direction: a branch this file has that the manifest has
    # never heard of is archived nowhere and reported nowhere, which is how
    # a file with a different branch set would be silently stripped. Only
    # real leaves count -- parent nodes and ROOT's own bookkeeping are not
    # data. Reported, not fatal: a new branch is news, not an error.
    parents = {{k.rsplit("/", 1)[0] for k in keys if "/" in k}}
    known = manifest.known({manifest_path!r})
    unlisted = [
        k for k in keys
        if k not in known
        and k.rsplit(".", 1)[-1] not in ("fBits", "fUniqueID")
        and not k.endswith("TObject")
        and k not in parents
    ]

    stop = {max_events!r}
    columns = {{}}
    for name in wanted:
        # short, stable column names: drop the NtpStRecord/<group>/ prefix
        short = name.split("/")[-1]
        columns[short] = tree[name].array(entry_stop=stop)
    n_events = tree.num_entries if stop is None else min(stop, tree.num_entries)

metadata = {{
    "source": str(src),
    "source_bytes": src.stat().st_size,
    "events": int(n_events),
    "branches": len(columns),
    "written": {written!r},
    "tool": "minos_data_storage/export.py",
}}

formats.WRITERS[fmt](dst, columns, metadata)
print("RESULT " + json.dumps({{
    "events": int(n_events),
    "branches": len(columns),
    "unlisted": unlisted,
}}))
'''


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:,.1f} {unit}"
        n /= 1024
    return f"{n:,.1f} PB"


def output_for(src: Path, in_dir: Path, out_dir: Path, fmt: str) -> Path:
    """Mirror `src` under `out_dir`, swapping the trailing .root.

    Only the last suffix changes, so `f21….sntp.dogwood5.0.root` keeps the
    middle components that distinguish it from its siblings.

    Raises if that lands on the input. Exporting ROOT to ROOT leaves the
    suffix unchanged, so an output directory equal to the input directory
    makes destination and source the same path -- and writing it destroys
    the file being read. This has happened; hence the check rather than a
    reliance on the caller choosing different directories.
    """
    dst = (out_dir / src.relative_to(in_dir)).with_suffix(formats_suffix(fmt))
    if dst.resolve() == src.resolve():
        raise ValueError(
            f"refusing to write over the input file: {src}\n"
            f"    output would land on the source (format {fmt!r} keeps the "
            f"same suffix).\n"
            f"    choose an output directory outside {in_dir}"
        )
    return dst


def formats_suffix(fmt: str) -> str:
    import formats

    return formats.SUFFIX[fmt]


def convert(src: Path, dst: Path, args) -> dict:
    code = WORKER.format(
        root=str(Path(__file__).resolve().parent),
        src=str(src),
        dst=str(dst),
        fmt=args.format,
        manifest_path=str(args.manifest),
        tree=TREE,
        max_events=args.max_events,
        written=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    started = time.perf_counter()
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    elapsed = time.perf_counter() - started

    if done.returncode != 0 or not dst.exists():
        tail = (done.stderr or "").strip().splitlines()
        return {
            "ok": False,
            "elapsed": elapsed,
            "error": tail[-1] if tail else f"exited {done.returncode}",
            "stderr": "\n".join(tail[-12:]),
        }

    payload = {}
    for line in done.stdout.splitlines():
        if line.startswith("RESULT "):
            payload = json.loads(line[len("RESULT "):])
    return {"ok": True, "elapsed": elapsed, **payload}


def report_unlisted(unlisted: list[str], show: int = 5) -> None:
    """Say which branches this file has that the manifest never mentions.

    Not a failure. A file whose branch set differs from the manifest is
    worth knowing about -- those branches went nowhere -- but it is a
    reason to look at the manifest, not to refuse the conversion.
    """
    if not unlisted:
        return
    print(f"    note: {len(unlisted)} branch(es) in this file are not in the "
          f"manifest and were not archived:")
    for name in unlisted[:show]:
        print(f"      {name}")
    if len(unlisted) > show:
        print(f"      ... and {len(unlisted) - show} more")


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("input", type=Path, help="a .root file, or a directory of them")
    p.add_argument("output", type=Path, nargs="?", help="where to write")
    p.add_argument("-f", "--format", choices=("root", "hdf5"), default="root",
                   help="output format (default %(default)s)")
    p.add_argument("--manifest", type=Path, default=manifest.DEFAULT_MANIFEST)
    p.add_argument("--pattern", default="**/*.root", help="default: %(default)s")
    p.add_argument("--max-events", type=int, default=None, help="cap per file, for testing")
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("--no-check", action="store_true",
                   help="skip the checks on dropped branches")
    p.add_argument("--check-events", type=int, default=5000)
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    if args.output is None:
        print("error: an output directory is required", file=sys.stderr)
        return 2

    args.input, args.output = args.input.resolve(), args.output.resolve()
    wanted = manifest.load(args.manifest)

    if args.input.is_dir():
        sources = sorted(args.input.glob(args.pattern))
        in_dir = args.input
    else:
        sources, in_dir = [args.input], args.input.parent

    if not sources:
        print(f"No files matching {args.pattern!r} under {args.input}")
        return 0

    groups = manifest.summarise(args.manifest)
    total = sum(t for _, t in groups.values())
    print(f"{len(sources)} file(s); {len(wanted)} of {total} branches enabled; "
          f"format {args.format}\n")

    converted, skipped, failures = [], [], []
    bytes_in = bytes_out = 0

    inputs = {s.resolve() for s in sources}

    for i, src in enumerate(sources, 1):
        try:
            dst = output_for(src, in_dir, args.output, args.format)
        except ValueError as exc:
            print(f"[{i}/{len(sources)}] {src.relative_to(in_dir)}\n    REFUSED: {exc}")
            failures.append((src, {"error": "output would overwrite the input"}))
            continue

        # --overwrite must never apply to a file we are reading
        if dst.resolve() in inputs:
            print(f"[{i}/{len(sources)}] {src.relative_to(in_dir)}\n"
                  f"    REFUSED: output {dst} is one of the input files")
            failures.append((src, {"error": "output collides with an input file"}))
            continue

        label = f"[{i}/{len(sources)}] {src.relative_to(in_dir)}"

        if dst.exists() and not args.overwrite:
            print(f"{label}\n    skipped, output exists")
            skipped.append(src)
            continue

        print(label, flush=True)

        if not args.no_check:
            import check_exclusions

            broken = check_exclusions.check_file(src, check_events=args.check_events)
            if broken:
                print(f"    REFUSED: {len(broken)} assumption(s) about dropped "
                      "branches do not hold for this file")
                for msg in broken:
                    print(f"      - {msg}")
                failures.append((src, {"error": "exclusion checks failed",
                                       "stderr": "\n".join(broken)}))
                continue

        result = convert(src, dst, args)
        if not result["ok"]:
            print(f"    FAILED after {result['elapsed']:.1f}s: {result['error']}")
            failures.append((src, result))
            continue

        a, b = src.stat().st_size, dst.stat().st_size
        bytes_in += a
        bytes_out += b
        print(f"    {result['events']:,} events, {result['branches']} branches")
        report_unlisted(result.get("unlisted", []))
        print(f"    {human(a)} -> {human(b)} ({b / a:.1%}) in {result['elapsed']:.1f}s")
        converted.append(src)

    print(f"\n{'-' * 60}")
    print(f"converted {len(converted)}, skipped {len(skipped)}, failed {len(failures)}")
    if bytes_in:
        print(f"total {human(bytes_in)} -> {human(bytes_out)} ({bytes_out / bytes_in:.1%})")
    if failures:
        print("\nFailures:")
        for src, result in failures:
            print(f"  {src}\n    {result.get('error', 'unknown')}")
            for line in (result.get("stderr") or "").splitlines()[-4:]:
                print(f"      | {line}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

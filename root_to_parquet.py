#!/usr/bin/env python3
"""Convert a MINOS sntp ROOT ntuple (NtpSt tree) into a small set of Parquet
tables that don't require ROOT/uproot or any MINOS-specific software to read.

Kept:
  - events            one row per event, run/subrun/snarl/event identifiers
  - hits              one row per digitized strip hit (the raw "data")
  - truth_event       one row per event, MC interaction truth (--mc only)
  - truth_particles   one row per final-state truth particle (--mc only)

Dropped: everything produced by the MINOS reconstruction chain (trk, shw,
vtx, slc, fit, lin, cr, purity/completeness/fiducial flags, ...).

Usage:
    python3 root_to_parquet.py --mc   input.sntp.root output_dir/
    python3 root_to_parquet.py --data input.sntp.root output_dir/
"""
import argparse
import sys
from pathlib import Path

import awkward as ak
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import uproot

TREE_NAME = "NtpSt"

HEADER_PREFIX = "NtpStRecord/RecRecordImp<RecCandHeader>/fHeader."
RUN_BRANCH = HEADER_PREFIX + "RecPhysicsHeader/fHeader.RecDataHeader/fHeader.fRun"
SUBRUN_BRANCH = HEADER_PREFIX + "RecPhysicsHeader/fHeader.RecDataHeader/fHeader.fSubRun"
SNARL_BRANCH = HEADER_PREFIX + "RecPhysicsHeader/fHeader.fSnarl"
EVENT_BRANCH = HEADER_PREFIX + "fEvent"

HIT_PREFIX = "NtpStRecord/stp/stp."
HIT_BRANCHES = {
    "plane": HIT_PREFIX + "plane",
    "view": HIT_PREFIX + "planeview",
    "strip": HIT_PREFIX + "strip",
    "z": HIT_PREFIX + "z",
    "pe0": HIT_PREFIX + "ph0.pe",
    "pe1": HIT_PREFIX + "ph1.pe",
    "time0": HIT_PREFIX + "time0",
    "time1": HIT_PREFIX + "time1",
}

MC_PREFIX = "NtpStRecord/mc/mc."
MC_TRUTH_SCALAR_BRANCHES = {
    "inu": MC_PREFIX + "inu",
    "inunoosc": MC_PREFIX + "inunoosc",
    "itg": MC_PREFIX + "itg",
    "a": MC_PREFIX + "a",
    "iaction": MC_PREFIX + "iaction",
    "iboson": MC_PREFIX + "iboson",
    "iresonance": MC_PREFIX + "iresonance",
    "istruckq": MC_PREFIX + "istruckq",
    "iflags": MC_PREFIX + "iflags",
    "x": MC_PREFIX + "x",
    "y": MC_PREFIX + "y",
    "z": MC_PREFIX + "z",
    "q2": MC_PREFIX + "q2",
    "w2": MC_PREFIX + "w2",
    "sigma": MC_PREFIX + "sigma",
    "sigmadiff": MC_PREFIX + "sigmadiff",
    "emfrac": MC_PREFIX + "emfrac",
    "vtxx": MC_PREFIX + "vtxx",
    "vtxy": MC_PREFIX + "vtxy",
    "vtxz": MC_PREFIX + "vtxz",
    "ndigu": MC_PREFIX + "ndigu",
    "ndigv": MC_PREFIX + "ndigv",
    "tphu": MC_PREFIX + "tphu",
    "tphv": MC_PREFIX + "tphv",
}
MC_TRUTH_P4_BRANCHES = {
    "p4neu": MC_PREFIX + "p4neu[4]",
    "p4neunoosc": MC_PREFIX + "p4neunoosc[4]",
    "p4tgt": MC_PREFIX + "p4tgt[4]",
    "p4shw": MC_PREFIX + "p4shw[4]",
    "p4mu1": MC_PREFIX + "p4mu1[4]",
    "p4mu2": MC_PREFIX + "p4mu2[4]",
    "p4el1": MC_PREFIX + "p4el1[4]",
    "p4el2": MC_PREFIX + "p4el2[4]",
    "p4tau": MC_PREFIX + "p4tau[4]",
}

PARTICLE_PREFIX = "NtpStRecord/stdhep/stdhep."
PARTICLE_BRANCHES = {
    "pdg": PARTICLE_PREFIX + "IdHEP",
    "status": PARTICLE_PREFIX + "IstHEP",
    "mass": PARTICLE_PREFIX + "mass",
    "p4": PARTICLE_PREFIX + "p4[4]",
    "vtx": PARTICLE_PREFIX + "vtx[4]",
}

# Fraction of mc.inu entries that must be non-zero for the file to count as
# carrying real MC truth (vs. a zero-filled placeholder in real-data files).
MC_TRUTH_FRACTION_THRESHOLD = 0.5


def read_branch(tree, branch_path):
    return tree[branch_path].array()


def read_event_ids(tree, n_events):
    run = read_branch(tree, RUN_BRANCH).to_numpy()
    subrun = read_branch(tree, SUBRUN_BRANCH).to_numpy()
    snarl = read_branch(tree, SNARL_BRANCH).to_numpy()
    event = read_branch(tree, EVENT_BRANCH).to_numpy()
    entry = np.arange(n_events, dtype=np.int64)
    return {
        "entry": entry,
        "run": run,
        "subrun": subrun,
        "snarl": snarl,
        "event": event,
    }


def detect_mc_truth_fraction(tree):
    keys = set(tree.keys(recursive=True))
    if MC_TRUTH_SCALAR_BRANCHES["inu"] not in keys:
        return 0.0
    inu = read_branch(tree, MC_TRUTH_SCALAR_BRANCHES["inu"])
    flat = ak.flatten(inu, axis=None)
    if len(flat) == 0:
        return 0.0
    return float(ak.mean(flat != 0))


def build_events_table(event_ids):
    return pa.Table.from_pydict(event_ids)


def explode_to_long_table(tree, branches, entry_ids, extra_id_name="event_id"):
    """Read a jagged (per-event list) collection and flatten it into a long
    (one row per item) table, repeating the event id for each item."""
    arrays = {name: read_branch(tree, path) for name, path in branches.items()}
    any_array = next(iter(arrays.values()))
    counts = ak.num(any_array, axis=1)
    counts_np = ak.to_numpy(counts)

    columns = {extra_id_name: np.repeat(entry_ids, counts_np)}
    for name, arr in arrays.items():
        flat = ak.flatten(arr, axis=1)
        if flat.ndim == 2:
            # fixed-size sub-array (e.g. a 4-vector) -> one column per component
            flat_np = ak.to_numpy(flat)
            for i in range(flat_np.shape[1]):
                columns[f"{name}{i}"] = flat_np[:, i]
        else:
            columns[name] = ak.to_numpy(flat)
    return pa.Table.from_pydict(columns)


def build_truth_event_table(tree, entry_ids):
    inu = read_branch(tree, MC_TRUTH_SCALAR_BRANCHES["inu"])
    counts = ak.to_numpy(ak.num(inu, axis=1))
    if np.any(counts > 1):
        bad = np.nonzero(counts > 1)[0][:5]
        raise ValueError(
            "Expected at most one MC truth record per event, found more than "
            f"one for entries {bad.tolist()} (and possibly others). This "
            "file's truth structure differs from what this script assumes; "
            "it needs to be extended before it can be converted."
        )
    has_truth = counts == 1

    columns = {"event_id": entry_ids}
    for name, path in MC_TRUTH_SCALAR_BRANCHES.items():
        arr = read_branch(tree, path)
        first = ak.to_numpy(ak.fill_none(ak.firsts(arr, axis=1), np.nan))
        columns[name] = first
    for name, path in MC_TRUTH_P4_BRANCHES.items():
        arr = read_branch(tree, path)
        first = ak.firsts(arr, axis=1)
        first_np = ak.to_numpy(
            ak.fill_none(first, [np.nan, np.nan, np.nan, np.nan])
        )
        for i in range(4):
            columns[f"{name}{i}"] = first_np[:, i]
    columns["has_truth"] = has_truth
    return pa.Table.from_pydict(columns)


def convert(input_path, output_dir, expect_mc):
    with uproot.open(input_path) as f:
        tree = f[TREE_NAME]
        n_events = tree.num_entries
        print(f"Read {n_events} events from {input_path}")

        truth_fraction = detect_mc_truth_fraction(tree)
        has_mc_truth = truth_fraction > MC_TRUTH_FRACTION_THRESHOLD
        if expect_mc and not has_mc_truth:
            raise SystemExit(
                "--mc was given but no MC truth was found "
                f"(non-zero mc.inu fraction = {truth_fraction:.3f}). "
                "This looks like a real-data file; rerun with --data."
            )
        if not expect_mc and has_mc_truth:
            raise SystemExit(
                "--data was given but MC truth was found "
                f"(non-zero mc.inu fraction = {truth_fraction:.3f}). "
                "This looks like a simulation file; rerun with --mc."
            )

        event_ids = read_event_ids(tree, n_events)
        entry_ids = event_ids["entry"]

        output_dir.mkdir(parents=True, exist_ok=True)

        events_table = build_events_table(event_ids)
        pq.write_table(events_table, output_dir / "events.parquet")
        print(f"  wrote events.parquet ({events_table.num_rows} rows)")

        hits_table = explode_to_long_table(tree, HIT_BRANCHES, entry_ids)
        pq.write_table(hits_table, output_dir / "hits.parquet")
        print(f"  wrote hits.parquet ({hits_table.num_rows} rows)")

        if expect_mc:
            truth_event_table = build_truth_event_table(tree, entry_ids)
            pq.write_table(truth_event_table, output_dir / "truth_event.parquet")
            print(f"  wrote truth_event.parquet ({truth_event_table.num_rows} rows)")

            truth_particles_table = explode_to_long_table(
                tree, PARTICLE_BRANCHES, entry_ids
            )
            pq.write_table(
                truth_particles_table, output_dir / "truth_particles.parquet"
            )
            print(
                f"  wrote truth_particles.parquet "
                f"({truth_particles_table.num_rows} rows)"
            )


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_root", type=Path, help="path to the .sntp.root file")
    parser.add_argument(
        "output_dir", type=Path, help="directory to write the Parquet tables into"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--mc",
        action="store_true",
        help="file is simulation: require MC truth to be present",
    )
    mode.add_argument(
        "--data",
        action="store_true",
        help="file is real data: require MC truth to be absent",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    convert(args.input_root, args.output_dir, expect_mc=args.mc)


if __name__ == "__main__":
    main(sys.argv[1:])

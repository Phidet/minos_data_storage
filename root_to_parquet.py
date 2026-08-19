#!/usr/bin/env python3
"""Convert a MINOS sntp ROOT ntuple (NtpSt tree) into Parquet tables that
don't require ROOT/uproot or any MINOS-specific software to read.

Kept:
  - data.parquet    one row per digitized strip hit (the raw "data"),
                     with event identifiers attached. Written always.
  - truth.parquet   one row per MC final-state truth particle, with the
                     event's MC interaction truth and identifiers attached.
                     Written for simulation files only (--mc).

Both tables are "left joins": an event with zero hits (or, for truth, zero
particles) still gets exactly one row, with the item-level columns null, so
`entry.nunique()` always equals the number of events in the source file.

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

# Bjorken x/y/z kinematic variables, *not* a spatial position (see vtxx/y/z
# below for the interaction vertex).
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
    "bjorken_x": MC_PREFIX + "x",
    "bjorken_y": MC_PREFIX + "y",
    "bjorken_z": MC_PREFIX + "z",
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
    return {
        "entry": np.arange(n_events, dtype=np.int64),
        "run": read_branch(tree, RUN_BRANCH).to_numpy(),
        "subrun": read_branch(tree, SUBRUN_BRANCH).to_numpy(),
        "snarl": read_branch(tree, SNARL_BRANCH).to_numpy(),
        "event": read_branch(tree, EVENT_BRANCH).to_numpy(),
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


def to_columns(name, array):
    """A 1-D array becomes one column; a fixed-width sub-array (e.g. a
    4-vector) becomes one column per component, named f"{name}{i}"."""
    np_array = ak.to_numpy(array, allow_missing=True)
    if np_array.ndim == 1:
        return {name: np_array}
    return {f"{name}{i}": np_array[:, i] for i in range(np_array.shape[1])}


def build_child_table(tree, branches, meta_columns):
    """One row per item in a jagged per-event collection (e.g. hits or
    truth particles), left-joined with per-event metadata. Events with zero
    items still get exactly one row with all item columns null, so the
    table's distinct `entry` values always match the source event list."""
    arrays = {name: read_branch(tree, path) for name, path in branches.items()}
    padded = {
        name: ak.pad_none(arr, 1, axis=1, clip=False) for name, arr in arrays.items()
    }
    counts = ak.to_numpy(ak.num(next(iter(padded.values())), axis=1))

    columns = {name: np.repeat(col, counts) for name, col in meta_columns.items()}
    for name, arr in padded.items():
        columns.update(to_columns(name, ak.flatten(arr, axis=1)))
    return columns


def read_mc_truth_event_columns(tree):
    """Per-event MC interaction truth (the mc.* group), one value per event
    (null if the event has no truth record). Raises if a file ever has more
    than one truth record for a single event (unsupported/ambiguous)."""
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

    columns = {}
    for name, path in MC_TRUTH_SCALAR_BRANCHES.items():
        first = ak.firsts(read_branch(tree, path), axis=1)
        columns.update(to_columns(name, first))
    for name, path in MC_TRUTH_P4_BRANCHES.items():
        first = ak.firsts(read_branch(tree, path), axis=1)
        columns.update(to_columns(name, first))
    return columns


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

        output_dir.mkdir(parents=True, exist_ok=True)

        data_columns = build_child_table(tree, HIT_BRANCHES, event_ids)
        data_table = pa.Table.from_pydict(data_columns)
        pq.write_table(data_table, output_dir / "data.parquet")
        print(f"  wrote data.parquet ({data_table.num_rows} rows)")

        if expect_mc:
            truth_event_columns = read_mc_truth_event_columns(tree)
            truth_meta = {**event_ids, **truth_event_columns}
            truth_columns = build_child_table(tree, PARTICLE_BRANCHES, truth_meta)
            truth_table = pa.Table.from_pydict(truth_columns)
            pq.write_table(truth_table, output_dir / "truth.parquet")
            print(f"  wrote truth.parquet ({truth_table.num_rows} rows)")


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

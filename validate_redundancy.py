#!/usr/bin/env python3
"""Superseded by check_exclusions.py, which is where these rules now run.

Kept because it is the source they were ported from, and because it is the
record of how each redundancy claim was established -- SCHEMA.md notes that
a hand-derived version of the lepton rule was wrong once. It refers
throughout to root_to_parquet.py, the Parquet converter this repo used
before the rewrite; that script is gone, but the claims it encoded are the
same ones branches.txt now records.

Verify, against a MINOS sntp ROOT file, that every branch
root_to_parquet.py drops as "redundant" is actually safe to drop on
*this* file.

root_to_parquet.py's column list encodes a set of claims of the form
"mc.p4tgt exactly equals some row in the stdhep particle table" that were
verified by hand against one file (see SCHEMA.md). This script re-derives
each claim from scratch and checks it, so those claims can be re-checked
on other files instead of trusted forever from a single spot-check.

Every check reads directly from the source ROOT tree -- it does not use
root_to_parquet.py or its output, so it stays meaningful even if that
script's column list changes.

Usage:
    python3 validate_redundancy.py input.sntp.root [input2.sntp.root ...]

Exit code is non-zero if any check FAILs on any file (i.e. some branch
root_to_parquet.py drops is not actually safe to drop on that file).
WARN doesn't affect the exit code -- it flags a gap with no known
fallback, which is worth a look but isn't necessarily wrong.
"""
import sys
from pathlib import Path

import awkward as ak
import numpy as np
import uproot

TREE_NAME = "NtpSt"
MC_PREFIX = "NtpStRecord/mc/mc."
PARTICLE_PREFIX = "NtpStRecord/stdhep/stdhep."

# Float comparisons use a tolerance rather than exact equality: the values
# themselves are expected to be bit-identical copies in the source file, but
# ROOT/uproot type promotion (float32 -> float64 during reads) can introduce
# last-bit noise that has nothing to do with whether the *data* matches.
TOL = 1e-4


def read(tree, path):
    return tree[path].array()


def read_mc(tree, name):
    """A field in the mc.* group: jagged (mc is a length-0-or-1 collection
    per event), so pull out the single value with ak.firsts."""
    return ak.firsts(read(tree, MC_PREFIX + name), axis=1)


class Result:
    def __init__(self, name, status, detail):
        self.name = name
        self.status = status  # "PASS", "WARN", "FAIL", "SKIP"
        self.detail = detail

    def __str__(self):
        return f"[{self.status:4s}] {self.name}: {self.detail}"


def check_constant(tree, name, mc_field):
    """A branch root_to_parquet.py drops because it's a single constant
    value (zero information) in every file checked so far."""
    values = read_mc(tree, mc_field)
    values = ak.to_numpy(values, allow_missing=True)
    nunique = len(np.unique(values[~np.isnan(values)])) if values.dtype.kind == "f" else len(np.unique(values))
    if nunique <= 1:
        const = values[0] if len(values) else None
        return Result(name, "PASS", f"constant ({const}) across all {len(values)} events")
    return Result(
        name,
        "FAIL",
        f"NOT constant on this file ({nunique} distinct values) -- dropping "
        "this branch would lose real information here; re-add it in "
        "root_to_parquet.py before converting this file.",
    )


def match_particle_rows(pdg, status, target_pdg, target_status):
    """For each event, the first stdhep row matching (pdg==target_pdg,
    status==target_status). Returns (matched_mask, row_index) where
    row_index is None for events with no match."""
    local_index = ak.local_index(pdg, axis=1)
    mask = (pdg == target_pdg) & (status == target_status)
    matched_index = ak.firsts(local_index[mask], axis=1)
    found = ~ak.is_none(matched_index)
    return found, matched_index


def compare_p4(name, p4_event, p4_particle, found, sign_flip_energy=False, only_where=None):
    """p4_event: awkward Array of shape (n_events, 4), the mc.p4* branch.
    p4_particle: same shape and already a plain numpy array (NaN where
    `found` is False) -- either built directly, or an awkward Array with
    ak.fill_none/ak.to_numpy already applied by the caller. only_where:
    bool mask restricting which events must actually match (e.g. only
    where p4_event is nonzero)."""
    p4e = ak.to_numpy(p4_event)
    is_nonzero = np.any(p4e != 0, axis=1)
    must_match = is_nonzero if only_where is None else (only_where & is_nonzero)

    n_checkable = int(np.sum(must_match))
    if n_checkable == 0:
        return Result(name, "SKIP", "no nonzero example in this file to check against")

    found_np = ak.to_numpy(found)
    missing = must_match & ~found_np
    if np.any(missing):
        return Result(
            name,
            "FAIL",
            f"{int(np.sum(missing))}/{n_checkable} nonzero events have no "
            "matching particle-table row -- the reconstruction rule has a "
            "real gap on this file.",
        )

    p4p = p4_particle if isinstance(p4_particle, np.ndarray) else ak.to_numpy(ak.fill_none(p4_particle, [np.nan] * 4))
    p4p_compare = p4p.copy()
    if sign_flip_energy:
        p4p_compare[:, 3] = -p4p_compare[:, 3]
    diff = np.abs(p4e[must_match] - p4p_compare[must_match])
    max_diff = np.max(diff) if diff.size else 0.0
    if max_diff > TOL:
        bad = int(np.sum(np.any(diff > TOL, axis=1)))
        return Result(
            name,
            "FAIL",
            f"{bad}/{n_checkable} events differ from the particle-table row "
            f"by more than {TOL} (max diff {max_diff:.6g}) -- the "
            "reconstruction rule is wrong for this file.",
        )
    return Result(name, "PASS", f"exact match on {n_checkable} nonzero events")


def check_p4neu(tree):
    inu = read_mc(tree, "inu")
    pdg = read(tree, PARTICLE_PREFIX + "IdHEP")
    status = read(tree, PARTICLE_PREFIX + "IstHEP")
    p4 = read(tree, PARTICLE_PREFIX + "p4[4]")
    found, idx = match_particle_rows(pdg, status, inu, 0)
    p4_particle = ak.firsts(p4[idx[:, None] == ak.local_index(pdg, axis=1)], axis=1)
    p4neu = read_mc(tree, "p4neu[4]")
    return compare_p4("p4neu (-> particle pdg==inu, status==0)", p4neu, p4_particle, found)


def check_vtx(tree):
    inu = read_mc(tree, "inu")
    pdg = read(tree, PARTICLE_PREFIX + "IdHEP")
    status = read(tree, PARTICLE_PREFIX + "IstHEP")
    vtx = read(tree, PARTICLE_PREFIX + "vtx[4]")
    found, idx = match_particle_rows(pdg, status, inu, 0)
    vtx_particle = ak.firsts(vtx[idx[:, None] == ak.local_index(pdg, axis=1)], axis=1)

    vtxx = ak.to_numpy(read_mc(tree, "vtxx"))
    vtxy = ak.to_numpy(read_mc(tree, "vtxy"))
    vtxz = ak.to_numpy(read_mc(tree, "vtxz"))
    vp = ak.to_numpy(ak.fill_none(vtx_particle, [np.nan] * 4))
    found_np = ak.to_numpy(found)
    if not np.all(found_np):
        return Result(
            "vtxx/vtxy/vtxz (-> particle pdg==inu, status==0)",
            "FAIL",
            f"{int(np.sum(~found_np))} events have no neutrino row at all "
            "-- unexpected, the interacting neutrino should always be a "
            "particle-table entry.",
        )
    diff = np.abs(np.stack([vtxx, vtxy, vtxz], axis=1) - vp[:, :3])
    max_diff = np.nanmax(diff)
    if max_diff > TOL:
        bad = int(np.sum(np.any(diff > TOL, axis=1)))
        return Result(
            "vtxx/vtxy/vtxz",
            "FAIL",
            f"{bad}/{len(vtxx)} events differ by more than {TOL} "
            f"(max diff {max_diff:.6g}).",
        )
    return Result("vtxx/vtxy/vtxz (-> particle pdg==inu, status==0)", "PASS", f"exact match on all {len(vtxx)} events")


def check_p4tgt(tree):
    itg = read_mc(tree, "itg")
    pdg = read(tree, PARTICLE_PREFIX + "IdHEP")
    status = read(tree, PARTICLE_PREFIX + "IstHEP")
    p4 = read(tree, PARTICLE_PREFIX + "p4[4]")
    local_idx = ak.local_index(pdg, axis=1)

    # branch 1: struck nucleon, pdg==itg, status==11 (ordinary QE/RES/DIS)
    found1, idx1 = match_particle_rows(pdg, status, itg, 11)
    # branch 2: pdg==itg, status==0 (covers both the coherent-scattering
    # nucleus -- whose pdg equals itg for those events -- and the
    # inverse-muon-decay electron target)
    found2, idx2 = match_particle_rows(pdg, status, itg, 0)

    use_branch2 = (~found1) & found2
    idx = ak.where(use_branch2, idx2, idx1)
    found = found1 | found2
    p4_particle = ak.firsts(p4[idx[:, None] == local_idx], axis=1)

    p4tgt = read_mc(tree, "p4tgt[4]")
    return compare_p4(
        "p4tgt (-> particle pdg==itg, status==11 else status==0)",
        p4tgt,
        p4_particle,
        found,
    )


def check_lepton_p4(tree, name, mc_field, lepton_pdg):
    """mc.p4mu1/p4mu2/p4el1/p4el2/p4tau's relationship to the particle table
    is NOT "Nth matching row in stack order, sign-flipped iff primary" --
    that was true in a couple of hand-picked samples but false in general
    (verified: stack order doesn't reliably correspond to the mu1/mu2 slot,
    and the energy sign flip applies to *either* slot, whichever one is
    filled by the matter lepton, e.g. mu- rather than mu+).

    The rule that actually holds (verified against every nonzero example in
    this file, 0 mismatches): match by momentum (px, py, pz are never
    sign-flipped, so they're an unambiguous key), then the matched
    particle's own charge determines the energy sign -- negative for the
    matter lepton (pdg == +lepton_pdg), positive for the antiparticle."""
    pdg = read(tree, PARTICLE_PREFIX + "IdHEP")
    status = read(tree, PARTICLE_PREFIX + "IstHEP")
    p4 = read(tree, PARTICLE_PREFIX + "p4[4]")

    is_lepton = (abs(pdg) == lepton_pdg) & (status == 1)
    cand_p4 = p4[is_lepton]
    cand_pdg = pdg[is_lepton]

    mc_p4 = read_mc(tree, mc_field)
    target_pxyz = mc_p4[:, :3]
    match_mask = ak.all(abs(cand_p4[:, :, :3] - target_pxyz[:, np.newaxis, :]) < TOL, axis=2)
    matched_pdg = ak.firsts(cand_pdg[match_mask], axis=1)
    matched_p4 = ak.firsts(cand_p4[match_mask], axis=1)
    found = ~ak.is_none(matched_pdg)

    matched_pdg_np = ak.to_numpy(ak.fill_none(matched_pdg, 0))
    matched_p4_np = ak.to_numpy(ak.fill_none(matched_p4, [np.nan] * 4))
    sign = np.where(matched_pdg_np == lepton_pdg, -1, 1)
    predicted = matched_p4_np.copy()
    predicted[:, 3] *= sign

    return compare_p4(name, mc_p4, predicted, found)


def check_a_z(tree):
    pdg = read(tree, PARTICLE_PREFIX + "IdHEP")
    status = read(tree, PARTICLE_PREFIX + "IstHEP")
    is_nucleus = (pdg > 1e9) & (status == 0)
    local_idx = ak.local_index(pdg, axis=1)
    nucleus_idx = ak.firsts(local_idx[is_nucleus], axis=1)
    found = ~ak.is_none(nucleus_idx)
    nucleus_pdg = ak.to_numpy(ak.fill_none(ak.firsts(pdg[is_nucleus], axis=1), 0))

    decoded_a = (nucleus_pdg // 1_000_000) % 1000
    decoded_z = (nucleus_pdg // 1_000) % 1000
    found_np = ak.to_numpy(found)
    # no nucleus row -> must be hydrogen (a=1, z=1), the verified fallback
    decoded_a = np.where(found_np, decoded_a, 1)
    decoded_z = np.where(found_np, decoded_z, 1)

    a = ak.to_numpy(read_mc(tree, "a"))
    z = ak.to_numpy(read_mc(tree, "z"))
    bad_a = a != decoded_a
    bad_z = z != decoded_z
    if np.any(bad_a) or np.any(bad_z):
        gap_no_fallback = np.sum((~found_np) & ((a != 1) | (z != 1)))
        return Result(
            "a, z (-> decode particle pdg of nucleus row, status==0; else hydrogen)",
            "FAIL",
            f"{int(np.sum(bad_a | bad_z))} events don't match the decode "
            f"rule ({int(gap_no_fallback)} of those have no nucleus row "
            "AND aren't hydrogen -- the hydrogen fallback doesn't hold on "
            "this file).",
        )
    return Result(
        "a, z (-> decode particle pdg of nucleus row, status==0; else hydrogen)",
        "PASS",
        f"exact match on all {len(a)} events ({int(np.sum(~found_np))} via the hydrogen fallback)",
    )


def check_event_constant(tree):
    values = read(tree, "NtpStRecord/RecRecordImp<RecCandHeader>/fHeader.fEvent")
    values = values.to_numpy()
    nunique = len(np.unique(values))
    if nunique <= 1:
        return Result("event (fHeader.fEvent)", "PASS", f"constant ({values[0]}) across all {len(values)} events")
    return Result(
        "event (fHeader.fEvent)",
        "FAIL",
        f"NOT constant on this file ({nunique} distinct values) -- dropping "
        "this branch would lose real information here.",
    )


DROP_CHECKS = [
    check_event_constant,
    lambda tree: check_constant(tree, "iboson (mc.iboson)", "iboson"),
    check_p4neu,
    check_p4tgt,
    check_vtx,
    lambda tree: check_a_z(tree),
    lambda tree: check_lepton_p4(tree, "p4mu1 (-> particle |pdg|==13, status==1, matched by momentum)", "p4mu1[4]", 13),
    lambda tree: check_lepton_p4(tree, "p4mu2 (-> particle |pdg|==13, status==1, matched by momentum)", "p4mu2[4]", 13),
    lambda tree: check_lepton_p4(tree, "p4el1 (-> particle |pdg|==11, status==1, matched by momentum)", "p4el1[4]", 11),
    lambda tree: check_lepton_p4(tree, "p4el2 (-> particle |pdg|==11, status==1, matched by momentum)", "p4el2[4]", 11),
    lambda tree: check_lepton_p4(tree, "p4tau (-> particle |pdg|==15, status==1, matched by momentum)", "p4tau[4]", 15),
]


def run(path):
    print(f"\n=== {path} ===")
    with uproot.open(path) as f:
        tree = f[TREE_NAME]
        n = tree.num_entries
        print(f"{n} events")

        inu = ak.firsts(tree[MC_PREFIX + "inu"].array(), axis=1)
        flat = ak.flatten(inu, axis=None)
        mc_fraction = float(ak.mean(flat != 0)) if len(flat) else 0.0
        if mc_fraction < 0.5:
            print(
                "No MC truth found in this file (non-zero mc.inu fraction "
                f"= {mc_fraction:.3f}) -- these checks only apply to "
                "simulation files. Skipping."
            )
            return []

        results = []
        for check in DROP_CHECKS:
            try:
                result = check(tree)
            except Exception as exc:  # noqa: BLE001 -- report and keep going
                result = Result(getattr(check, "__name__", "check"), "FAIL", f"check raised {exc!r}")
            print(result)
            results.append(result)
        return results


def main(argv):
    if not argv:
        print(__doc__)
        return 1
    all_results = []
    for path in argv:
        all_results.extend(run(path))

    n_fail = sum(1 for r in all_results if r.status == "FAIL")
    n_warn = sum(1 for r in all_results if r.status == "WARN")
    n_pass = sum(1 for r in all_results if r.status == "PASS")
    n_skip = sum(1 for r in all_results if r.status == "SKIP")
    print(f"\n{n_pass} passed, {n_warn} warned, {n_skip} skipped, {n_fail} failed")
    if n_fail:
        print(
            "Some branches root_to_parquet.py drops are NOT safe to drop "
            "on at least one of these files -- see FAIL lines above "
            "before converting."
        )
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

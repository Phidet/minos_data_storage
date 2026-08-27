#!/usr/bin/env python3
"""Write and read the archive formats: HDF5 and ROOT.

Both hold the same thing -- one column per exported branch, jagged where the
source is jagged. HDF5 is the compact, dependency-light option; ROOT keeps
the data in the ecosystem it came from, which may be where it ends up.
"""

from __future__ import annotations

import json
from pathlib import Path

import awkward as ak
import h5py
import numpy as np
import uproot

TREE = "NtpSt"


# --------------------------------------------------------------------------
# HDF5
# --------------------------------------------------------------------------


def _write_h5_column(group, name: str, array, compression: dict) -> None:
    """Store one column as `values` + `offsets`, not as a vlen dataset.

    This is not a stylistic choice. HDF5 keeps variable-length data in the
    global heap and applies dataset filters only to the pointers, so a vlen
    dataset is effectively *uncompressed* whatever codec is requested --
    measured at 2% for gzip-9, against 39% for the same data and codec in
    this layout. Two fixed-width datasets compress normally.

    `inner_shape` records the trailing dimensions so a cell written as
    (n, 4) -- a per-event list of 4-vectors -- comes back that shape rather
    than as a flat run.
    """
    if array.ndim == 1:
        group.create_dataset(name=name, data=np.asarray(array), **compression)
        return

    counts = np.asarray(ak.num(array, axis=1), dtype=np.int64)
    flat = ak.flatten(array, axis=None)
    values = np.asarray(flat)

    # trailing dimensions, if the cells are themselves arrays
    inner: tuple[int, ...] = ()
    probe = array
    depth = 0
    while probe.ndim > 1:
        probe = ak.flatten(probe, axis=1)
        depth += 1
    if depth > 1:
        total_rows = int(counts.sum())
        if total_rows:
            inner = (len(values) // total_rows,)

    scale = int(np.prod(inner)) if inner else 1
    offsets = np.zeros(len(counts) + 1, dtype=np.int64)
    np.cumsum(counts * scale, out=offsets[1:])

    sub = group.create_group(name)
    sub.attrs["jagged"] = True
    sub.attrs["inner_shape"] = np.asarray(inner, dtype=np.int64)
    sub.create_dataset(name="values", data=values, **compression)
    sub.create_dataset(name="offsets", data=offsets, **compression)


def _read_h5_column(node):
    """Inverse of `_write_h5_column`."""
    if not isinstance(node, h5py.Group):
        return node[:]

    values = node["values"][:]
    offsets = node["offsets"][:]
    inner = tuple(int(d) for d in node.attrs.get("inner_shape", ()))
    scale = int(np.prod(inner)) if inner else 1

    out = np.empty(len(offsets) - 1, dtype=object)
    for i in range(len(offsets) - 1):
        segment = values[offsets[i]:offsets[i + 1]]
        out[i] = segment.reshape((-1,) + inner) if inner else segment
    return out


def write_hdf5(path: Path, columns: dict, metadata: dict, compression="gzip") -> None:
    """Write the columns to HDF5, gzip-compressed.

    gzip is fixed rather than offered as a choice. It is not only the best
    ratio measured here -- 58% on the test file -- but the one HDF5 filter
    guaranteed present in every build. `lzf` is an h5py extension, and a
    file written with it cannot be opened by plain HDF5 tooling without
    that filter plugin; for a format meant to outlive its tooling, that
    rules it out whatever it does for speed.

    The argument survives so a test can pass `None` and confirm the
    compression is doing something. Nothing else should pass it.
    """
    kwargs = {} if compression in (None, "none") else {"compression": compression}
    path.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(path, "w") as f:
        data = f.create_group("data")
        for name, array in columns.items():
            _write_h5_column(data, name, array, kwargs)
        f.create_dataset(
            "metadata",
            dtype=h5py.string_dtype(encoding="utf-8"),
            data=json.dumps(metadata).encode("utf-8"),
        )


def read_hdf5(path: Path) -> tuple[dict, dict]:
    with h5py.File(path, "r") as f:
        columns = {k: _read_h5_column(f["data"][k]) for k in f["data"].keys()}
        metadata = json.loads(f["metadata"][()].decode("utf-8"))
    return columns, metadata


# --------------------------------------------------------------------------
# ROOT
# --------------------------------------------------------------------------


def _root_safe(name: str) -> str:
    """A ROOT-safe branch name.

    uproot splits a dotted name into nested record fields on read, so a
    branch written as `calstatus.gevpermip` comes back only as a parent
    `calstatus` that cannot be indexed. Brackets fare no better. The
    original names are stored in the provenance so nothing is lost.
    """
    return name.replace(".", "_").replace("[", "_").replace("]", "")


def _root_encode(array):
    """Prepare one column for a TTree branch; return (array, inner_width).

    A TTree branch cannot express "a variable number of fixed-width rows",
    which is what a branch like `vetostp.z[2]` is. uproot will write the
    bytes, but reads them back with an interpretation that no longer
    reshapes, and the read fails outright. So the inner dimension is
    flattened here and its width recorded in the provenance, exactly as the
    HDF5 writer does with `inner_shape`.

    A column that is fixed-width per *event* (`mc.p4mu1[4]`) has no jagged
    axis and needs none of this -- it becomes a plain fixed-width branch.
    """
    if isinstance(array, np.ndarray):
        return array, 0

    array = ak.Array(array)
    if array.ndim <= 1:
        return array, 0
    if array.ndim == 2:
        try:
            return ak.to_numpy(array), 0  # regular: a fixed-width branch
        except Exception:
            return array, 0  # genuinely jagged

    widths = ak.flatten(ak.num(array, axis=2), axis=None)
    if len(widths) and len(np.unique(ak.to_numpy(widths))) > 1:
        raise ValueError("ragged inner dimension cannot be written to a TTree")
    width = int(widths[0]) if len(widths) else 0
    return ak.flatten(array, axis=2), width


def _root_decode(array, width: int):
    """Inverse of `_root_encode`: restore the flattened inner dimension."""
    if width <= 1:
        return array
    counts = ak.num(array, axis=1) // width
    return ak.unflatten(ak.unflatten(ak.flatten(array), width), counts)


def write_root(path: Path, columns: dict, metadata: dict, compression="zlib") -> None:
    """Write the columns as a ROOT TTree, zlib-compressed.

    Uses `mktree` rather than `f[TREE] = {...}`: the latter produces an
    RNTuple, which only ROOT 6.28 and later can read. A TTree is readable by
    anything, which is what matters if this is the format the data ends up
    kept in.

    `compression` takes "zlib" or None only. It used to accept anything and
    quietly write zlib regardless, which made the setting a lie.
    """
    if compression not in ("zlib", None, "none"):
        raise ValueError(
            f"write_root supports 'zlib' or None, not {compression!r}. "
            "ROOT output is zlib so that any ROOT version can read it."
        )
    path.parent.mkdir(parents=True, exist_ok=True)

    encoded, widths = {}, {}
    for name, array in columns.items():
        safe = _root_safe(name)
        if safe in encoded:
            raise ValueError(f"branch names collide once made ROOT-safe: {name}")
        encoded[safe], widths[safe] = _root_encode(array)

    def branch_type(a):
        if not isinstance(a, np.ndarray):
            return ak.type(a)
        # a fixed-width column must declare its width, or `extend` refuses
        # to fill a scalar branch with (n, k) data
        return a.dtype if a.ndim == 1 else np.dtype((a.dtype, a.shape[1:]))

    types = {name: branch_type(a) for name, a in encoded.items()}

    provenance = dict(metadata)
    provenance["branch_names"] = {_root_safe(k): k for k in columns}
    provenance["inner_widths"] = {k: w for k, w in widths.items() if w > 1}

    settings = {} if compression in (None, "none") else {"compression": uproot.ZLIB(4)}
    with uproot.recreate(path, **settings) as f:
        # the provenance rides in the tree title rather than a separate key:
        # it stays attached to the data, and a plain `f[name] = str` written
        # alongside a tree did not reliably survive the close
        f.mktree(TREE, types, title=json.dumps(provenance))
        f[TREE].extend(encoded)


def read_root(path: Path) -> tuple[dict, dict]:
    """Read back a file written by `write_root`, restoring names and shapes."""
    with uproot.open(path) as f:
        tree = f[TREE]

        metadata = {}
        if tree.title.startswith("{"):
            metadata = json.loads(tree.title)
        names = metadata.get("branch_names", {})
        widths = metadata.get("inner_widths", {})

        # uproot adds a counter branch per jagged branch; the mapping names
        # exactly what was written, so use it to leave those behind
        keys = [k for k in tree.keys() if k in names] if names else tree.keys()
        columns = {
            names[k] if names else k: _root_decode(tree[k].array(), widths.get(k, 0))
            for k in keys
        }
    return columns, metadata


WRITERS = {"hdf5": write_hdf5, "root": write_root}
READERS = {"hdf5": read_hdf5, "root": read_root}
SUFFIX = {"hdf5": ".h5", "root": ".root"}

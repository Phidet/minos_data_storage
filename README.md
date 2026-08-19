# minos_data_storage

Tools for converting MINOS `.sntp.root` ntuples into Parquet tables that
don't require ROOT, `uproot`, or any MINOS-specific software to read —
just pandas (or polars/DuckDB/Spark/R/...).

Only two tiers of the original ntuple are kept:

- **MC truth** — the true neutrino interaction and final-state particles
  (simulation files only).
- **Raw digitized data** — the strip-level hits, i.e. the calorimeter's
  measured response before any track/shower fitting.

Everything produced by the MINOS reconstruction chain (fitted tracks,
showers, vertices, slices, fiducial/containment flags, ...) is dropped.

## Setup

Dependencies are managed with [uv](https://docs.astral.sh/uv/):

```bash
uv sync
```

This creates `.venv/` and installs `uproot`, `awkward`, `numpy`, and
`pyarrow` as pinned in `uv.lock`.

## Converting a file

```bash
# simulation: requires MC truth to be present, errors out otherwise
uv run root_to_parquet.py --mc input.sntp.root output_dir/

# real data: requires MC truth to be absent, errors out otherwise
uv run root_to_parquet.py --data input.sntp.root output_dir/
```

The `--mc`/`--data` flag is a correctness check, not just a label: the
script inspects the MC truth branches in the file and refuses to run if
they don't match what you asked for (e.g. running `--data` on a
simulation file, or `--mc` on a real-data file).

## Output tables

Two flat, denormalized tables — no nested/list columns, so any tool that
reads Parquet can use them directly:

| file             | one row per...              | written for | 
|------------------|------------------------------|-------------|
| `data.parquet`   | digitized strip hit          | mc + data   |
| `truth.parquet`  | MC final-state truth particle| mc only     |

Both carry the event identifiers (`entry`, `run`, `subrun`, `snarl`,
`event`) on every row — `entry` is the row index in the source `NtpSt`
tree and is the key to group/filter by. `truth.parquet` additionally
repeats the event's MC interaction truth (neutrino kinematics, vertex,
interaction code, ...) on every one of that event's particle rows, so it's
a single self-contained table rather than something you need to join.

Both tables are a *left join*: an event with zero hits (or, for truth,
zero truth particles) still gets exactly one row, with the item-level
columns null. That means `data["entry"].nunique()` always equals the
number of events in the source file — no event silently disappears just
because it happened to have no hits.

`data.parquet` is intentionally a "long" (sparse/COO-style) table rather
than a dense per-plane image: strip occupancy is typically well under
0.1% of the full plane×strip grid, so a dense tensor would be orders of
magnitude larger on disk than the source file. Pivot into a dense or
`scipy.sparse`/`torch.sparse` tensor per event at load time if that's
the representation you need (e.g. for a CNN) — see the notebook example
below.

Note on naming: in `truth.parquet`, `bjorken_x`/`bjorken_y`/`bjorken_z`
are the standard DIS kinematic variables, *not* a spatial position —
the interaction vertex position is `vtxx`/`vtxy`/`vtxz`.

## Loading in pandas

```python
import pandas as pd

data = pd.read_parquet("output_dir/data.parquet")

# hits for a single event
event_hits = data[data["entry"] == 0]

# simulation only
truth = pd.read_parquet("output_dir/truth.parquet")
event_truth = truth[truth["entry"] == 0]

# e.g. rebuild a dense (plane, strip) image for one event, per view
import numpy as np

ev = event_hits[event_hits["view"] == 2]  # U view
image = np.zeros((486, 192), dtype=np.float32)
image[ev["plane"].astype(int), ev["strip"].astype(int)] = ev["pe0"] + ev["pe1"]
```

`groupby("entry")` iterates event-by-event over either table. Columns
that hold small whole numbers (`plane`, `strip`, `view`, ...) read back
as `float64` in plain pandas because they contain nulls (for zero-hit/
zero-particle events) — pandas' default integer dtype can't hold nulls.
Pass `dtype_backend="pyarrow"` to `read_parquet` if you want the exact
nullable integer types instead.

`inspect_parquet.ipynb` (generated locally, not checked into the repo —
see below) has a runnable walkthrough of both tables, including the
dense-image pivot above.

## Inspecting the output

A Jupyter notebook, `inspect_parquet.ipynb`, is a handy way to poke at the
converted tables (row counts, hit-multiplicity histograms, a rendered
event display, the truth energy spectrum, ...). Notebooks and `.parquet`
files are gitignored — they're local scratch/derived artifacts, not
tracked in the repo — so create it once for yourself and keep iterating
on it locally. `uv sync` installs `jupyter`/`pandas`/`matplotlib` so it's
ready to run:

```bash
uv run jupyter lab
```

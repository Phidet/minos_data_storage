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

All tables are keyed by an integer `event_id` (== row index in the
source `NtpSt` tree, called `entry` in `events.parquet`), so they can be
joined across files.

| file                     | one row per...            | written for | key column(s)           |
|--------------------------|----------------------------|-------------|--------------------------|
| `events.parquet`         | event                      | mc + data   | `entry` (+ `run`/`subrun`/`snarl`/`event`) |
| `hits.parquet`           | digitized strip hit        | mc + data   | `event_id`               |
| `truth_event.parquet`    | event's MC interaction     | mc only     | `event_id`               |
| `truth_particles.parquet`| MC final-state particle    | mc only     | `event_id`               |

`hits.parquet` is intentionally a "long" (sparse/COO-style) table rather
than a dense per-plane image: strip occupancy is typically well under
0.1% of the full plane×strip grid, so a dense tensor would be orders of
magnitude larger on disk than the source file. Pivot into a dense or
`scipy.sparse`/`torch.sparse` tensor per event at load time if that's
the representation you need (e.g. for a CNN).

## Loading in pandas

```python
import pandas as pd

events = pd.read_parquet("output_dir/events.parquet")
hits = pd.read_parquet("output_dir/hits.parquet")

# hits for a single event
event_hits = hits[hits["event_id"] == 0]

# join truth onto events (simulation only)
truth_event = pd.read_parquet("output_dir/truth_event.parquet")
truth_particles = pd.read_parquet("output_dir/truth_particles.parquet")

events_with_truth = events.merge(
    truth_event, left_on="entry", right_on="event_id"
)

# e.g. rebuild a dense (plane, strip) image for one event, per view
import numpy as np

ev = event_hits[event_hits["view"] == 2]  # U view
image = np.zeros((486, 192), dtype=np.float32)
image[ev["plane"], ev["strip"]] = ev["pe0"] + ev["pe1"]
```

Everything else (`hits`, `truth_particles`) works the same way: filter
by `event_id`, or `groupby("event_id")` to iterate event-by-event.

# minos_data_storage

Converts MINOS `.sntp.root` ntuples into Parquet, readable without ROOT
or `uproot`. Keeps raw digitized hits and MC truth; drops everything
from the MINOS reconstruction chain (tracks, showers, vertices, slices,
fiducial flags, ...).

## Setup

```bash
uv sync
```

## Usage

```bash
uv run root_to_parquet.py --mc   input.sntp.root output_dir/  # simulation
uv run root_to_parquet.py --data input.sntp.root output_dir/  # real data
```

`--mc`/`--data` is checked against the file's actual MC truth content,
not just taken as a label — it errors out on a mismatch.

## Output

| file             | one row per...                 | written for |
|------------------|---------------------------------|-------------|
| `data.parquet`   | digitized strip hit             | mc + data   |
| `truth.parquet`  | MC final-state truth particle, with its event's interaction truth attached | mc only |

Both carry `entry`/`run`/`subrun`/`snarl`/`event` on every row. Events
with zero hits (or zero truth particles) still get one row with those
columns null, so `data["entry"].nunique()` always equals the event count.

`bjorken_x/y/z` in `truth.parquet` are DIS kinematic variables, not a
position — the vertex position is `vtxx`/`vtxy`/`vtxz`.

## Loading in pandas

```python
import pandas as pd
import numpy as np

data = pd.read_parquet("output_dir/data.parquet", dtype_backend="pyarrow")
event_hits = data[data["entry"] == 0]

# rebuild a dense (plane, strip) image for one event's U view
ev = event_hits[(event_hits["view"] == 2) & event_hits["plane"].notna()]
image = np.zeros((486, 192), dtype=np.float32)
image[ev["plane"], ev["strip"]] = ev["pe0"] + ev["pe1"]
```

`dtype_backend="pyarrow"` keeps `plane`/`strip`/`view` as proper nullable
integers; without it, pandas upcasts them to `float64` because of the
nulls from zero-hit events.

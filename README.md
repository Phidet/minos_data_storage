# minos_data_storage

Converts MINOS `.sntp.root` ntuples into an archival format — HDF5 or ROOT —
for long-term storage.

The aim is to preserve **what the detector and the simulation recorded**, so
that a future analysis can start from the data rather than inherit MINOS's
own conclusions. Everything the reconstruction chain produced from that data
is dropped: tracks, showers, slices, clusters, and reconstructed events
including the vertex.

MINOS has finished and no new simulations are coming, so anything not
archived here is gone for good. Where a branch is a borderline call, it is
kept.

## Setup

```bash
uv sync
```

## Usage

```bash
# one file
uv run python export.py input.sntp.root out/

# a whole tree, mirrored
uv run python export.py /data/sntp /archive/hdf5

# ROOT instead of HDF5
uv run python export.py /data/sntp /archive/root -f root
```

The input structure is preserved, with only the trailing `.root` replaced:

```
/data/sntp/2010/run1/f21….sntp.dogwood5.0.root
  ->  /archive/hdf5/2010/run1/f21….sntp.dogwood5.0.h5
```

Stripping the whole `.sntp.dogwood5.0` tail would read better, but two
inputs differing only in those middle components would then collide on one
output name.

| Flag | |
|------|--|
| `-f, --format {hdf5,root}` | output format (default `hdf5`) |
| `--pattern GLOB` | which files to pick up (default `**/*.root`) |
| `--manifest FILE` | a manifest other than `branches.txt` |
| `--max-events N` | keep only the first N events per file (testing) |
| `--overwrite` | reconvert files whose output already exists |
| `--no-check` | skip the exclusion checks described below |
| `--check-events N` | events to sample for those checks (default 5000) |

Compression is not an option: HDF5 output is gzip, ROOT output is zlib.
Both are the codec their format guarantees every reader can decompress,
which is the property that matters for an archive.

## What gets archived: `branches.txt`

Every branch in the `NtpSt` tree has a line in
[`branches.txt`](branches.txt). A line that is not commented out is
exported; a leading `#` excludes it, and the note after it says why.

```
# ── stp — digitised strip hits ─────────────────────────────
NtpStRecord/stp/stp.plane
NtpStRecord/stp/stp.strip
NtpStRecord/stp/stp.planeview     # fixed by plane (2=U, 3=V)
#NtpStRecord/stp/stp.z            # fixed by plane; one z per plane
#NtpStRecord/stp/stp.index        # array position, carried by row order
```

**Changing what the archive contains is an edit to that file, not a code
change.** 171 of 755 branches are enabled by default. If a file turns up
carrying a branch the manifest does not mention, `export.py` names it and
carries on — add it here to archive it.

`branches.py` parses it, and checks the count in the header comment against
the number actually enabled, so the two cannot drift apart.

**[BRANCHES.md](BRANCHES.md) is the reference**: all 755 branches, grouped
as the file groups them, each with its meaning and a ✓/✗ saying whether it
is archived. It is generated from the manifest by `make_branches_doc.py`, so
the ticks cannot drift out of step — regenerate it after editing
`branches.txt`:

```bash
python make_branches_doc.py
```

CI regenerates it too and fails if the committed copy differs, so a manifest
edit without the matching doc is caught on push.

## Output formats

Both hold one column per enabled branch, and round-trip every one exactly —
dtype, shape and values — checked against the source rather than assumed. On
the test file (119,205 events, 171 branches) 2.0 GB of SNTP becomes 578 MB
of HDF5 or 577 MB of ROOT, so the choice is about what will read them rather
than about size.

**HDF5** (`.h5`) — compact and dependency-light. Jagged columns are stored
as `values` + `offsets` rather than as variable-length datasets, which HDF5
would leave effectively uncompressed whatever codec was asked for; the
comment in [`formats.py`](formats.py) has the measurements.

**ROOT** (`.root`) — a plain `TTree`, not the RNTuple uproot writes by
default (only ROOT 6.28 and later read those). Two things to know if you
open one directly rather than through `read_root`:

- Branch names are made ROOT-safe: `mc.p4mu1[4]` is stored as `mc_p4mu1_4`.
  The originals are in the tree title as JSON, and `read_root` restores them.
- A jagged run of fixed-width rows (`vetostp.z[2]`, one pair per shield hit)
  cannot be a single TTree branch, so it is stored flat with its width in
  that same title.

## Reading it back

```python
import formats

columns, metadata = formats.read_hdf5("archive/f21….h5")   # or read_root
columns["stp.plane"]     # jagged, one entry per event
metadata["events"], metadata["source"]
```

Column names drop the `NtpStRecord/<group>/` prefix, so `stp.plane` rather
than the full key. Every file carries its provenance: source path and size,
event and branch counts, and when it was written.

The output also says what it *is*, without reference to its filename:
`fHeader.fVldContext.fDetector` (`1` Near, `2` Far), `.fSimFlag` (`1` data,
`4` Monte Carlo) and, for simulation, `mchdr.geninfo.codename` — the MC
release such as `daikon_07`. A filename is not a durable record.

## Behaviour worth knowing

**One process per file.** A single file needs roughly 1.2 GB resident, and
Python does not reliably hand that back between iterations, so a long
in-process loop climbs until it is killed. Each conversion runs in its own
subprocess: a couple of seconds of startup against a conversion measured in
minutes, and memory stays flat across a run of any length.

**Resumable.** Files whose output already exists are skipped unless
`--overwrite`, so an interrupted run can simply be repeated.

**One bad file does not stop the batch.** Failures are collected, printed at
the end with the tail of their error, and the exit status is non-zero.

**The output can never land on the input.** ROOT-to-ROOT keeps the `.root`
suffix, so a careless output directory would otherwise overwrite the source
— and with `--overwrite` it did, once. Two guards now refuse it.

**Real data vs simulation.** The default set includes `mc.*`. `NtpStRecord`
declares those arrays whatever the file holds, so a real-data file should
carry the branches empty rather than absent — that is read off the class
definition, not yet confirmed against a data file. If a branch is genuinely
missing the file is reported as failed, naming the branch, rather than
guessing.

**A branch the manifest has never heard of is reported, not ignored.** The
checks above run manifest-to-file; this is the other direction. A file whose
branch set differs from what `branches.txt` describes would otherwise be
quietly stripped of the difference, so those branches are named under the
file's line and the conversion continues — it is news, not an error.

## Checks on excluded branches

Some branches are dropped because of a *claim about their contents*: that
they are empty, a constant sentinel, or an exact copy of something kept.
Before converting, each file is tested against those claims
([`check_exclusions.py`](check_exclusions.py)), and refused if any fails.

Without that, a file where `digihit` was actually populated, or where
`mc.p4neu` did not match the truth particle table, would be silently
stripped of real data.

```bash
uv run python check_exclusions.py input.sntp.root
```

Branches dropped *by policy* — the whole reconstruction chain — are not
checked: there is no claim to test.

---

## Files

| | |
|--|--|
| [`branches.txt`](branches.txt) | the manifest — every branch, grouped, with reasons |
| [`branches.py`](branches.py) | parses it |
| [`BRANCHES.md`](BRANCHES.md) | all 755 branches, what each means, and whether it is archived |
| [`make_branches_doc.py`](make_branches_doc.py) | regenerates BRANCHES.md from the manifest |
| [`formats.py`](formats.py) | HDF5 and ROOT writers and readers |
| [`export.py`](export.py) | the CLI |
| [`check_exclusions.py`](check_exclusions.py) | verifies the assumptions behind dropped branches |

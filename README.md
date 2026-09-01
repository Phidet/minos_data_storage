# minos_data_storage

Converts MINOS `.sntp.root` ntuples into HDF5 or ROOT for long-term archival.

It keeps what the detector and the simulation recorded — 177 of the 755
branches — and drops everything the reconstruction chain produced from that:
tracks, showers, slices, clusters, and reconstructed events including the
vertex. MINOS has finished, so anything not archived is gone for good; where
a branch is a borderline call, it is kept.

Two entry points. [`export.py`](export.py) converts files already on local
disk. [`pipeline.py`](pipeline.py) runs at Fermilab: stages from tape and
converts next to it, writing the results into your MINOS data area. Moving
them onward is a separate bulk transfer — the conversion is what makes the
files small (an SNTP data file comes out around 3% of its input), so there
is no reason to move the big version anywhere.

## Quickstart on a gpvm

```bash
git clone https://github.com/Phidet/minos_data_storage.git
cd minos_data_storage
./setup.sh                        # venv + the four packages it imports
source .venv/bin/activate

ARCHIVE=/exp/minos/data/users/$USER/archive

# Point it straight at a /pnfs directory. Listing one is metadata only --
# it does not touch tape. A text file of paths works too, for a subset.
python pipeline.py /pnfs/minos/reco_far/elm7/sntp_data/2016-06 $ARCHIVE --dry-run

# Run it. This takes hours to days, so run it detached.
tmux new -s minos
python pipeline.py /pnfs/minos/reco_far/elm7/sntp_data/2016-06 $ARCHIVE
```

**If it stops, rerun the same command** — progress is in a ledger beside the
output, so it resumes and reconverts nothing. Add `--retry-failed` to requeue
whatever failed; tape errors are often transient. The ledger sits with the
output rather than the staging area, so wiping scratch costs nothing but the
files in flight.

A run prints three counters (staged, fetched, converted), live on a terminal
and as plain lines in a log.

**Getting it to UCL** is a separate step, once conversion is done and
verified: Globus, or an rsync pull initiated from the far end. Keeping it
separate means a days-long tape job does not depend on the network, and no
credentials sit on a shared machine.

### The two settings that matter

| | |
|--|--|
| `--prestage-ahead N` | prestage requests in flight, default 500. **This is the tape knob.** With many outstanding, dCache orders them by tape volume and mounts each tape once; one at a time sends the robot back to the same tape repeatedly. Costs no local disk, so be generous |
| `--scratch-budget GB` | cap on the *staging* area, default 300. Gates fetching only, never staging requests, and does not limit the output. Lower it if scratch is tight — requests can stay far ahead of fetches |

Staged files go to `<output>/.staging` unless `--work` points elsewhere;
put it on local scratch if the data area is slow or tight.

Others: `-f {root,hdf5}`, `--pattern GLOB`, `--max-events N` (testing),
`--no-check`, `--stage-timeout H`, `--dccp CMD`, `--plain`, `--dry-run`.

## Converting files already on disk

```bash
python export.py input.sntp.root out/           # one file
python export.py /data/sntp /archive            # a tree, mirrored
python export.py /data/sntp /archive -f hdf5    # HDF5 instead of ROOT
```

Only the trailing `.root` is replaced, so
`2010/run1/f21….sntp.dogwood5.0.root` becomes
`2010/run1/f21….sntp.dogwood5.0.h5` — or keeps the `.root` suffix in the
default ROOT format, which is why the output directory must not be the input
directory. The tool refuses that rather than overwriting its own source.

| Flag | |
|------|--|
| `-f, --format {root,hdf5}` | output format (default `root`) |
| `--pattern GLOB` | which files to pick up (default `**/*.root`) |
| `--manifest FILE` | a manifest other than `branches.txt` |
| `--max-events N` | keep only the first N events per file (testing) |
| `--overwrite` | reconvert files whose output already exists |
| `--no-check` | skip the exclusion checks |
| `--check-events N` | events to sample for those checks (default 5000) |

Compression is not an option: HDF5 is gzip, ROOT is zlib — the codec each
format guarantees every reader can decompress.

## Changing what gets archived

Every branch has a line in [`branches.txt`](branches.txt). Uncommented means
exported; a leading `#` excludes it, and the note says why.

```
NtpStRecord/stp/stp.plane
NtpStRecord/stp/stp.planeview     # fixed by plane (2=U, 3=V)
#NtpStRecord/stp/stp.z            # fixed by plane; one z per plane
```

Editing that file is the whole mechanism — no code change. After editing,
regenerate the reference:

```bash
python make_branches_doc.py
```

**[BRANCHES.md](BRANCHES.md) documents all 755 branches**, each with its
meaning and a ✓/✗ for whether it is archived. It is generated from the
manifest, and CI fails if the committed copy has drifted.

## Reading the output back

```python
import formats

columns, metadata = formats.read_hdf5("archive/f21….h5")   # or read_root
columns["stp.plane"]     # jagged, one entry per event
metadata["events"], metadata["source"]
```

Names drop the `NtpStRecord/<group>/` prefix. Each file records its own
provenance, and says what it is without reference to its filename:
`fHeader.fVldContext.fSimFlag` (`1` data, `4` MC), `.fDetector` (`1` Near,
`2` Far), and for simulation `mchdr.geninfo.codename` (e.g. `daikon_07`).

Opening a ROOT output directly rather than through `read_root` needs two
caveats — branch names are mangled to be ROOT-safe, and jagged fixed-width
runs are flattened. Both mappings are in the tree title as JSON; see
[`formats.py`](formats.py).

## Notes

**Python 3.9 is enough**, which is what minosgpvm has, so nothing needs
installing beyond the venv. `pip` resolves the newest packages that support
whatever Python is present; the round-trip suite was verified byte-identical
on 3.9 (uproot 5.6, awkward 2.8, numpy 2.0) and on 3.10 (uproot 5.7, awkward
2.13). CI checks both.

**Two `/pnfs` namespaces.** The NFS mount on a gpvm is `/pnfs/minos/...`,
which is what to point this at. The `dcap://` URL form uses a different
prefix — `dcap://fndca1.fnal.gov:24125/pnfs/fnal.gov/usr/minos/...` — and is
what you need if `/pnfs` is not mounted; a list file may hold either.

**An unstaged dCache file reads back as the right size in zeros rather than
failing** — that is how two 600 MB files once arrived holding nothing. Every
fetch is checked for the ROOT magic, and a file that never comes online is
abandoned after `--stage-timeout` hours.

**Excluded branches are verified, not assumed.**
[`check_exclusions.py`](check_exclusions.py) re-tests every "this branch is
empty / constant / a duplicate" claim on each file and refuses one that
breaks it. That is not theoretical: it is how `deadchips` was caught being
live in real data after looking empty in all the simulation.

**Data and MC share one schema.** Measured against Elm7 Far Detector files:
the branch set is identical to Dogwood5 MC, and a data file carries the
`mc`/`stdhep`/`thstp` columns present but empty — 99 of the 177. Data
therefore compresses far harder, to about 3% against 29% for MC.

**Design rationale lives with the code**, not here: why HDF5 avoids
variable-length datasets and why ROOT output is a TTree are in
[`formats.py`](formats.py); why each file converts in its own subprocess is
in [`export.py`](export.py).

## Files

| | |
|--|--|
| [`setup.sh`](setup.sh) | makes the venv and installs the four packages |
| [`pipeline.py`](pipeline.py) | stage from tape at Fermilab and convert, on the spot |
| [`export.py`](export.py) | convert files already on local disk |
| [`branches.txt`](branches.txt) | the manifest — what is archived, and why not |
| [`BRANCHES.md`](BRANCHES.md) | all 755 branches explained |
| [`branches.py`](branches.py) | parses the manifest |
| [`formats.py`](formats.py) | HDF5 and ROOT readers and writers |
| [`check_exclusions.py`](check_exclusions.py) | verifies the exclusion claims per file |
| [`make_branches_doc.py`](make_branches_doc.py) | regenerates BRANCHES.md |

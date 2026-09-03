# minos_data_storage

Converts MINOS `.sntp.root` ntuples into HDF5 or ROOT for long-term archival.

It keeps what the detector and the simulation recorded — 177 of the 755
branches — and drops everything the reconstruction chain produced from that:
tracks, showers, slices, clusters, and reconstructed events including the
vertex. MINOS has finished, so anything not archived is gone for good; where
a branch is a borderline call, it is kept.

Two entry points. [`export.py`](export.py) converts files already on local
disk. [`pipeline.py`](pipeline.py) runs at Fermilab: converts files as they
come online from tape, writing the results into your MINOS data area. Moving
them onward is a separate bulk transfer — the conversion is what makes the
files small (an SNTP data file comes out around 3% of its input), so there
is no reason to move the big version anywhere.

## Setting up a gpvm session

```bash
git clone https://github.com/Phidet/minos_data_storage.git
cd minos_data_storage
source ./setup.sh
```

`source` matters — the script activates a venv and sets `SAM_EXPERIMENT` in
your *current* shell, which a child process cannot do for you; run directly
as `./setup.sh` it refuses and tells you so. Run the same command **every
session**: the first run creates `.venv` and installs packages (a few
minutes); every run after that is fast — it sets up `samweb` (UPS
`sam_web_client`) and activates the venv, and does nothing else.

`setup.sh` assumes the standard gpvm UPS location,
`/grid/fermiapp/products/common/etc/setups.sh`, and sets
`SAM_EXPERIMENT=minos` unless it is already set. Both are the values used to
get this working; if `samweb` still isn't on `PATH` afterwards, or picks the
wrong experiment, see [Verifying the SAM setup](#verifying-the-sam-setup).

## Running it — every time

Start in `tmux`, so the job survives your ssh connection dropping —
`source ./setup.sh` still has to happen *inside* that tmux session, since
activation is per-shell.

```bash
tmux new -s minos                 # returning later: tmux attach -t minos
cd minos_data_storage
source ./setup.sh
```

**Stage the data first.** This tool does not stage anything itself — it only
watches dCache and converts what's already there. A SAM dataset definition
is staged in one command, run once, outside the tool:

```bash
samweb prestage-dataset --defname=my_definition --parallel=4
```

That can take hours for a large definition; it's fine to start the pipeline
in the same or another tmux window shortly after — files convert as they
come online, so the two overlap rather than needing to run in sequence.

```bash
ARCHIVE=/exp/minos/data/users/$USER/archive

python pipeline.py --defname my_definition $ARCHIVE --dry-run    # what would happen
python pipeline.py --defname my_definition $ARCHIVE              # do it
```

Detach with `Ctrl-b d` and the run keeps going; `tmux attach -t minos` picks
it back up.

`--defname` is the normal input — MINOS files already have SAM dataset
definitions; `samweb list-definitions` lists them. For files staged some
other way, give a `/pnfs` directory (listing one is metadata only and does
not touch tape), a single `.root` file, or a text file of paths one per
line, in place of `--defname`:

```bash
python pipeline.py /pnfs/minos/reco_far/elm7/sntp_data/2016-06 $ARCHIVE
```

**If it stops, rerun the same command** — progress is in a ledger beside the
output, so it resumes and reconverts nothing. Add `--retry-failed` to requeue
whatever failed; tape errors are often transient.

A run prints two counters (on disk, converted), live on a terminal and as
plain lines in a log.

**Getting it to UCL** is a separate step, once conversion is done and
verified: Globus, or an rsync pull initiated from the far end. Keeping it
separate means a days-long tape job does not depend on the network, and no
credentials sit on a shared machine.

### Nothing is copied to local disk

Prestaging brings a file from tape to dCache disk; the conversion then reads
it **over XRootD**, straight from dCache. There is no staging area, no scratch
budget, and no local copy to clean up.

That is deliberate, and the obvious alternatives are both wrong. Reading
`/pnfs/...` directly goes against Fermilab guidance — those NFS mounts are a
convenience for metadata, and heavy read traffic on them stalls the
interactive node for everyone. Copying each file down first would put a
stream of 600 MB writes onto `/exp/…/data`, a quota-limited NAS that is
explicitly not for heavy I/O. XRootD is the protocol meant for this.

**Staging is `samweb`'s job, not this tool's.** `samweb prestage-dataset`
lodges the whole definition with dCache as one bulk request, which is what
lets dCache order it by tape volume and mount each tape once; requesting
files one at a time sends the robot back to the same tape repeatedly. This
tool only watches — see "Stage the data first" above.

Others: `-f {root,hdf5}`, `--pattern GLOB` (directory mode only), `--door
HOST:PORT` (directory mode only — a SAM definition supplies its own door),
`--max-events N` (testing), `--no-check`, `--stage-timeout H`, `--poll-batch
N`, `--poll-interval S`, `--plain`, `--dry-run`.

### Verifying the SAM setup

Assumptions baked into `setup.sh` and `pipeline.py` that are only checkable
on a real gpvm session — worth running once after cloning:

```bash
source ./setup.sh
which samweb && samweb --version
echo "SAM_EXPERIMENT=$SAM_EXPERIMENT"
samweb list-definitions --limit 5          # is the MINOS station alive, and named what you expect?

# pick a real definition name from the above, then:
samweb list-files "defname: <name>" | head
samweb get-file-access-url --schema=root <one filename from the line above>

python pipeline.py --defname <name> /exp/minos/data/users/$USER/archive --dry-run
```

If any of those come back in a shape `pipeline.py` doesn't expect, it'll show
up directly in the dry-run output or as a samweb-surfaced error rather than
silently doing the wrong thing.

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

**A file that never comes online is abandoned** after `--stage-timeout`
hours, rather than holding the run open — this tool only watches locality,
so a file stuck here was never staged (check `samweb prestage-dataset` ran
and finished) or is still waiting behind a slow tape.

Copying an unstaged dCache file over NFS used to return the right size in
zeros rather than failing — that is how two 600 MB files once arrived here
holding nothing at all. Streaming removes that failure mode: XRootD will not
hand over a file that is not there.

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

## Future direction: output as a SAM definition, and a grid job

Neither of these is built — both are notes for later.

**Output as a SAM definition.** Registering the converted files back into
SAM, rather than just leaving them under the archive directory, would need:

- **A SAM/dCache-registerable write location.** The current output area,
  `/exp/minos/data/users/$USER/archive`, is a NAS mount — not somewhere SAM
  can register a file location. Output would need to move to a `/pnfs` area.
- **New SAM metadata.** `samweb declare-file` needs a full metadata record —
  `file_format`/`data_tier` (a new one: "slimmed archival HDF5/ROOT" doesn't
  exist in SAM's MINOS catalog today), checksum, application/version, and
  whatever run/subrun or parentage fields the MINOS schema expects. That
  data_tier needs defining, most likely by a SAM admin.
- **Permissions** — declaring files is commonly restricted to a production
  role, not a personal one (unconfirmed for MINOS specifically).
- **Grouping** — `add-file-location` after declare, then
  `samweb create-definition` over the new files.

A bad declaration isn't casually undone, and none of the above is checkable
without either a production role or a SAM admin conversation — worth a
distinct follow-up once those are in hand.

**A grid job.** The production-grade way to consume a SAM definition across
many concurrent workers is a **SAM project** (`samweb start-project`, then
each worker calls `getNextFile`/`getFileInfo` in a loop), not a flat
per-file-URL list — a project tracks per-file consumption across workers and
skips anything not yet staged. `--defname`'s current CLI-per-file approach is
right for one job; many concurrent grid workers would want project-based
consumption instead.

Each worker would need: a `jobsub` wrapper for resource requests, the
UPS/samweb environment (the same idea as `setup.sh`, packaged for a batch job
instead of an interactive session), the XRootD/uproot/h5py stack (worker
nodes don't have this repo's venv — needs cvmfs or a shipped tarball), input
via the SAM project's next-file call, and output written to a dCache/RSE
endpoint reachable from a worker node (not the NAS archive path, which isn't
grid-writable at scale).

This is meaningfully less complex than it would have been before this
change: staging is already external and definition-based, so a grid job
would have no request/poll loop to reimplement — just a project-consumption
loop wrapping the existing `export.convert`, which is already
file-in/file-out and would need no change itself.

## Files

| | |
|--|--|
| [`setup.sh`](setup.sh) | run every gpvm session: sets up samweb, makes/activates the venv |
| [`pipeline.py`](pipeline.py) | convert a SAM definition (or a /pnfs directory) at Fermilab, on the spot |
| [`export.py`](export.py) | convert files already on local disk |
| [`branches.txt`](branches.txt) | the manifest — what is archived, and why not |
| [`BRANCHES.md`](BRANCHES.md) | all 755 branches explained |
| [`branches.py`](branches.py) | parses the manifest |
| [`formats.py`](formats.py) | HDF5 and ROOT readers and writers |
| [`check_exclusions.py`](check_exclusions.py) | verifies the exclusion claims per file |
| [`make_branches_doc.py`](make_branches_doc.py) | regenerates BRANCHES.md |

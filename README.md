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
| `--branches FILE` | a manifest other than `branches.txt` |
| `--compression {gzip,lzf,none}` | default `gzip` |
| `--max-events N` | keep only the first N events per file (testing) |
| `--overwrite` | reconvert files whose output already exists |
| `--dry-run` | list planned work and stop |
| `--no-check` | skip the exclusion checks described below |
| `--dump-branches FILE` | print every branch in a file, as manifest lines |

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
change.** 166 of 755 branches are enabled by default. For a file with a
different branch set, regenerate the list with
`export.py --dump-branches FILE.root` and annotate it.

`branches.py` parses it, and checks the count in the header comment against
the number actually enabled, so the two cannot drift apart.

## Output formats

**HDF5** (`.h5`) — the compact, dependency-light option. On the test file
(119,205 events, 166 branches) 2.0 GB of SNTP becomes 578 MB, and gzip
accounts for a 58% saving over the same data uncompressed.

Jagged columns are stored as a `values` dataset plus an `offsets` dataset,
not as one variable-length dataset. This is not a stylistic choice: HDF5
keeps variable-length data in a global heap and applies dataset filters only
to the pointers, so a vlen dataset is effectively *uncompressed* whatever
codec is requested — measured at 2% for gzip against 39% for the same data
and codec in this layout. An `inner_shape` attribute records the trailing
dimensions, so a cell written as `(n, 4)` comes back that shape rather than
as a flat run.

**ROOT** (`.root`) — a plain `TTree`, in case this is the format the data
ends up kept in. 577 MB on the same file, so the two formats land within
half a percent of each other and the choice can be made on what will read
them rather than on size. Two things are worth knowing:

- It is written with `mktree`, deliberately. Assigning a dict to a key
  (`f["NtpSt"] = {...}`) produces an **RNTuple**, which only ROOT 6.28 and
  later can read.
- Branch names are made ROOT-safe (`mc.p4mu1[4]` → `mc_p4mu1_4`), because
  uproot reads a dotted name back as a nested record that cannot be indexed.
  The original names are stored in the tree title as JSON, and `read_root`
  restores them.

A branch that is a variable-length list of fixed-width rows —
`vetostp.z[2]`, one pair per shield hit — cannot be expressed as a single
TTree branch. Those are flattened on write and restored on read, with the
width recorded alongside the names. A fixed-width branch that has no jagged
axis keeps its declared width.

Both formats round-trip every enabled branch with dtype, shape and values
intact; this is checked against the source file rather than assumed.

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

**Real data vs simulation.** The default set includes `mc.*`. Real-data
files carry those branches zero-filled, so they convert without complaint.
If a branch is genuinely missing the file is reported as failed, naming the
branch, rather than guessing.

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

# Variable reference

What each archived branch means. Anything still unresolved is marked
**`???`**.

## Header

| Branch | Meaning |
|--------|---------|
| `fHeader.fRun` | DAQ run number. |
| `fHeader.fSubRun` | DAQ subrun number. |
| `fHeader.fSnarl` | Snarl number within the run. |
| `fHeader.fEvent` | Event number within the snarl. Constant `-1` in every file checked — the reconstruction chain assigns it. |

## `stp` — digitised strip hits

| Branch | Meaning |
|--------|---------|
| `stp.plane` | Plane number along the beam axis, 1–485 in a Far Detector file. |
| `stp.strip` | Strip number within the plane, 0–191. |
| `stp.planeview` | Which stereo view the strip belongs to: `2` = U, `3` = V. |
| `stp.ph0.raw` | Raw ADC, east end — what the electronics recorded, before any correction. |
| `stp.ph1.raw` | Same, west end. |
| `stp.ph0.sigcor` | Attenuation-normalised strip response, east end. |
| `stp.ph1.sigcor` | Same, west end. |
| `stp.ph0.pe` | Calibrated light yield, east end [photoelectrons]. |
| `stp.ph1.pe` | Same, west end. |
| `stp.time0` | Charge-weighted mean hit time, east end [s], relative to the trigger. `-999999` means that end saw no signal. |
| `stp.time1` | Same, west end. |

Raw ADC is kept alongside the calibrated pulse height on purpose. The
conversion between them is per-strip and cannot be inverted, so an archive
holding only the calibrated values could never be re-calibrated. With both,
the factor is recoverable from the data.

## `mc` — interaction truth

| Branch | Meaning |
|--------|---------|
| `mc.inunoosc` | PDG code of the neutrino flavour at production. Equals `mc.inu` throughout the file checked, but would differ in a sample where flavours are swapped. |
| `mc.iaction` | `0` = NC, `1` = CC. |
| `mc.itg` | PDG code of the struck target: `2212`/`2112` nucleons, a large nucleus code for coherent events, `11` for inverse muon decay. |
| `mc.iresonance` | Channel: `1001` QE, `1002` resonance, `1003` DIS, `1004` coherent pion, `1005` inverse muon decay. |
| `mc.istruckq` | PDG id of the struck quark: `0` none (non-DIS), `1` d, `2` u. |
| `mc.iflags` | Hadronisation model: `0` non-DIS, `1` old KNO, `2` modified KNO, `3` charm, `11`/`12`/`13` JETSET string/cluster/other. |
| `mc.x` | Bjorken x. |
| `mc.y` | Inelasticity y. The invariant form, not the lab-frame ratio, which ignores Fermi motion. |
| `mc.q2` | Four-momentum transfer squared. |
| `mc.w2` | Hadronic invariant mass squared [GeV²]. |
| `mc.sigma` | Cross section for this interaction. Units unconfirmed — passed through unchanged from NEUGEN. **`???`** |
| `mc.sigmadiff` | Differential cross section. Same issue. **`???`** |
| `mc.emfrac` | EM fraction of hadronic shower energy. Pre-FSI. |
| `mc.ndigu` | Raw digits in the u-view truth-matched to this interaction. |
| `mc.ndigv` | Same, v-view. A digit touching both views is counted in u only. |
| `mc.tphu` | Summed pulse height, u-view — **raw ADC, pedestal-subtracted** — over the same digits as `ndigu`. |
| `mc.tphv` | Same, v-view. |

4-momenta are `(px, py, pz)` then energy, all GeV.

| Branch | Meaning |
|--------|---------|
| `mc.p4neunoosc[4]` | Neutrino 4-momentum under the unoscillated hypothesis. |
| `mc.p4mu1[4]` | Primary muon 4-momentum. Duplicates a `stdhep` lepton row, but with the energy component's sign flipped for the matter lepton. |
| `mc.p4shw[4]` | Final-state hadronic system. Not the sum of the `stdhep` hadrons: it is evaluated before final-state interactions (FSI) — the rescattering of products inside the struck nucleus — whereas `stdhep` records the particles that emerge after them. |

## `stdhep` — truth particle stack

One row per particle, variable length per event: the incoming neutrino, the
struck target, and everything in the final state.

| Branch | Meaning |
|--------|---------|
| `stdhep.IdHEP` | PDG code. |
| `stdhep.IstHEP` | HEPEVT status code: `0` initial state, `1` final state, `11` struck nucleon. |
| `stdhep.mass` | Rest mass [GeV]. |
| `stdhep.p4[4]` | 4-momentum. |
| `stdhep.vtx[4]` | Production 4-position: `(x, y, z)` in metres, then time in seconds. |
| `stdhep.parent[2]` | Indices of the particle's parents. |
| `stdhep.child[2]` | Indices of its daughters. |
| `stdhep.ndethit` | How many digits it deposited energy in. |

**Every event carries one `IstHEP == 999`, `IdHEP == 0` row** — a *rootino*,
a null placeholder never tracked.

The genealogy (`parent`, `child`) is kept because it exists nowhere else:
without it, the decay chain behind a final-state particle cannot be
reconstructed from the archive, and there will be no new simulations to
regenerate it from.

## `thstp` — strip truth

Truth for each strip in `stp`: which simulated particles deposited energy
there, and in what proportion. Exactly one record per hit, so it lines up
row-for-row with the hit variables.

| Branch | Meaning |
|--------|---------|
| `thstp.neumc` | Index of the interaction (`mc` record) responsible for the strip. |
| `thstp.nneu` | How many interactions contributed to it. |
| `thstp.sigflg` | Signal flag. |
| `thstp.stdhep[3]` | Up to three contributing `stdhep` particle indices. |
| `thstp.phfrac[3]` | Fraction of the strip's pulse height from each of those. |

## `mc.flux` — beam simulation

The gnumi record: where this neutrino came from, from the primary proton
through to its weight at each detector. Kept in full because there will be
no new beam simulation to regenerate it from.

| Fields | What they hold |
|--------|----------------|
| `fluxrun`, `fluxevtno` | Which beam-simulation event this was. |
| `ntype`, `nenergy`, `npz`, `ndxdz`, `ndydz` | The neutrino as generated: flavour, energy, direction. |
| `nenergynear`, `nwtnear`, `ndxdznear`, `ndydznear` | The same neutrino as it would appear at the Near Detector, with the weight that turns generated events into a flux prediction there. |
| `nenergyfar`, `nwtfar`, `ndxdzfar`, `ndydzfar` | The same for the Far Detector. Together with the near fields, this pair is what the near/far extrapolation is built from. |
| `ndecay`, `norig`, `vx`, `vy`, `vz`, `pdpx`, `pdpy`, `pdpz`, `necm` | The decay that produced it: mode, where it happened, parent momentum. |
| `ptype`, `pppz`, `ppenergy`, `ppdxdz`, `ppdydz`, `ppmedium`, `ppvx`, `ppvy`, `ppvz` | The parent hadron: type, momentum, and where it was produced. |
| `muparpx`, `muparpy`, `muparpz`, `mupare` | The muon's momentum and energy, where the parent was a muon. |
| `tgen`, `tptype`, `tgptype`, `tvx…tpz`, `tgppx…tgppz`, `tprivx…tprivz` | Ancestry in the target. `tgen` counts how many hadronic interactions deep the chain runs, which is what hadron-production reweighting needs. |
| `beamx…beampz`, `xpoint`, `ypoint`, `zpoint` | The primary proton beam, and the ray-traced point used for the weights. |
| `nimpwt`, `mc.fluxwgt.weight`, `weighterr`, `version` | Importance weight, then the overall flux weight with its uncertainty and the version that produced it. |
| `mc.fluxwgt.beam[33]` | Per-beam-configuration flux weights. |

## `calstatus`, `detstatus` — detector state

| Branch | Meaning |
|--------|---------|
| `calstatus.gevpermip` | Calibration constant converting MIP-equivalent signal to GeV. Needed to turn the `pe` values into energy. |
| `detstatus.coilcurrent1` | Magnet coil current, which sets the field and hence momentum and charge-sign measurement. |
| `detstatus.coilcurrent2` | Second coil current reading. |
| `detstatus.coilstatus` | Magnet on/off and polarity. |
| `detstatus.dcscoilstatus` | The same, as reported by the slow-control system. |
| `detstatus.dbuhvstatus` | Photomultiplier high-voltage status. |
| `detstatus.coldchips1` | Front-end chips below HV threshold in supermodule 1. |
| `detstatus.coldchips2` | The same for supermodule 2. |

## `dataquality`, `timestatus` — DAQ context

Beam, trigger and absolute-timing context for the snarl. Unset (`-1`) for
Monte Carlo.

| Branch | Meaning |
|--------|---------|
| `dataquality.spillstatus` | Beam spill status. |
| `dataquality.spilltype` | Beam spill type. |
| `dataquality.spilltimeerror` | Spill timing error. |
| `dataquality.trigsource` | What triggered the readout. |
| `dataquality.trigtime` | Trigger time. |
| `dataquality.snarlmultiplicity` | Interactions in this snarl. |
| `dataquality.cratemask` | How many readout crates were active; 16 is a full Far Detector readout. |
| `dataquality.pretrigdigits` | Digits recorded before the trigger. |
| `dataquality.posttrigdigits` | Digits recorded after it. |
| `dataquality.errorcode` | DAQ error code. |
| `dataquality.readouterrors` | Readout errors in this snarl. |
| `dataquality.coldchips` | Front-end chips reading nothing. |
| `dataquality.hotchips` | Chips firing far above their expected rate. |
| `dataquality.busychips` | Chips saturated by readout load. |
| `dataquality.dataqualityword` | Packed overall data-quality flag for the snarl. |
| `timestatus.sgate_10mhz` | Spill gate on the 10 MHz clock. |
| `timestatus.sgate_53mhz` | Spill gate on the 53 MHz clock. |
| `timestatus.rollover_53mhz` | 53 MHz counter rollovers. |
| `timestatus.rollover_last_53mhz` | The rollover count at the previous snarl, so an interval spanning a rollover can be unwrapped. |
| `timestatus.crate_t0_ns` | Crate time zero [ns]. |
| `timestatus.timeframe` | Time frame number. |

The light-injection fields describe MINOS's LED calibration pulser, which
fired between beam spills. They identify LI snarls and record what the
pulser did, so those snarls can be recognised and the gain calibration
re-derived.

| Branch | Meaning |
|--------|---------|
| `dataquality.litrigger` | Whether this snarl was a light-injection trigger. |
| `dataquality.litime` | LI pulse time. |
| `dataquality.lisubtractedtime` | The same with the pedestal offset removed. |
| `dataquality.lirelativetime` | Time relative to the LI trigger. |
| `dataquality.licalibpoint` | Which point in the calibration sequence. |
| `dataquality.licalibtype` | Which calibration type was running. |
| `dataquality.libox` | Which pulser box fired. |
| `dataquality.liled` | Which LED within it. |
| `dataquality.lipulseheight` | Pulser amplitude setting. |
| `dataquality.lipulsewidth` | Pulser width setting. |

## `vetostp` — veto shield hits

Raw hits in the veto shield. Values indexed `[2]` are one per strip end.

| Branch | Meaning |
|--------|---------|
| `vetostp.pln` | Shield plane. |
| `vetostp.plank` | Shield plank within the plane. |
| `vetostp.x` | Position [m]. |
| `vetostp.y` | Position [m]. |
| `vetostp.z[2]` | Position at each strip end [m]. |
| `vetostp.adc[2]` | Raw pulse height at each end [ADC]. |
| `vetostp.time[2]` | Hit time at each end. |
| `vetostp.timeraw[2]` | The same before timing corrections. |
| `vetostp.ndigit` | Digits on this shield strip. |
| `vetostp.pmtindex[2]` | Which photomultiplier channel each end is read out by. |
| `vetostp.pmtpixel[2]` | Which pixel on that photomultiplier. |
| `vetostp.wlspigtail[2]` | Length of wavelength-shifting fibre from the strip end [m]. |
| `vetostp.clearlen[2]` | Length of clear fibre from there to the photomultiplier [m]. |

The last four describe the shield's readout map rather than the event, and
the equivalent fields on `stp` (`pmtindex0`/`pmtindex1`) are excluded for
exactly that reason. They are kept here because the shield is a smaller,
less-documented system whose mapping may not survive elsewhere — an
inconsistency worth revisiting rather than one to rely on.

---

# Not included

## Recoverable from what is kept

Each of these is re-checked per file before anything is dropped, so a file
that breaks one is refused rather than silently stripped.

| Branch | Why |
|--------|-----|
| `stp.z` | One fixed z per plane, so a lookup on `stp.plane`. |
| `stp.tpos` | Transverse position, fixed by `plane` and `strip` together. |
| `stp.ndigit` | Only ever 1 or 2: how many ends of the strip fired. The same information as which of `time0`/`time1` holds the `-999999` sentinel. |
| `mc.inu` | Duplicates the PDG code on the `stdhep` initial-state neutrino row (`IstHEP == 0`). |
| `mc.a` | The target nucleus's mass number, encoded in the PDG code of the `stdhep` nucleus row. Hydrogen when there is no such row. |
| `mc.z` | Its atomic number, from the same code. |
| `mc.vtxx` | Duplicates `vtx[0]` on the `stdhep` neutrino row. |
| `mc.vtxy` | Duplicates its `vtx[1]`. |
| `mc.vtxz` | Duplicates its `vtx[2]`. |
| `mc.p4neu[4]` | Duplicates `p4` on the `stdhep` neutrino row. |
| `mc.p4tgt[4]` | Duplicates `p4` on the `stdhep` struck-nucleon or nucleus row. |
| `mc.p4mu2[4]` | Duplicates a second `stdhep` muon row, where the event has one. |
| `mc.p4el1[4]` | Duplicates an `stdhep` electron row. |
| `mc.p4el2[4]` | Duplicates a second one. |
| `mc.p4tau[4]` | Duplicates an `stdhep` tau row. Never non-zero in any file checked — no ν_τ events. |

## Array bookkeeping — meaningless once loaded

| Branch | Why |
|--------|-----|
| `stp.index` | Position of the strip in its array; row order already carries it. |
| `mc.index` | Likewise for the interaction record. |
| `stdhep.index` | Likewise for the particle. |
| `mc.flux.index` | Likewise for the flux record. |
| `mc.fluxwgt.index` | Likewise for the flux weight. |
| `mc.stdhep[2]` | Index range pointing into `stdhep`; both are already joined per event. |
| `stdhep.mc` | The same pointer in reverse. |

## Detector hardware, not event data

| Branch | Why |
|--------|-----|
| `stp.pmtindex0` | Which photomultiplier channel the east end is wired to. Fixed per strip, and a property of the readout map rather than the event. |
| `stp.pmtindex1` | The same for the west end. |
| `stp.demuxveto` | Output of demultiplexing, which resolves the Near Detector's several-strips-per-channel readout. Constant `0` in the Far Detector files here, and a reconstruction step in any case. |

## An intermediate calibration stage

| Branch | Why |
|--------|-----|
| `stp.ph0.siglin` | Sits between `raw` and `pe`, linearity-corrected but not yet attenuation-corrected. Both endpoints of that chain are kept, and the factor between them is recoverable from the pair, so the middle step adds little. |
| `stp.ph1.siglin` | The same, west end. |

## Unset

| Branch | Why |
|--------|-----|
| `mc.iboson` | Should carry the exchange boson's PDG code (Z⁰ = 23, W⁺ = 24) but holds a constant sentinel in every file checked. |

## Truth dropped for a technical reason

| Branch | Why |
|--------|-----|
| `stdhep.dethit[2]` | The particle's first and last hit — plane, strip, position and momentum for each. Left out because uproot reads it as a C++ struct array, which would need unpacking work. **This is the one exclusion that loses information with no way to recover it**, and is worth revisiting. |

## Whole branch groups

| Group | Why |
|-------|-----|
| `trk` | Reconstructed tracks. |
| `shw` | Reconstructed showers. |
| `slc` | Reconstructed slices. |
| `clu` | Clusters, upstream of track and shower fitting. |
| `evt` | Reconstructed events — including the vertex `evt.vtx.*`, which analyses do use for fiducial cuts, but which is reconstruction output like the rest. |
| `thevt` | Truth matching for reconstructed events. Unlike `thstp`, which labels the strips we keep, this is meaningless without the reco object it describes. |
| `thtrk` | Likewise for tracks. |
| `thshw` | Likewise for showers. |
| `thslc` | Likewise for slices. |
| `crhdr` | Cosmic-ray zenith/azimuth and sky coordinates, derived from reconstructed tracks. |
| `vetohdr` | Veto shield summary; the raw hits are kept. |
| `vetoexp` | Where a *reconstructed track* was expected to cross the shield — a projection, not a measurement. |
| `dmxstatus` | Demultiplexing quality; demultiplexing is a reconstruction step. |
| `deadchips` | Which channels were dead — genuinely useful for efficiency, but **empty in every file checked**, so there is nothing to keep. |
| `digihit` | Per particle per strip: entry and exit point, path length. The finest-grained truth there is — but **empty in every file checked**. |
| `detsim` | Per-snarl counters from the electronics simulation: photoelectrons, pixels hit, cross-talk, and how many digits survived each trigger stage. A record of the simulation's behaviour rather than the event's. |
| `photon` | Per-snarl counters from the optical simulation: blue scintillation photons made, green ones re-emitted by the fibre, photoelectrons resulting, and how much was discarded. Describes how the simulation ran; the resulting light is already in `stp.ph*`. |
| `mchdr` | Generator codename, host and timestamp. |
| `evthdr` | Counts of reconstructed objects per snarl. |

`deadchips` and `digihit` are dropped only because they are empty. If a file
turns up where they are populated, `check_exclusions.py` refuses it rather
than dropping real data — enable them in the manifest and re-run.

## Files

| | |
|--|--|
| [`branches.txt`](branches.txt) | the manifest — every branch, grouped, with reasons |
| [`branches.py`](branches.py) | parses it |
| [`formats.py`](formats.py) | HDF5 and ROOT writers and readers |
| [`export.py`](export.py) | the CLI |
| [`check_exclusions.py`](check_exclusions.py) | verifies the assumptions behind dropped branches |
| [`SCHEMA.md`](SCHEMA.md) | the derivations behind the redundancy claims above |

# Column reference

Full list of columns in `data.parquet` and `truth.parquet`, and what each
one means. Source: the MINOS `NtpSt` ntuple (`NtpStRecord.stp` for hits,
`NtpStRecord.mc` and `NtpStRecord.stdhep` for truth).

Confirmed fields below are cross-checked against three sources: (1) a
2003 MINOS internal glossary for the predecessor `NtpSR` tree (same
field names, `NtpSRStrip`/`NtpSRMCTruth`/`NtpSRStdHep`), archived at
[web.archive.org](https://web.archive.org/web/20111018134109/http://www-numi.fnal.gov/offline_software/srt_public_context/WebDocs/ntpdict.html);
(2) `NuEvent.h`, a MINOS common-ntuple/analysis header (comments map its
fields straight back to the raw `mc.*` branches, e.g. `iresonance;//QE=1001,
RES=1002, DIS=1003, CPP=1004`); (3) the actual value distributions in
this file. A couple more fields (`ndigu`/`ndigv`/`tphu`/`tphv`) are
addressed by a newer doxygen page, `Truth.h` on `nusoft.fnal.gov` — that
host doesn't resolve from this environment (DNS failure) so I couldn't
read it directly, only a search engine's indexed snippet of it, which is
weaker evidence than (1) and (2) and is labeled as such below. Everything
else (`istruckq`, `iflags`, `sigmadiff`, exact `view` mapping) is still
genuinely unresolved — marked `???` with my best guess. If you know the
real answer, replace the `???`.

**Every "exact duplicate of the particle table" claim below is checked
by `validate_redundancy.py`, not just asserted from a one-off sample.**
Run it against a new file before trusting that these columns are still
safe to drop there:

```bash
uv run validate_redundancy.py input.sntp.root
```

This exists because a hand-picked-sample spot-check of the `p4mu1`/
`p4mu2`/`p4el1`/`p4el2` reconstruction rule turned out to be wrong (see
those rows below) — it happened to pick examples that hid the real,
more subtle rule. The script re-derives every dropped branch from the
raw ROOT file across *every* event and reports PASS/FAIL/SKIP, so that
kind of mistake gets caught by re-running it rather than by getting
lucky on which sample a human happened to check by hand.

Struck-through rows below (e.g. ~~`event`~~) are fields that are read
from the source file but dropped from the Parquet output, with the
reason given in place of a description. Three different reasons show up:

- **Zero information**: `event` and `iboson` are a single constant
  value across all 119205 events in every file checked so far (not
  just low-variance). If you convert a file where one of them might
  actually vary, add the branch back in `root_to_parquet.py` — one
  line each, see the comments there.
- **Exact duplicate of the particle table, verified**: `p4neu0..3`,
  `p4tgt0..3`, `p4mu10..3`, `p4mu20..3`, `p4el10..3`, `a`, `z`,
  `vtxx`/`vtxy`/`vtxz` were checked row-by-row (not assumed) to be
  byte-for-byte reconstructable by filtering the particle-level table
  on `pdg`/`status` — see each row for the exact filter, including the
  channel-dependent (QE/RES/DIS vs. coherent vs. inverse-muon-decay)
  rules for `itg`-like fields and the one sign-convention quirk on
  `p4mu1`/`p4el1`'s energy component. Recover any of them with a
  filter/join instead of paying to store the same numbers twice.
- **Same category, not independently verified**: `p4el20..3` and
  `p4tau0..3` are dropped by analogy with the pattern above (their
  particle-table counterpart would be `pdg == ±11`/`±15`, `status == 1`
  respectively), but this file has no nonzero example of either to
  spot-check against — see their rows for what would need re-checking
  if that assumption turns out wrong.

See "Dropped branches" at the end of this document for everything
dropped at the *branch-group* level (MINOS reconstruction output,
detector status monitoring, etc.) rather than field-by-field.

## Storage

Two lossless-for-practical-purposes downcasts, both noted inline in the
tables below: `time0`/`time1` from the source's 8-byte `Double_t` to
float32 (round-trip error <1e-13, far below any physical significance),
and `strip` from the default 2-byte int to `uint8` (max observed value
191). Together with switching Parquet's compression codec from the
pyarrow default (`snappy`) to `zstd` at a high level, and dropping the
11 event-level fields that duplicate the particle table (the six
4-vectors above, plus `a`/`z`/`vtxx`/`vtxy`/`vtxz`), this cuts total
output size by roughly a third. `itg` is the one exception left in:
it's the same kind of duplicate as `p4tgt`, but kept anyway because,
unlike `p4tgt`, it's the *key* needed to find the right particle-table
row in the first place, and it's a single cheap scalar.

### Genuinely not reconstructable (checked, kept)

`ndigu`/`ndigv` are strongly correlated with (but not identical to —
only ~3% exact matches) the per-view hit counts you'd get by grouping
`data.parquet`. `emfrac` disagrees with the naive EM-energy fraction
computed from the particle table (median abs. difference 0.41 against a
mean value of 0.18). `p4shw` matches neither the sum of the particle
table's final-state hadrons nor `p4neu + p4tgt − p4lep` (100% mismatch).
The likely reason for the last two: both are evaluated *pre*-FSI, before
the intranuclear cascade, while the particle table is post-FSI. `snarl`
is not a restatement of `entry` either (they agree for only 3.4% of
events — snarl numbers skip). All of these carry real information.

### Derivable but deliberately kept

These *are* reconstructable, but were kept anyway — together they'd save
only ~4 MB (1.4% of the output), which doesn't justify making every user
re-derive them, and several break on specific channels:

| Field | Reconstruction | Why kept |
|-------|----------------|----------|
| `q2` | `(p4neu − p4lep)²` | Breaks for coherent + IMD |
| `w2` | `(p_tgt + q)²` | Breaks for coherent (nucleus target) + IMD |
| `x` | `−q2 / (2 p_tgt·q)` | Breaks for coherent + IMD |
| `y` | `(p_tgt·q)/(p_tgt·p4neu)` | Breaks for IMD; needs the invariant form, not the naive lab-frame ratio |
| `inu` | pdg of the `status == 0` neutrino row | 0.05 MB; needed constantly, and it's the natural key |
| `itg` | pdg of the `status == 11` row | 0.10 MB; channel-dependent, and it's a lookup key |
| `iaction` | 1 iff a `status == 1` charged lepton exists | 0.09 MB; 99.993%, not exact |
| `inunoosc` | equals `inu` in this file | Could legitimately differ in an oscillated sample |

`mass` (particle-level, 1.09 MB) is a special case: it's a *pure*
function of `pdg` here — 192 distinct codes, none with more than one
mass. But unlike everything else in this table, recovering it needs an
**external PDG mass table**, not just other columns of this file. That
makes it not self-contained, which cuts against the whole point of this
format, so it stays.

## Identifiers (both files)

| Column   | Meaning |
|----------|---------|
| `entry`  | Row index into the source `NtpSt` tree. The join key across `data.parquet` and `truth.parquet`, and the thing to `groupby`/filter on to get one event. |
| `run`    | DAQ run number. |
| `subrun` | DAQ subrun number. |
| `snarl`  | Snarl (readout window / trigger) number within the run. |
| ~~`event`~~ | Dropped: `fHeader.fEvent` is always `-1` in every file checked so far — event-level indexing within a snarl is assigned by the reconstruction chain we intentionally don't keep. Use `entry`. |

## `data.parquet` — digitized strip hits

One row per hit; one null-padded row per event with zero hits.
Confirmed against the glossary's `stp.` (`NtpSRStrip`) entry.

| Column   | Meaning |
|----------|---------|
| `plane`  | Scintillator plane number along the beam axis (1–485 in this Far Detector file; 486 planes total). |
| `view`   | Strip orientation (`NtpSRStrip.planeview`, type `PlaneView::EPlaneView`) — one of MINOS's two ±45° readout planes (there is no separate X view). Only values `2` and `3` occur; the glossary names the enum but not its values, so which integer is U and which is V isn't confirmed. `???` |
| `strip`  | Strip number within the plane (0–191). **Stored as `uint8`** (downcast from the source's wider int type — 191 fits comfortably, saves a byte/hit). |
| `z`      | Plane position along the beam axis, in meters — confirmed by the glossary (`stp.z`, "z position (m)"). |
| `pe0`    | Calibrated light yield at the strip's **east** end, in photoelectrons (confirmed: `pmtindex0`/`ph0` = east). This is the `pe` calibration stage specifically — MINOS also has `raw` (ADC counts), `siglin` (nonlinearity-corrected) and `sigcor` (attenuation-normalized) stages for the same hit, not kept here. |
| `pe1`    | Calibrated light yield at the strip's **west** end, in photoelectrons (`pmtindex1`/`ph1` = west). |
| `time0`  | Hit time at the east end: a charge-weighted mean, in **seconds**, relative to the event trigger (confirmed by the glossary). Sentinel value `-999999` means that end didn't register a signal (true for ~23% of hits — only one end fires above threshold). **Downcast from the source's `Double_t` (float64) to float32** — max round-trip error ~1e-13, far below any physical significance. |
| `time1`  | Same as `time0`, for the west end (same float32 downcast). |

Occupancy is very low (~0.1% of the full plane×strip grid has a hit),
which is why this is a sparse/long table rather than a dense image — see
the README.

## `truth.parquet` — MC truth

One row per final-state truth particle, with that particle's event-level
interaction truth repeated on every row. One null-padded row per event
with zero truth particles (none observed in this file, but the join is
still null-safe).

### Event-level interaction truth (repeated per particle)

Confirmed against the glossary's `NtpSRMCTruth` entry, which lists the
same field names.

| Column        | Meaning |
|---------------|---------|
| `inu`         | PDG code of the interacting neutrino (confirmed by both sources: "PDG id of neutrino"; `NuEvent.h`: ">0 particles, <0 anti-particles"). ±12 νe, ±14 νμ, ±16 ντ. |
| `inunoosc`    | PDG code of the neutrino's flavor at production, before oscillation (confirmed: "PDG id of un-oscillated neutrino"; `NuEvent.h`: "id of neutrino at birth"). Identical to `inu` for every event in this file — NEUGEN generates interactions using the unoscillated flux and applies oscillation as a separate downstream weight rather than swapping the interacting flavor, which is consistent with the two fields matching here. |
| `itg`         | PDG code of the target struck within the nucleus (confirmed: "PDG id of target nucleon"). 2212 proton, 2112 neutron for QE/RES/DIS channels, the large "nucleus" PDG code for coherent events (same as ~~`a`~~/~~`z`~~ below), `11` (electron) for the 8 inverse-muon-decay events. **Kept despite being the same kind of duplicate as `p4tgt`** (see below) because — unlike `p4tgt` — it's needed as the *key* to find the right particle-table row in the first place, and it's a single cheap scalar. |
| ~~`a`~~       | Dropped: mass number (A) of the target nucleus (confirmed: "nucleus A"). **Verified an exact duplicate for 100% of events**: decodes as `(pdg // 1e6) % 1000` from the particle-table's initial-state nucleus row (`status == 0`, `pdg > 1e9`) for 118859/119205 events; the remaining 346 have no such row and are — verified — all exactly hydrogen (`a=1`), which is the correct default whenever a nucleus row is absent (a free proton has no separate "nucleus"). |
| ~~`z`~~       | Dropped: **not** Bjorken z, and **not** a position — the atomic number (Z) of the target nucleus (confirmed: the glossary lists it right after `a` as "nucleon Z"). Same decode as `a` (`(pdg // 1e3) % 1000`), same 100%-verified hydrogen fallback. 15 distinct integer values 1–29 in this file, consistent with the mix of elements in steel + scintillator (H, C, O, Fe, plus alloy trace elements). Don't confuse with the spatial vertex `vtxz`, or with `data.parquet`'s `z` (plane position) — same column name, unrelated quantities, different files/columns. |
| `iaction`     | Interaction type. Confirmed by both sources — the 2003 glossary: "0=NC 1=CC (2=QE 3=QE+pi)"; `NuEvent.h`: "CC=1, NC=0". Only `0`/`1` occur in this file, split ~25%/75%, matching the expected NC:CC ratio. The glossary's `2`/`3` sub-codes aren't observed here — possibly retired in this generator version, since `iresonance` (below) now carries the QE/RES/DIS/COH split instead. |
| ~~`iboson`~~  | Dropped: PDG code of the exchanged boson (confirmed meaning), but constant `-99999` (not a real PDG code) for every event in this file — looks unset/unused in this generator configuration. |
| `iresonance`  | Interaction-channel code. **Confirmed exact mapping** via `NuEvent.h`: `QE=1001, RES=1002, DIS=1003, CPP=1004` (CPP = coherent pion production). Despite the name, this isn't just about baryon resonances — it's the QE/RES/DIS/COH channel selector. This file has a 5th value, `1005`, covering exactly 8 events, all CC — checked and **ruled out 2p2h/MEC**: those 8 events have `itg = 11` (an electron, not a nucleon), `x`/`y`/`q2`/`w2` all exactly `0` (undefined for a lepton-target process, left at the default), a final state of exactly one muon + one νe, and neutrino energies (14.7–56.5 GeV) all above the ~11.03 GeV threshold for νμ + e⁻ → μ⁻ + νe. `1005` = **inverse muon decay**, a real but rare process (elastic scattering off atomic electrons rather than nucleons), consistent with its 8/119205 (~0.007%) rate. |
| `istruckq`    | Struck-quark-related code for DIS events. Not in the 2003 glossary (newer field); GENIE's analogous `.gst` summary-ntuple format has a documented "hit quark PDG code (DIS only)" field, which supports this general reading without confirming MINOS's specific encoding. Values `{0, 1, 2}` observed (76%/4%/19%) — too few distinct values to be a raw PDG code, so probably a small enumerated category rather than the quark's actual PDG id. `???` (encoding) |
| `iflags`      | Bitmask of additional interaction flags. Not in the 2003 glossary (newer field). Values `{0, 2, 3, 11}` observed. `???` |
| `x`           | Bjorken x, standard DIS kinematic variable (confirmed: "x"). Range [0, 1], continuous. **Kept, though derivable**: equals `-q2 / (2 p_tgt·q)` (with `q = p4neu - p4lep`) for QE/RES/DIS, but **not** for coherent events, where the "target" is the whole nucleus and the nucleon-level relation breaks (100% mismatch there). See the "Derivable but kept" note below. |
| `y`           | Inelasticity y, standard DIS kinematic variable (confirmed: "y"). Range [0, 1], continuous. **Kept, though derivable**: equals the *invariant* `(p_tgt·q)/(p_tgt·p4neu)` — note this is not the naive lab-frame `(Eν−Elep)/Eν`, which disagrees for ~93% of events because it ignores the target nucleon's Fermi motion. Verified to match for every channel except inverse muon decay. |
| `q2`          | Four-momentum transfer squared (confirmed: "Q**2"). Despite the plain label, values in this file are **negative** (spacelike, `q2 = -Q²` in the usual positive-`Q²` convention) — negate it if you want the positive `Q²` normally quoted. **Kept, though derivable**: equals `(p4neu - p4lep)²` for QE/RES/DIS. An earlier version of this doc claimed it was *not* derivable; that was wrong — the check had used `p4mu1`'s sign-flipped energy without correcting the sign (see the `p4mu1` row). |
| `w2`          | Hadronic invariant mass squared, `W²` (confirmed: "W**2"), GeV². Cross-checked: quasi-elastic events in this file have `w2 ≈ 0.880` = (proton mass)², as expected when the target nucleon stays intact. **Kept, though derivable**: equals `(p_tgt + q)²` for QE/RES/DIS (100% match), but **not** for coherent events (100% mismatch — nucleus target, as for `x`). Same earlier-doc correction as `q2`. |
| `sigma`       | Cross section for this interaction. Confirmed by both sources — glossary: "cross section for this interaction"; `NuEvent.h`: "mc.sigma=cross-section". A plausible-sounding claim (from an AI search summary) that this is in the standard HEP unit `10⁻³⁸ cm²` and follows the textbook CC scaling `σ ≈ 0.67×Eν[GeV] ×10⁻³⁸ cm²` (per nucleon) **does not hold up against this file**: for CC events, `sigma/Eν` clusters around 50–57, not 0.67 — off by ~75–85×, and even scaling by the target mass number (`0.67×A=56≈37.5`) doesn't close the gap. Units and normalization remain unconfirmed. `???` (units) |
| `sigmadiff`   | A differential cross section — plausibly `d²σ/dx dy` (unconfirmed formula), but the general idea that it depends on the DIS kinematic variables `x`/`y` is supported by data: it's **exactly `0.0` for all 8 inverse-muon-decay (`iresonance = 1005`) events, and those are the only 8 zero/negative `sigmadiff` values in the whole file** — consistent with a quantity that's undefined/left at 0 whenever `x`/`y` themselves are undefined (as they are for that lepton-target process; see `iresonance` above). `sigma` stays nonzero for those same events, and `sigma`/`sigmadiff` are correlated (r≈0.71) across the file, consistent with both being genuine cross-section-derived quantities. Not in the 2003 glossary (newer field). Exact formula/units still unconfirmed. `???` |
| `emfrac`      | Fraction of the hadronic shower energy that is electromagnetic (confirmed: "fraction of 'shower' energy which is EM", e.g. from π⁰ → γγ). Range [0, 1]. **Checked and kept**: does *not* match a naive (EM-particle energy / total final-state energy) computed from the particle table (median absolute difference 0.41, vs. a mean stored value of 0.18) — genuinely distinct information, likely computed relative to the pre-FSI hadronic system like `p4shw`, not the post-FSI final state. |
| ~~`vtxx`~~    | Dropped: interaction vertex x position, detector coordinates, meters (confirmed, `NuEvent.h`: "x vtx of neutrino interaction"). **Verified an exact duplicate for 100% of events** (zero difference), of `vtx0` on the particle-table row where `pdg == inu` and `status == 0` — the interacting neutrino always exists as a particle, so unlike `a`/`z`/`p4tgt` there's no channel-dependent fallback needed here. |
| ~~`vtxy`~~    | Dropped: same as `vtxx`, matches `vtx1` on the same row, 100% exact, zero gap. |
| ~~`vtxz`~~    | Dropped: same as `vtxx`, matches `vtx2` on the same row, 100% exact, zero gap. |
| `ndigu`       | Not in the 2003 glossary. A search engine's indexed snippet of the newer `Truth.h` doxygen page (page itself unreachable — see note above) describes it as "total number of digits in u-view". Consistent with the observed range (0–820, mean ~93). Not independently verified by reading the source. `???` (unverified) |
| `ndigv`       | Same source, "total number of digits in v-view". `???` (unverified) |
| `tphu`        | Same source: "summed pulse height u-view". Values run up to ~6×10⁵, much larger than the calibrated `pe0`/`pe1` scale, consistent with a pre-calibration/pre-electronics quantity, but the unit isn't given. `???` (unverified, units) |
| `tphv`        | Same source, "summed pulse height v-view". `???` (unverified, units) |
| ~~`p4neu0..3`~~ | Dropped: 4-momentum of the interacting neutrino. **Verified an exact duplicate**, for 119205/119205 (100%) of events, of the particle-table row where `pdg == inu` and `status == 0` — zero difference in any of the 4 components. Recover it with a filter/join instead of storing it twice. |
| `p4neunoosc0..3` | 4-momentum for the unoscillated-neutrino hypothesis. Unlike `p4neu`, this does **not** match any particle-table row (checked) — it's a hypothetical quantity that was never itself generated as a particle, so it's kept. Purpose relative to `p4neu` otherwise unconfirmed (see `inunoosc`). `???` |
| ~~`p4tgt0..3`~~ | Dropped: 4-momentum of the initial-state target (nucleon, nucleus, or electron, depending on channel). **Verified an exact duplicate for 100% of events, via a 3-way rule by `iresonance`**: QE/RES/DIS (`pdg == itg`, `status == 11`) → the struck nucleon with Fermi motion; CPP/coherent (`status == 0`, the large "nucleus" PDG code — same row `a`/`z` decode from) → the nucleus at rest; inverse muon decay (`pdg == 11`, `status == 0`) → the electron at rest. All three branches spot-checked with zero difference. |
| `p4shw0..3`   | Total 4-momentum of the final-state hadronic system ("shower"). **Checked and kept**: does *not* match summing the final-state (`status == 1`) hadrons in the particle table — likely because this is evaluated pre-FSI/intranuclear-cascade, while the particle table is post-FSI. Genuinely distinct information. `NuEvent.h` computes shower energy as `p4shw[3]` and, separately, as `y*p4neu[3]` — i.e. shower energy ≈ neutrino energy × inelasticity, as expected. |
| ~~`p4mu10..3`~~ | Dropped: 4-momentum of a final-state muon. **Verified against every nonzero event in this file (0 mismatches) by `validate_redundancy.py`** — but not by the rule an earlier hand-picked spot-check suggested. It's **not** "the first `pdg==±13,status==1` row in stack order, sign-flipped." The correct rule: find the `pdg==±13, status==1` particle whose momentum (`px,py,pz`) exactly matches `p4mu1`'s (momentum is never sign-flipped, so it's an unambiguous key) — that same match can be *either* charge, and stack order doesn't predict which. Its energy then gets negated iff its `pdg` is `+13` (the matter muon, μ⁻) rather than `-13` (μ⁺) — a documented convention (`NuEvent.h`: `p4mu1[3];//not proper p4: muon energy (+/- !!!)`), fully recoverable from the matched particle's own `pdg`. |
| ~~`p4mu20..3`~~ | Dropped: 4-momentum of a second final-state muon (if any, e.g. dimuon events). **Same rule and same verification as `p4mu1`** — match by momentum, sign the energy by the matched particle's own charge. An earlier version of this doc claimed `p4mu2` was never sign-flipped; that was wrong (based on a sample where the antimuon happened to fill the "mu2" role) — `validate_redundancy.py` confirmed 0 mismatches on all 100 nonzero events once the momentum-matching + per-particle-charge-sign rule replaced the stack-order assumption. |
| ~~`p4el10..3`~~ | Dropped: 4-momentum of a final-state electron. Same rule as `p4mu1` (`pdg==±11` instead of `±13`) — **verified, 0 mismatches on all 1460 nonzero events**. |
| ~~`p4el20..3`~~ | Dropped: 4-momentum of a second final-state electron. Same rule as `p4mu2` — **verified, 0 mismatches on all 40 nonzero events**. |
| ~~`p4tau0..3`~~ | Dropped: 4-momentum of a final-state tau. Same rule presumed by analogy (`pdg==±15`) — **not verifiable in this file**, which has zero `inu = ±16` ντ events (`p4tau` is constant `0.0` here, and `validate_redundancy.py` reports `SKIP` for it accordingly, not `PASS`). It's dropped for the same reason as the other lepton fields, not because it's assumed safe without evidence: if a file ever does have a ντ CC event, run `validate_redundancy.py` against it to check this row for real, since the pattern above was wrong once already. |

`p4*0..3` are the 4 components of each 4-vector, expanded into separate
columns: `*0`, `*1`, `*2` = momentum (px, py, pz) in GeV, `*3` = energy in
GeV.

### Particle-level (one row per truth particle)

Confirmed against the glossary's `NtpSRStdHep` entry.

| Column     | Meaning |
|------------|---------|
| `pdg`      | PDG code of the truth particle (confirmed: `IdHEP`, "particle ID (PDG standard)"). |
| `status`   | HEPEVT/GENIE status code (confirmed: `IstHEP`, "status code"; value meanings not spelled out — by HEPEVT convention `1` is typically a stable final-state particle). **Every single event has exactly one extra row with `status = 999`, `pdg = 0`, and a large nonphysical energy** — checked across all 119205 events, always exactly 1 such row each. This is a systematic per-event terminator/padding entry from the generator's particle stack, not a real particle. Filter it out (`status != 999`) before doing particle-level physics (e.g. counting final-state particles). `???` (exact origin of the value in its energy field) |
| `mass`     | Particle rest mass, GeV. |
| `p40..3`   | Particle 4-momentum: `p40,p41,p42` = (px, py, pz) GeV, `p43` = energy GeV. |
| `vtx0..3`  | Particle production 4-position. `vtx0,vtx1,vtx2` likely (x, y, z) in meters; `vtx3` likely time. Not spelled out in the glossary. `???` |

## Dropped branches

The source `NtpStRecord` has 29 top-level branch groups; only `stp`,
`mc`, `stdhep`, and a few header fields under `RecRecordImp<RecCandHeader>`
are used above, and only partially. Everything below is read from the
tree by nothing in `root_to_parquet.py` — never even touched, not just
filtered out afterwards. Grouped by why, not alphabetically:

- **MINOS reconstruction output** (fitted physics objects): `trk`
  (tracks), `shw` (showers), `slc` (slices), `evt` (reconstructed
  events), `clu` (clusters, upstream of track/shower fitting).
- **Reconstruction↔truth matching diagnostics** (purity/completeness of
  a reco object against MC truth — only meaningful once a reco object
  exists): `thevt`, `thtrk`, `thshw`, `thslc`, `thstp`.
- **Veto shield / cosmic-ray subsystem** (a separate detector system
  for tagging cosmic-ray muons, not the main tracking calorimeter):
  `crhdr`, `vetohdr`, `vetostp`, `vetoexp`.
- **Detector/DAQ hardware & data-quality status** (electronics health,
  not physics): `calstatus`, `detstatus`, `timestatus`, `dataquality`,
  `dmxstatus`, `deadchips`.
- **Simulation-pipeline & generator bookkeeping** (diagnostic counters,
  not physics truth): `detsim` (hits/digits surviving each simulation
  stage), `photon` (photon-counting QA), `mchdr` (generator
  codename/hostname/timestamp provenance).
- **Event/DAQ header summary counts** (mixes raw digit counts,
  timing/date, and reconstruction-derived object counts): `evthdr`.
- **Per-digit truth** (energy deposit ↔ originating particle, one tier
  below the `stdhep` particle stack we do keep): `digihit`.
- **Beamline/flux provenance** (parent-particle production kinematics
  and decay point from the NuMI beamline simulation, plus flux
  reweighting factors — useful for flux systematics studies, not kept
  here): `mc.flux.*` (~60 fields) and `mc.fluxwgt.*`.
- **Unused fields within groups we do partially keep**: in
  `RecRecordImp<RecCandHeader>`, everything except `fRun`/`fSubRun`/
  `fSnarl` — `fRunType`, `fErrorCode`, `fRemoteSpillType`, `fTrigSrc`,
  `fTimeFrame`, `fVldContext.{fDetector,fSimFlag,fTimeStamp}`, plus
  ROOT/job bookkeeping (`fJobHistory`, `fIsClearable`, `RecRecord`); in
  `mc`, the internal `mc.index` and `mc.stdhep[2]` (begin/end pointer
  into the particle stack — redundant since we already join by `entry`);
  in `stdhep`, `index`, `mc` (index back to the interaction record),
  `parent[2]`/`child[2]` (particle-genealogy links), `ndethit`/
  `dethit[2]` (which digits this particle deposited energy in — a
  truth↔reco association); in `stp`, `index`, `ndigit`, `demuxveto`,
  `pmtindex0`/`pmtindex1` (electronics channel address), `tpos`
  (transverse position — redundant with `plane`+`strip`).

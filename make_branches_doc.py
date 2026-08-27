#!/usr/bin/env python3
"""Generate BRANCHES.md from branches.txt plus the descriptions below.

The tick column has to agree with the manifest, and a hand-maintained table
of 755 rows would not stay in step with it. So the manifest is the input:
this script reads which branches are enabled and emits the document. Run it
after editing branches.txt.

    python make_branches_doc.py

Descriptions come from the MINOS LOON sources (the NtpSR*/NtpMC*/NtpTH*
class headers and the modules that fill them). Where a field's meaning could
not be established, it is marked ??? rather than guessed at.
"""

from __future__ import annotations

import re
from pathlib import Path

HERE = Path(__file__).parent


# --------------------------------------------------------------------------
# Sub-objects that several groups embed. Described once, applied per use.
# --------------------------------------------------------------------------

def vertex(noun: str) -> dict[str, str]:
    """NtpSRVertex: a point, a direction, and the errors on both."""
    return {
        "plane": f"Plane number at the {noun}.",
        "u": f"U coordinate at the {noun} [m].",
        "v": f"V coordinate at the {noun} [m].",
        "x": f"x at the {noun} [m].",
        "y": f"y at the {noun} [m].",
        "z": f"z at the {noun} [m], along the beam axis.",
        "t": f"Time at the {noun} [s].",
        "dcosu": f"Direction cosine along u at the {noun}.",
        "dcosv": f"Direction cosine along v at the {noun}.",
        "dcosx": f"Direction cosine along x at the {noun}.",
        "dcosy": f"Direction cosine along y at the {noun}.",
        "dcosz": f"Direction cosine along z at the {noun}.",
        "eu": f"Uncertainty on u at the {noun}.",
        "ev": f"Uncertainty on v at the {noun}.",
        "ex": f"Uncertainty on x at the {noun}.",
        "ey": f"Uncertainty on y at the {noun}.",
        "edcosu": f"Uncertainty on the u direction cosine at the {noun}.",
        "edcosv": f"Uncertainty on the v direction cosine at the {noun}.",
        "edcosx": f"Uncertainty on the x direction cosine at the {noun}.",
        "edcosy": f"Uncertainty on the y direction cosine at the {noun}.",
        "edcosz": f"Uncertainty on the z direction cosine at the {noun}.",
    }


def planes(what: str) -> dict[str, str]:
    """NtpSRPlane: how many planes the object spans, and where it starts."""
    return {
        "n": f"Planes hit by the {what}.",
        "nu": f"Of those, planes in the u view.",
        "nv": f"Of those, planes in the v view.",
        "beg": f"First plane of the {what}.",
        "begu": f"First u-view plane of the {what}.",
        "begv": f"First v-view plane of the {what}.",
        "end": f"Last plane of the {what}.",
        "endu": f"Last u-view plane of the {what}.",
        "endv": f"Last v-view plane of the {what}.",
        "ntrklike": "Planes carrying a track-like hit pattern.",
    }


def fiducial(which: str, whole_track: bool = False) -> dict[str, str]:
    """NtpSRFiducial: how far the point sits inside the fiducial volume.

    `fidall` is not a point at all: each of its fields is the minimum over
    the track start, the track end and every strip along the track, so it
    answers "how close did this track ever get to the boundary".
    """
    if whole_track:
        return {
            "dr": "Smallest radial distance to the fiducial boundary reached anywhere along the track — the minimum over the start, the end, and every strip on it [m].",
            "dz": "The same for the distance along z [m].",
            "trace": "The same for the path length remaining inside the fiducial volume [m].",
            "tracez": "The same measured along z [m].",
            "nplane": "The same for the number of planes to the boundary.",
        }
    return {
        "dr": f"Radial distance from the {which} to the fiducial boundary [m].",
        "dz": f"Distance along z from the {which} to the fiducial boundary [m].",
        "trace": f"Path length from the {which} to where it leaves the fiducial volume [m].",
        "tracez": "The same measured along z [m].",
        "nplane": f"Planes between the {which} and the fiducial boundary.",
    }


def pulseheight(what: str, strip: bool = True) -> dict[str, str]:
    """NtpSRPulseHeight, and NtpSRStripPulseHeight's three extra stages."""
    d = {
        "raw": f"Summed raw ADC over the {what}.",
        "siglin": "The same, linearity-corrected.",
        "sigcor": "The same, also corrected for fibre attenuation.",
        "pe": "The same, in photoelectrons.",
    }
    if strip:
        d |= {
            "sigmap": "The same, normalised by the strip-to-strip response map.",
            "mip": "The same, in units of a minimum-ionising particle.",
            "gev": "The same, converted to GeV.",
        }
    return d


def strip_arrays(obj: str) -> dict[str, str]:
    """The per-strip arrays that trk, shw and evt carry, one entry per strip."""
    return {
        "stp": f"Indices of the strips belonging to this {obj}, into the `stp` array.",
        "stpu": f"U position of the {obj} at each strip's plane [m].",
        "stpv": f"V position of the {obj} at each strip's plane [m].",
        "stpx": f"x position of the {obj} at each strip's plane [m].",
        "stpy": f"y position of the {obj} at each strip's plane [m].",
        "stpz": f"z position of the {obj} at each strip's plane [m].",
        "stpph0sigmap": "Attenuation-corrected pulse height at each strip, east end.",
        "stpph0mip": "The same in MIPs, east end.",
        "stpph0gev": "The same in GeV, east end.",
        "stpph1sigmap": "Attenuation-corrected pulse height at each strip, west end.",
        "stpph1mip": "The same in MIPs, west end.",
        "stpph1gev": "The same in GeV, west end.",
        "stpattn0c0": "Attenuation constant C0 from the fibre mapper, east end.",
        "stpattn1c0": "The same, west end.",
        "stpt0": "Hit time at each strip corrected for propagation delay, east end [s].",
        "stpt1": "The same, west end [s].",
        "stptcal0t0": "Calibration T0 offset applied at each strip, east end [s].",
        "stptcal1t0": "The same, west end [s].",
    }


def common(obj: str) -> dict[str, str]:
    """Fields every reconstructed object carries."""
    return {
        "index": f"Position of the {obj} in its array. Row order already carries it.",
        "slc": f"Index of the slice this {obj} belongs to.",
        "ndigit": f"Digits (single-end readouts) making up the {obj}.",
        "nstpcnt": f"Strips counted toward the {obj}, including repeats across planes.",
        "nstrip": f"Strips making up the {obj}.",
        "contained": f"Whether the {obj} is fully contained in the detector.",
    }


# --------------------------------------------------------------------------
# Per-group descriptions and blurbs.
# --------------------------------------------------------------------------

GROUPS: list[tuple[str, str, dict[str, str]]] = []


def group(name: str, blurb: str, fields: dict[str, str]) -> None:
    GROUPS.append((name, blurb, fields))


group("header", """Run, subrun and snarl identifiers, plus ROOT and job
bookkeeping. Only the four identifiers are archived.""", {
    "fUniqueID": "ROOT's `TObject` identifier. Internal to ROOT.",
    "fBits": "ROOT's `TObject` status bits. Internal to ROOT.",
    "fName": "ROOT object name.",
    "fTitle": "ROOT object title.",
    "fHeader.fVldContext.fDetector": "Which detector: Near, Far or CalDet. Constant within a file.",
    "fHeader.fVldContext.fSimFlag": "Data, Monte Carlo, or a reroot/daq variant. Constant within a file.",
    "fHeader.fVldContext.fTimeStamp.fSec": "Validity timestamp, whole seconds. Duplicated by the DAQ timing branches.",
    "fHeader.fVldContext.fTimeStamp.fNanoSec": "The nanosecond part of the same timestamp.",
    "fHeader.fRun": "DAQ run number.",
    "fHeader.fSubRun": "DAQ subrun number.",
    "fHeader.fRunType": "Run type: physics, calibration, test. Constant within a file.",
    "fHeader.fErrorCode": "Error code for the record.",
    "fHeader.fSnarl": "Snarl number within the run — the readout window this record describes.",
    "fHeader.fTrigSrc": "Trigger source bitmask. Duplicated by `dataquality.trigsource`.",
    "fHeader.fTimeFrame": "Time frame number. Duplicated by `timestatus.timeframe`.",
    "fHeader.fRemoteSpillType": "Spill type as sent from the accelerator. Duplicated by `dataquality.spilltype`.",
    "fHeader.fEvent": "Event number within the snarl. Constant `-1` in every file checked — the reconstruction chain assigns it.",
    "fIsClearable": "Whether the record may be cleared from memory. A framework flag.",
    "fJobHistory.fJobRecordMap": "Which modules processed the record. Job provenance, not event data.",
})

group("evthdr", """Per-snarl counts of what the reconstruction found, plus
the snarl's summed pulse height and date. All of it is either a count of
reconstructed objects or recoverable from the branches kept.""",
    {
        "ndigit": "Digits in the snarl.",
        "nstrip": "Strips hit in the snarl. Recoverable by counting `stp` rows.",
        "nslice": "Slices the reconstruction found.",
        "ncluster": "Clusters it found.",
        "ntrack": "Tracks it found.",
        "nshower": "Showers it found.",
        "nevent": "Reconstructed events it built.",
        "trigtime": "Trigger time for the snarl. Duplicated by `dataquality.trigtime`.",
        "litime": "Light-injection pulse time. Duplicated by `dataquality.litime`.",
    }
    | {f"ph.{k}": v for k, v in pulseheight("whole snarl", strip=False).items()}
    | {
        "planeall.n": "Planes in the snarl with at least one digit.",
        "planeall.nu": "Of those, planes in the u view.",
        "planeall.nv": "Of those, planes in the v view.",
        "planeall.beg": "Lowest plane carrying a digit.",
        "planeall.begu": "The same in the u view.",
        "planeall.begv": "The same in the v view.",
        "planeall.end": "Highest plane carrying a digit.",
        "planeall.endu": "The same in the u view.",
        "planeall.endv": "The same in the v view.",
        "plane.n": "Planes in the snarl's active range with a non-zero readout. The range is found by looking for four contiguous planes whose summed pulse height across both ends exceeds 3 pe; it runs from the first such group to the last, so isolated noise hits outside it do not count.",
        "plane.nu": "Of those, planes in the u view.",
        "plane.nv": "Of those, planes in the v view.",
        "plane.beg": "First plane of that range.",
        "plane.begu": "First u-view plane of it.",
        "plane.begv": "First v-view plane of it.",
        "plane.end": "Last plane of that range.",
        "plane.endu": "Last u-view plane of it.",
        "plane.endv": "Last v-view plane of it.",
    }
    | {
        "date.year": "Calendar year of the snarl.",
        "date.month": "Month.",
        "date.day": "Day.",
        "date.hour": "Hour.",
        "date.minute": "Minute.",
        "date.sec": "Second.",
        "date.utc": "Whether the above are UTC.",
    })

group("vetohdr", """Veto shield summary for a reconstructed track: what the
shield saw around the time the track crossed it. Indexed `[3]` by time
window relative to the track — `0` pre-trigger, `1` in time, `2`
post-trigger. All of it is keyed to a reconstructed track, and the shield's
actual hits are kept in `vetostp`.""", {
    "ndigit[3]": "Shield digits in each time window.",
    "nplank[3]": "Shield planks hit in each time window.",
    "adc[3]": "Summed shield ADC in each time window.",
    "dx[3]": "Closest approach between the track projection and a shield hit, per window [m].",
    "dxvetostp[3]": "Index into `vetostp` of the hit that achieved it.",
    "dcos": "One component of the track's direction where it was projected to the shield — which component depends on the shield section the crossing was in.",
    "projx": "x where the track was projected to cross the shield [m].",
    "projy": "y of the same projection [m].",
    "projz": "z of the same projection [m].",
    "exphits": "Shield crossings expected for this track.",
    "ishit": "Whether the shield was hit at all.",
    "found2sect": "Whether hits were found in two shield sections.",
})

group("crhdr", """Cosmic-ray direction for the snarl, derived from a
reconstructed track. Meaningless without the track it came from.""", {
    "zenith": "Zenith angle of the track [rad].",
    "azimuth": "Azimuth of the track [rad].",
    "ra": "Right ascension of the arrival direction [rad].",
    "rahourangle": "The same expressed as an hour angle.",
    "dec": "Declination of the arrival direction [rad].",
    "juliandate": "Julian date of the snarl.",
    "locsiderialtime": "Local sidereal time at the detector.",
})

group("dmxstatus", """Quality of demultiplexing — the step that resolves
which of the several strips sharing a Near Detector readout channel actually
fired. A reconstruction step, and its failure flags describe that step.""", {
    "ismultimuon": "Whether the snarl was tagged as multi-muon.",
    "nonphysicalfail": "Demultiplexing produced a non-physical result.",
    "validplanesfail": "Too few valid planes to demultiplex.",
    "vertexplanefail": "The vertex plane could not be resolved.",
    "ustrayplanes": "U-view planes whose hits could not be assigned.",
    "vstrayplanes": "The same in the v view.",
    "uvalidplanes": "U-view planes successfully demultiplexed.",
    "vvalidplanes": "The same in the v view.",
    "avgtimeoffset": "Mean timing offset applied during demultiplexing [s].",
})

group("detstatus", """Magnet and high-voltage state. Needed to interpret
momentum and charge sign, so all of it is archived.""", {
    "coilstatus": "**Always `0`.** The filler sets it unconditionally and comments that the variable is deprecated; `dcscoilstatus` is the one that carries the real state. Archived anyway, since it costs nothing and an empty field is itself a fact about the file.",
    "dcscoilstatus": "Magnet coil state from the slow-control system: OK or bad, with a reverse-polarity bit OR'd in. At the Far Detector the two supermodules must agree, or it is set to unknown.",
    "coilcurrent1": "Coil current, which sets the field and hence the momentum and charge-sign measurement [A]. Supermodule 1 at the Far Detector, the single coil at the Near. `-999.9` when unknown.",
    "coilcurrent2": "Supermodule 2's coil current [A]. Far Detector only; `-999.9` otherwise.",
    "dbuhvstatus": "Photomultiplier high-voltage status. `-1` when unknown.",
    "coldchips1": "Front-end chips below high-voltage threshold in supermodule 1.",
    "coldchips2": "The same for supermodule 2.",
})

group("timestatus", """Absolute timing for the snarl. Archived: without it
a snarl cannot be placed on the beam clock.""", {
    "sgate_10mhz": "Spill gate counted on the 10 MHz clock.",
    "sgate_53mhz": "Spill gate counted on the 53 MHz clock.",
    "rollover_53mhz": "How many times the 53 MHz counter has wrapped.",
    "rollover_last_53mhz": "The rollover count at the previous snarl, so an interval spanning a wrap can be unwrapped.",
    "crate_t0_ns": "Crate time zero [ns].",
    "timeframe": "Time frame number.",
})

group("calstatus", """Calibration constants in force for the snarl.""", {
    "gevpermip": "Constant converting MIP-equivalent signal to GeV. Needed to turn the `pe` values into energy.",
})

group("dataquality", """Beam spill, trigger and DAQ state for the snarl,
including the light-injection pulser. Unset (`-1`) for Monte Carlo. All of
it is archived: it describes the conditions the data was taken under, and
nothing else records them.""", {
    "trigsource": "What triggered the readout, as a bitmask.",
    "trigtime": "Trigger time.",
    "errorcode": "DAQ error code for the snarl.",
    "cratemask": "How many readout crates were active; 16 is a full Far Detector readout.",
    "pretrigdigits": "Digits recorded before the trigger.",
    "posttrigdigits": "Digits recorded after it.",
    "snarlmultiplicity": "Interactions in this snarl.",
    "spillstatus": "Beam spill status.",
    "spilltype": "Beam spill type.",
    "spilltimeerror": "Error on the spill timing.",
    "litrigger": "Whether this snarl was a light-injection trigger.",
    "litime": "Light-injection pulse time.",
    "lisubtractedtime": "The same with the pedestal offset removed.",
    "lirelativetime": "Time relative to the light-injection trigger.",
    "licalibpoint": "Which point in the light-injection calibration sequence.",
    "licalibtype": "Which calibration type was running.",
    "libox": "Which pulser box fired.",
    "liled": "Which LED within that box.",
    "lipulseheight": "Pulser amplitude setting.",
    "lipulsewidth": "Pulser width setting.",
    "coldchips": "Front-end chips reading nothing.",
    "hotchips": "Chips firing far above their expected rate.",
    "busychips": "Chips saturated by readout load.",
    "readouterrors": "Readout errors in the snarl.",
    "dataqualityword": "Packed overall data-quality flag for the snarl.",
})

group("mchdr", """Which generator produced the file, and on what machine.
Job provenance rather than event data, and constant within a file.""", {
    "error": "Generator error code.",
    "nmc": "Interaction records in the file.",
    "nstdhep": "Truth particle records in the file.",
    "ndigihit": "Per-digit truth records in the file.",
    "geninfo.time.fSec": "When the generator ran, whole seconds.",
    "geninfo.time.fNanoSec": "The nanosecond part.",
    "geninfo.codename": "Generator codename.",
    "geninfo.hostname": "Machine it ran on.",
})

group("photon", """Counters from the optical simulation: how much light was
made and how much was thrown away. Describes how the simulation ran, not
what physically happened — the resulting light is already in `stp.ph*`.
Simulating every photon is too slow, so a fraction is tracked and scaled up;
both the scaled and unscaled counts are here.""", {
    "hitsDiscardedGeom": "Hits dropped because they fell outside the simulated geometry.",
    "hitsDiscardedBad": "Hits dropped as unusable.",
    "totalHits": "Scintillator hits simulated.",
    "totalStripsHit": "Distinct strips those hits touched.",
    "bluePhotons": "Blue scintillation photons produced, prescale-corrected.",
    "greenPhotons": "Green photons re-emitted by the wavelength-shifting fibre, prescale-corrected.",
    "bluePhotons_nonprescaled": "Blue photons actually tracked, before the prescale correction.",
    "greenPhotons_nonprescaled": "The same for green photons.",
    "totalPE": "Photoelectrons produced.",
    "totalPixels": "Photomultiplier pixels they landed on.",
    "totalHitEnergy": "Energy deposited by the simulated hits [GeV].",
    "energyDiscardedGeom": "Energy in the hits dropped on geometry.",
    "energyDiscardedBad": "Energy in the hits dropped as unusable.",
})

group("detsim", """Counters from the electronics simulation: how many digits
survived each stage of the front-end and DAQ. A record of the simulation's
behaviour rather than the event's.""", {
    "timeShift": "Time offset applied to the simulated snarl [s].",
    "nPE": "Photoelectrons entering the electronics simulation.",
    "hitPixels": "Photomultiplier pixels hit.",
    "hitPixelsWithXtalk": "The same including cross-talk between pixels.",
    "digitsAfterFETrigger": "Digits surviving the front-end trigger.",
    "digitsAfterSpars": "Digits surviving sparsification.",
    "digitsAfterDaqTrigger": "Digits surviving the DAQ trigger.",
    "totalPE": "Summed photoelectrons.",
    "totalCharge": "Summed charge [ADC].",
    "adcsAfterFETrigger": "Summed ADC surviving the front-end trigger.",
    "adcsAfterSpars": "Summed ADC surviving sparsification.",
    "adcsAfterDaqTrigger": "Summed ADC surviving the DAQ trigger.",
    "bigSnarl": "Whether the simulated snarl was flagged as unusually large.",
    "snarls": "Snarls the simulation split this event into.",
    "snarlDigits": "Digits in each of those snarls.",
    "snarlTrigger": "Trigger decision for each of them.",
    "snarlAdcs": "Summed ADC in each of them.",
})

group("vetostp", """Raw hits in the veto shield — the scintillator blanket
over the Far Detector used to tag entering cosmic-ray muons. A measurement,
so it is archived; the summary keyed to reconstructed tracks (`vetohdr`,
`vetoexp`) is not. Fields indexed `[2]` are one value per strip end.""", {
    "index": "Position of the hit in its array. Row order already carries it.",
    "ndigit": "Digits on this shield strip.",
    "pln": "Shield plane.",
    "plank": "Shield plank within the plane.",
    "x": "Position [m].",
    "y": "Position [m].",
    "z[2]": "Position at each strip end [m].",
    "adc[2]": "Raw pulse height at each end [ADC].",
    "pmtindex[2]": "Which photomultiplier channel each end is read out by.",
    "pmtpixel[2]": "Which pixel on that photomultiplier.",
    "wlspigtail[2]": "Length of wavelength-shifting fibre from the strip end [m].",
    "clearlen[2]": "Length of clear fibre from there to the photomultiplier [m].",
    "time[2]": "Calibrated hit time at each end [s].",
    "timeraw[2]": "The same before timing calibration [s].",
})

group("vetoexp", """Where a *reconstructed track* was expected to cross the
shield — a projection, not a measurement. Meaningless without the track, and
the shield's actual hits are kept in `vetostp`.""", {
    "index": "Position of the record in its array.",
    "plane": "Shield plane the crossing was expected in.",
    "plank": "Shield plank it was expected in.",
    "stripinplank[2]": "Which strips within that plank, one per end.",
    "projx": "x of the expected crossing [m].",
    "projy": "y of the expected crossing [m].",
    "projz": "z of the expected crossing [m].",
    "centerdis": "Distance from the plank centre to the expected crossing [m].",
    "isfound": "Whether a shield hit was actually found there.",
    "stripdigit": "Index of the digit found, if any.",
})

group("deadchips", """Map of dead electronics channels. Genuinely useful for
efficiency work, but **empty in every file checked**, so there is nothing to
archive. `check_exclusions.py` refuses any file where that stops being
true.""", {
    "channelid": "Identifier of the dead channel.",
    "plane0": "First plane the channel serves.",
    "plane1": "Last plane it serves.",
    "shield": "Whether the channel belongs to the veto shield.",
    "errorcode": "Why the channel was marked dead.",
    "status": "Status flag for the channel.",
})

group("stp", """Digitised strip hits: the detector's actual image of the
event, and the core of the archive. One row per strip that fired.""", {
    "index": "Position of the strip in its array. Row order already carries it.",
    "planeview": "Which stereo view the strip belongs to: `2` = U, `3` = V. Fixed by `plane`.",
    "ndigit": "How many ends of the strip fired, only ever 1 or 2. The same information as which of `time0`/`time1` holds the `-999999` sentinel.",
    "demuxveto": "Output of demultiplexing, which resolves the Near Detector's several-strips-per-channel readout. Constant `0` in the Far Detector files here, and a reconstruction step in any case.",
    "strip": "Strip number within the plane, 0–191.",
    "plane": "Plane number along the beam axis, 1–485 in a Far Detector file.",
    "tpos": "Transverse position of the strip [m]. Fixed by `plane` and `strip` together.",
    "z": "Position along the beam axis [m]. One fixed z per plane, so a lookup on `plane`.",
    "pmtindex0": "Which photomultiplier channel the east end is wired to. Fixed per strip, and a property of the readout map rather than the event.",
    "pmtindex1": "The same for the west end.",
    "time0": "Charge-weighted mean hit time, east end [s], relative to the trigger. `-999999` means that end saw no signal.",
    "time1": "The same, west end.",
    "ph0.raw": "Raw ADC, east end — what the electronics recorded, before any correction.",
    "ph0.siglin": "The same, linearity-corrected but not yet attenuation-corrected. Both endpoints of that chain are kept and the factor between them is recoverable from the pair, so the middle step adds little.",
    "ph0.sigcor": "Attenuation-normalised strip response, east end.",
    "ph0.pe": "Calibrated light yield, east end [photoelectrons].",
    "ph1.raw": "Raw ADC, west end.",
    "ph1.siglin": "The intermediate stage, west end.",
    "ph1.sigcor": "Attenuation-normalised strip response, west end.",
    "ph1.pe": "Calibrated light yield, west end [photoelectrons].",
})

group("slc", """Reconstructed slices — the first stage of reconstruction,
grouping a snarl's hits into what looks like separate interactions.""",
    common("slice") | {"stp": "Indices of the strips in this slice, into the `stp` array."}
    | {f"ph.{k}": v for k, v in pulseheight("slice", strip=False).items()}
    | {f"plane.{k}": v for k, v in planes("slice").items()})

group("clu", """Clusters — groups of hits within one view, upstream of track
and shower fitting.""",
    common("cluster") | {
        "planeview": "Which view the cluster lies in: `2` = U, `3` = V.",
        "nplane": "Planes the cluster spans.",
        "begplane": "First plane of the cluster.",
        "endplane": "Last plane of the cluster.",
        "id": "Cluster type identifier. **???** — no code that sets it appears in the LOON sources available here.",
        "stp": "Indices of the strips in this cluster, into the `stp` array.",
        "probem": "Used elsewhere as an electromagnetic-likelihood cut (`probem > 0.2`), so it reads as the probability the cluster is electromagnetic. **???** — nothing in the available sources sets it.",
        "zvtx": "z of the cluster's start [m]. **???**",
        "tposvtx": "Transverse position of the cluster's start [m]. **???**",
        "slope": "Slope of the cluster in the transverse-versus-z plane. **???**",
        "avgdev": "Mean deviation of the hits from that slope — how straight the cluster is. **???**",
    }
    | {f"ph.{k}": v for k, v in pulseheight("cluster").items()})

group("shw", """Reconstructed showers — hadronic or electromagnetic energy
deposits, fitted from the clusters.""",
    common("shower") | strip_arrays("shower") | {
        "nUcluster": "Clusters in the u view making up the shower.",
        "nVcluster": "The same in the v view.",
        "ncluster": "Clusters in total.",
        "clu": "Indices of those clusters, into the `clu` array.",
    }
    | {f"ph.{k}": v for k, v in pulseheight("shower").items()}
    | {
        "shwph.linCCgev": "Shower energy under the charged-current hypothesis, linear calibration [GeV].",
        "shwph.wtCCgev": "The same with the weighted calibration [GeV].",
        "shwph.linNCgev": "Shower energy under the neutral-current hypothesis, linear calibration [GeV].",
        "shwph.wtNCgev": "The same with the weighted calibration [GeV].",
        "shwph.EMgev": "Shower energy under an electromagnetic hypothesis [GeV].",
    }
    | {f"plane.{k}": v for k, v in planes("shower").items()}
    | {f"vtx.{k}": v for k, v in vertex("shower start").items()}
    | {
        "sss.nTrkLikeU": "Track-like sub-showers found in the u view.",
        "sss.nTrkLikeV": "The same in the v view.",
        "sss.nRecoTrkU": "Reconstructed tracks overlapping the shower in the u view.",
        "sss.nRecoTrkV": "The same in the v view.",
        "sss.phTrkLikeU": "Pulse height in the track-like part, u view.",
        "sss.phTrkLikeV": "The same in the v view.",
        "sss.probEMU": "Likelihood the u-view shower is electromagnetic.",
        "sss.probEMV": "The same in the v view.",
        "sss.compactU": "Compactness of the shower in the u view: `1` for a single cluster, otherwise a derived measure of how tightly the clusters group.",
        "sss.compactV": "The same in the v view.",
    })

group("trk", """Reconstructed tracks — fitted muon trajectories, and the
largest group in the file. All of it is reconstruction output.""",
    common("track") | strip_arrays("track") | {
        "stpds": "Path length from the track end to each strip [m].",
        "stpfit": "Whether each strip was used in the track fit.",
        "stpfitchi2": "Contribution of each strip to the fit χ².",
        "stpfitprechi2": "The same before the final iteration.",
        "stpfitqp": "Fitted charge-over-momentum at each strip [e/GeV].",
        "ds": "Track path length [m].",
        "range": "Range from the track start to its end [g/cm²].",
        "cputime": "CPU seconds spent reconstructing the track.",
    }
    | {f"ph.{k}": v for k, v in pulseheight("track").items()}
    | {f"plane.{k}": v for k, v in planes("track").items()}
    | {f"vtx.{k}": v for k, v in vertex("track start").items()}
    | {f"end.{k}": v for k, v in vertex("track end").items()}
    | {f"lin.{k}": v for k, v in vertex("track start from the linear fit").items()}
    | {f"fidvtx.{k}": v for k, v in fiducial("track start").items()}
    | {f"fidend.{k}": v for k, v in fiducial("track end").items()}
    | {f"fidall.{k}": v for k, v in fiducial("track", whole_track=True).items()}
    | {
        "time.ndigit": "Digits used in the track's timing fit.",
        "time.chi2": "χ² of that fit.",
        "time.u0": "Fitted time at the track start, u view [s].",
        "time.u1": "Fitted time at the track end, u view [s].",
        "time.v0": "The same at the start, v view [s].",
        "time.v1": "The same at the end, v view [s].",
        "time.cdtds": "The fitted time gradient multiplied by c, i.e. 1/β — `1` for a particle at the speed of light, larger for a slower one. Note it is the *inverse* of speed, and it is taken as an absolute value, so unlike `dtds` it carries no direction.",
        "time.du": "Path length spanned in the u view [m]. **???**",
        "time.dv": "The same in the v view [m]. **???**",
        "time.dtds": "Fitted time gradient along the track [s/m]; its sign gives the direction of travel.",
        "time.t0": "Fitted time at the track start [s].",
        "time.forwardRMS": "Timing residual RMS assuming the track ran forwards.",
        "time.forwardNDOF": "Degrees of freedom in that fit.",
        "time.backwardRMS": "The same assuming it ran backwards; comparing the two gives the direction.",
        "time.backwardNDOF": "Degrees of freedom in the backward fit.",
        "momentum.range": "Momentum from range, valid only for a stopping track [GeV/c].",
        "momentum.qp": "Fitted charge over momentum [e/GeV].",
        "momentum.eqp": "Uncertainty on it.",
        "momentum.best": "The better of the range and curvature estimates [GeV/c].",
        "momentum.qp_rangebiased": "Charge over momentum from a fit seeded with the range estimate.",
        "momentum.eqp_rangebiased": "Uncertainty on that.",
        "fit.pass": "Whether the track fit converged.",
        "fit.ndof": "Degrees of freedom in the fit.",
        "fit.niterate": "Iterations it took.",
        "fit.nswimfail": "How many times swimming the track through the field failed.",
        "fit.chi2": "χ² of the fit.",
        "fit.cputime": "CPU seconds spent on it.",
        "fit.bave": "Mean magnetic field along the track [T].",
        "cr.zenith": "Zenith angle of the track [rad].",
        "cr.azimuth": "Azimuth of the track [rad].",
        "cr.ra": "Right ascension of the arrival direction [rad].",
        "cr.rahourangle": "The same as an hour angle.",
        "cr.dec": "Declination of the arrival direction [rad].",
        "cr.juliandate": "Julian date of the snarl.",
        "cr.locsiderialtime": "Local sidereal time at the detector.",
    })

group("evt", """Reconstructed events — tracks and showers assembled into a
neutrino interaction candidate. Dropped in full, `evt.vtx.*` included: it is
what MINOS concluded from the data, and an archive should hold what the
detector recorded instead. Analyses do use the vertex for fiducial cuts, so
this is the exclusion most likely to be questioned.""",
    common("event") | {
        "stp": "Indices of the strips in this event, into the `stp` array.",
        "stpph0sigmap": "Attenuation-corrected pulse height at each strip, east end.",
        "stpph0mip": "The same in MIPs, east end.",
        "stpph0gev": "The same in GeV, east end.",
        "stpph1sigmap": "Attenuation-corrected pulse height at each strip, west end.",
        "stpph1mip": "The same in MIPs, west end.",
        "stpph1gev": "The same in GeV, west end.",
        "nshower": "Showers in the event.",
        "shw": "Indices of those showers, into the `shw` array.",
        "ntrack": "Tracks in the event.",
        "trk": "Indices of those tracks, into the `trk` array.",
        "primshw": "Index of the primary shower.",
        "primtrk": "Index of the primary track.",
    }
    | {f"ph.{k}": v for k, v in pulseheight("event").items()}
    | {f"plane.{k}": v for k, v in planes("event").items()}
    | {f"vtx.{k}": v for k, v in vertex("event vertex").items()}
    | {f"end.{k}": v for k, v in vertex("event end").items()}
    | {
        "bleach.lateBucketPHFraction": "Fraction of the event's pulse height arriving in late time buckets. Computed alongside a photomultiplier afterpulsing prediction, which is what it is there to catch.",
        "bleach.timeWeightedPHFraction": "The same fraction weighted by arrival time.",
        "bleach.straightPHFraction": "Fraction of pulse height lying along a straight path through the event.",
        "bleach.fixedWindowPH": "Pulse height inside a fixed time window.",
        "bleach.eventDuration": "How long the event's hits span [s].",
        "win.begplane": "First plane of the event's window: the event's own first plane, extended upstream by a fixed number of planes.",
        "win.endplane": "Last plane of it, extended downstream the same way.",
        "win.begtime": "Start of the window [s]: the event's start time, extended by a fixed offset.",
        "win.endtime": "End of the window [s], extended the same way.",
        "win.totalQ": "Summed attenuation-corrected charge of every strip in that window.",
        "win.specQ": "Near Detector only: the part of that charge from planes in the spectrometer section, downstream of the fully-instrumented region. Zero at the Far Detector.",
        "win.pinstQ": "Near Detector only: the part from the partially-instrumented strip range. Zero at the Far Detector.",
        "win.utotalQ": "The same as `totalQ` but counting only strips left unassociated with any reconstructed object — the `u` prefix is *unassociated*, not the u view.",
        "win.uspecQ": "`specQ` restricted to those unassociated strips.",
        "win.upinstQ": "`pinstQ` restricted to those unassociated strips.",
    })

group("mc", """Per-event interaction truth: what the generator says actually
happened. Kinematics, channel and the truth 4-vectors. The bulk of the
archive's simulation content, together with `stdhep`.""", {
    "index": "Position of the interaction record in its array. Row order already carries it.",
    "stdhep[2]": "Index range pointing into `stdhep`; both are already joined per event.",
    "inu": "PDG code of the neutrino flavour. Duplicates the code on the `stdhep` initial-state neutrino row (`IstHEP == 0`).",
    "inunoosc": "PDG code of the neutrino flavour at production. Equals `inu` throughout the file checked, but would differ in a sample where flavours are swapped.",
    "itg": "PDG code of the struck target: `2212`/`2112` nucleons, a large nucleus code for coherent events, `11` for inverse muon decay.",
    "iboson": "Should carry the exchange boson's PDG code (Z⁰ = 23, W⁺ = 24) but holds a constant sentinel in every file checked.",
    "iresonance": "Channel: `1001` QE, `1002` resonance, `1003` DIS, `1004` coherent pion, `1005` inverse muon decay.",
    "iaction": "`0` = NC, `1` = CC. Derivable as \"a final-state charged lepton exists\", but only to 99.993% — not exactly — so it is kept.",
    "istruckq": "PDG id of the struck quark: `0` none (non-DIS), `1` d, `2` u.",
    "iflags": "Hadronisation model: `0` non-DIS, `1` old KNO, `2` modified KNO, `3` charm, `11`/`12`/`13` JETSET string/cluster/other.",
    "ndigu": "Raw digits in the u view truth-matched to this interaction. Kept because it is *not* recoverable: it matches the naive per-view hit count from `stp` in only ~3% of events.",
    "ndigv": "The same in the v view. A digit touching both views is counted in u only.",
    "tphu": "Summed pulse height over those u-view digits — **raw ADC, pedestal-subtracted**.",
    "tphv": "The same in the v view.",
    "a": "Mass number of the target nucleus. Encoded in the PDG code of the `stdhep` nucleus row; hydrogen when there is no such row.",
    "z": "Its atomic number, from the same code.",
    "sigma": "Cross section for this interaction. Units unconfirmed — passed through unchanged from NEUGEN. **???**",
    "sigmadiff": "Differential cross section. Same issue. **???**",
    "x": "Bjorken x. Derivable as `−q2 / (2 p_tgt·q)` for QE, resonance and DIS, but the nucleon-level relation breaks entirely for coherent events, where the target is the whole nucleus, and for inverse muon decay. Kept rather than made every reader re-derive it and get those channels wrong.",
    "y": "Inelasticity y. The invariant form, not the lab-frame ratio, which ignores Fermi motion. Derivable in principle, but breaks for inverse muon decay, so it is kept.",
    "q2": "Four-momentum transfer squared [GeV²]. Derivable as `(p4neu − p4lep)²`, except for coherent events and inverse muon decay; kept for the same reason as `x`.",
    "w2": "Hadronic invariant mass squared [GeV²]. Derivable as `(p_tgt + q)²`, with the same coherent and inverse-muon-decay exceptions; kept.",
    "emfrac": "Electromagnetic fraction of the hadronic shower energy, evaluated before final-state interactions. Kept because it is not recoverable: it differs from the naive fraction computed from the truth particles by 0.41 median absolute difference, against a mean value of 0.18.",
    "vtxx": "Interaction vertex x [m]. Duplicates `vtx[0]` on the `stdhep` neutrino row.",
    "vtxy": "Interaction vertex y [m]. Duplicates its `vtx[1]`.",
    "vtxz": "Interaction vertex z [m]. Duplicates its `vtx[2]`.",
    "p4neu[4]": "Neutrino 4-momentum. Duplicates `p4` on the `stdhep` neutrino row.",
    "p4neunoosc[4]": "Neutrino 4-momentum under the unoscillated hypothesis.",
    "p4tgt[4]": "Target 4-momentum. Duplicates `p4` on the `stdhep` struck-nucleon or nucleus row.",
    "p4shw[4]": "Final-state hadronic system. Not the sum of the `stdhep` hadrons: it is evaluated before final-state interactions — the rescattering of products inside the struck nucleus — whereas `stdhep` records the particles that emerge after them. Checked against both the hadron sum and `p4neu + p4tgt − p4lep`: 100% mismatch with each, so it carries information nothing else does.",
    "p4mu1[4]": "Primary muon 4-momentum. Duplicates a `stdhep` lepton row, but with the energy component's sign flipped for the matter lepton.",
    "p4mu2[4]": "Second muon, where the event has one. Duplicates another `stdhep` muon row.",
    "p4el1[4]": "Electron 4-momentum. Duplicates an `stdhep` electron row.",
    "p4el2[4]": "Second electron. Duplicates another `stdhep` electron row. Rare — the test file has exactly one event with a non-zero value, and the rule is verified against it.",
    "p4tau[4]": "Tau 4-momentum. Duplicates an `stdhep` tau row, and never non-zero in any file checked — no ν_τ events.",
    # ---- flux, from the gnumi beam simulation ----
    "flux.index": "Position of the flux record in its array.",
    "flux.fluxrun": "gnumi beam-simulation run number.",
    "flux.fluxevtno": "Event number within that run.",
    "flux.ndxdz": "Neutrino dx/dz slope at the decay point.",
    "flux.ndydz": "Neutrino dy/dz slope at the decay point.",
    "flux.npz": "Neutrino momentum along z at the decay point [GeV/c].",
    "flux.nenergy": "Neutrino energy at the decay point [GeV].",
    "flux.ndxdznear": "Neutrino dx/dz slope toward the Near Detector centre.",
    "flux.ndydznear": "Neutrino dy/dz slope toward the Near Detector centre.",
    "flux.nenergynear": "Energy it would have at the Near Detector centre [GeV].",
    "flux.nwtnear": "Weight turning generated events into a flux prediction at the Near Detector.",
    "flux.ndxdzfar": "Neutrino dx/dz slope toward the Far Detector centre.",
    "flux.ndydzfar": "Neutrino dy/dz slope toward the Far Detector centre.",
    "flux.nenergyfar": "Energy it would have at the Far Detector centre [GeV].",
    "flux.nwtfar": "The same weight for the Far Detector. With the near fields, this pair is what the near/far extrapolation is built from.",
    "flux.norig": "Marked *(ignore)* in the gnumi source.",
    "flux.ndecay": "Tag identifying the decay mode that produced the neutrino.",
    "flux.ntype": "Neutrino type, as a PDG code.",
    "flux.vx": "x of the decay vertex [cm].",
    "flux.vy": "y of the decay vertex [cm].",
    "flux.vz": "z of the decay vertex [cm].",
    "flux.pdpx": "Parent px at the decay point [GeV/c].",
    "flux.pdpy": "Parent py at the decay point [GeV/c].",
    "flux.pdpz": "Parent pz at the decay point [GeV/c].",
    "flux.ppdxdz": "Parent dx/dz slope at the decay point.",
    "flux.ppdydz": "Parent dy/dz slope at the decay point.",
    "flux.pppz": "Parent pz at the decay point [GeV/c].",
    "flux.ppenergy": "Parent energy at the decay point [GeV].",
    "flux.ppmedium": "GEANT medium the parent was produced in.",
    "flux.ptype": "Parent particle type, as a PDG code.",
    "flux.ppvx": "x of the parent's production vertex [cm].",
    "flux.ppvy": "y of the parent's production vertex [cm].",
    "flux.ppvz": "z of the parent's production vertex [cm].",
    "flux.muparpx": "Where the parent was a muon, its own parent's px [GeV/c].",
    "flux.muparpy": "The same, py.",
    "flux.muparpz": "The same, pz.",
    "flux.mupare": "The same, energy [GeV].",
    "flux.necm": "Neutrino energy in the parent's centre-of-mass frame [GeV].",
    "flux.nimpwt": "Importance weight from the simulation.",
    "flux.xpoint": "Marked *(unused)* in the gnumi source.",
    "flux.ypoint": "Marked *(unused)* in the gnumi source.",
    "flux.zpoint": "Marked *(unused)* in the gnumi source.",
    "flux.tvx": "x where the parent left the target [cm].",
    "flux.tvy": "y where the parent left the target [cm].",
    "flux.tvz": "z where the parent left the target [cm].",
    "flux.tpx": "Parent px at target exit [GeV/c].",
    "flux.tpy": "Parent py at target exit [GeV/c].",
    "flux.tpz": "Parent pz at target exit [GeV/c].",
    "flux.tptype": "Type of that parent, as a PDG code.",
    "flux.tgen": "Which generation of the hadronic cascade the parent belongs to. This is what hadron-production reweighting needs.",
    "flux.tgptype": "Type of the particle that came off the target, as a PDG code.",
    "flux.tgppx": "Its px at the interaction [GeV/c].",
    "flux.tgppy": "Its py [GeV/c].",
    "flux.tgppz": "Its pz [GeV/c].",
    "flux.tprivx": "x of the primary proton's interaction vertex [cm].",
    "flux.tprivy": "y of it [cm].",
    "flux.tprivz": "z of it [cm].",
    "flux.beamx": "x where the primary proton entered [cm].",
    "flux.beamy": "y where it entered [cm].",
    "flux.beamz": "z where it entered [cm].",
    "flux.beampx": "Primary proton px [GeV/c].",
    "flux.beampy": "Primary proton py [GeV/c].",
    "flux.beampz": "Primary proton pz [GeV/c].",
    "fluxwgt.index": "Position of the flux-weight record in its array.",
    "fluxwgt.beam[33]": "Name of the beam configuration the weight applies to, as 33 characters.",
    "fluxwgt.version": "Version of the reweighting that produced it.",
    "fluxwgt.weight": "Flux weight for this event.",
    "fluxwgt.weighterr": "Uncertainty on that weight.",
})

group("stdhep", """The truth particle stack, in HEPEVT convention: one row
per particle, variable length per event. The incoming neutrino, the struck
target, and everything in the final state.

Every event carries one `IstHEP == 999`, `IdHEP == 0` row — a *rootino*, a
null placeholder never tracked.""", {
    "index": "Position of the particle in its array. Row order already carries it.",
    "mc": "Index of the interaction this particle belongs to; the reverse of `mc.stdhep[2]`, and both are already joined per event.",
    "parent[2]": "Indices of the particle's parents.",
    "child[2]": "Indices of its daughters.",
    "IstHEP": "HEPEVT status code: `0` initial state, `1` final state, `11` struck nucleon.",
    "IdHEP": "PDG code.",
    "mass": "Rest mass [GeV]. A pure function of `IdHEP` in the files checked (192 distinct codes, none with two masses), so nominally redundant — but recovering it needs an **external PDG mass table**, not just other columns of this file. An archive that cannot be read without a second reference is not self-contained, so it stays.",
    "p4[4]": "4-momentum: `(px, py, pz)` then energy, all GeV.",
    "vtx[4]": "Production 4-position: `(x, y, z)` in metres, then time in seconds.",
    "ndethit": "How many digits the particle deposited energy in.",
    "dethit[2]": "The particle's first and last hit — plane, strip, position and momentum for each. Left out because uproot reads it as a C++ struct array, which would need unpacking work. **This is the one exclusion that loses information with no way to recover it**, and is worth revisiting.",
})

group("digihit", """The finest-grained truth there is: per particle per
strip, where it entered and left and how much it deposited. **Empty in every
file checked**, so there is nothing to archive. `check_exclusions.py`
refuses any file where that stops being true.""", {
    "index": "Position of the record in its array.",
    "planeview": "View of the strip: `2` = U, `3` = V.",
    "strip": "Strip number within the plane.",
    "plane": "Plane number.",
    "trkId": "Simulation track id of the particle that deposited the energy.",
    "pId": "PDG code of that particle.",
    "t0": "Time entering the scintillator [s].",
    "x0": "x entering [m].",
    "y0": "y entering [m].",
    "z0": "z entering [m].",
    "t1": "Time leaving [s].",
    "x1": "x leaving [m].",
    "y1": "y leaving [m].",
    "z1": "z leaving [m].",
    "dS": "Path length through the scintillator [m].",
    "dE": "Energy deposited [GeV].",
    "pE": "Photoelectrons the deposit produced.",
    "failbits": "Flags recording problems with the hit.",
})

group("thstp", """Truth for each strip in `stp`: which simulated particles
deposited energy there, and in what proportion. Exactly one record per hit,
so it lines up row-for-row with the hit variables. Archived, because it
labels data that is itself archived.""", {
    "index": "Position of the record in its array. Row order already carries it.",
    "neumc": "Index of the interaction (`mc` record) responsible for the strip.",
    "nneu": "How many interactions contributed to it.",
    "sigflg": "Signal flag. **???** — nothing in the available sources sets it, so the encoding is unconfirmed.",
    "stdhep[3]": "Up to three contributing `stdhep` particle indices.",
    "phfrac[3]": "Fraction of the strip's pulse height from each of those.",
})


def truth_match(obj: str, extra: dict[str, str] | None = None) -> dict[str, str]:
    d = {
        "index": "Position of the record in its array.",
        "neumc": f"Index of the interaction the {obj} was matched to.",
        "neustdhep": f"Index of the truth particle it was matched to.",
        "purity": f"Fraction of the {obj}'s energy that came from that interaction.",
        "completeall": f"Fraction of the interaction's energy the {obj} captured, over the whole snarl.",
        "completeslc": "The same restricted to the slice.",
        "completeallnopecut": "The same as `completeall`, without the photoelectron threshold.",
        "completeslcnopecut": "The same as `completeslc`, without it.",
    }
    return d | (extra or {})


for g, obj, plural in (("thslc", "slice", "slices"),
                       ("thshw", "shower", "showers"),
                       ("thtrk", "track", "tracks"),
                       ("thevt", "reconstructed event", "events")):
    extra = {}
    if g == "thslc":
        extra = {"nneu": "How many interactions contributed to the slice.",
                 "secondpurity": "Purity with respect to the second-best matching interaction.",
                 "complete": "Fraction of the matched interaction's energy that ended up in the slice."}
    if g == "thtrk":
        extra = {"trkstdhep": "Index of the truth particle the track itself was matched to."}
    group(g, f"""Truth matching for reconstructed {plural}. Unlike `thstp`,
which labels the strips the archive keeps, this is meaningless without the
reconstructed object it describes.""", truth_match(obj, extra))


# --------------------------------------------------------------------------
# Emit
# --------------------------------------------------------------------------

HEAD = """# MINOS SNTP branches

Every branch in the `NtpSt` tree of a MINOS SNTP file, grouped as the file
groups them.

The first column says whether the branch is in the archive by default:
**✓** exported, **✗** not. That is read from
[`branches.txt`](branches.txt), which is where the decision lives — see
[README.md](README.md) for how to change it. Anything whose meaning could
not be established is marked **???**.

Descriptions come from the MINOS LOON sources — the `NtpSR*`/`NtpMC*`/
`NtpTH*` class headers and the modules that fill them. A **???** means the
meaning could not be established from those sources: usually the field is
declared and read but nothing in the code available here sets it, so any
description would be a guess from the name. Where a branch is excluded, the
description says why.

Two themes run through the exclusions, and are worth stating once:

- **Reconstruction is dropped as a matter of policy.** `trk`, `shw`, `slc`,
  `clu`, `evt` and everything keyed to them is what MINOS concluded from the
  data; the archive holds what the detector and the simulation recorded, so
  a future analysis can draw its own conclusions.
- **Redundancy is dropped only after it is checked.** Where a branch is
  excluded because it duplicates another, `check_exclusions.py` re-tests
  that claim on every file before conversion, and refuses the file if it
  fails. Several branches that *look* redundant are kept because that test
  failed — the description says so where it applies.

A secondary source, where the LOON code is silent on a field's meaning, is
the 2003 MINOS internal glossary for the predecessor `NtpSR` tree, which
uses the same field names:
[web.archive.org](https://web.archive.org/web/20111018134109/http://www-numi.fnal.gov/offline_software/srt_public_context/WebDocs/ntpdict.html).

This file is generated by `make_branches_doc.py` from the manifest, so the
tick column cannot drift out of step with what the tool actually does.

"""


def read_manifest() -> dict[str, list[tuple[str, bool]]]:
    """Group -> [(short branch name, enabled)], in manifest order."""
    out: dict[str, list[tuple[str, bool]]] = {}
    current = None
    for line in (HERE / "branches.txt").read_text().splitlines():
        s = line.strip()
        m = re.match(r"^#\s+([\w<>]+)\s+—", s)
        if m:
            current = m.group(1)
            out.setdefault(current, [])
            continue
        if not s or s.startswith("# "):
            continue
        enabled = not s.startswith("#")
        name = s.lstrip("#").split("#", 1)[0].strip()
        if not name or current is None:
            continue
        out[current].append((name.split("/")[-1], enabled))
    return out


def main() -> int:
    manifest = read_manifest()
    described = {g: f for g, _, f in GROUPS}
    blurbs = {g: b for g, b, _ in GROUPS}

    parts, missing, totals = [HEAD], [], [0, 0]
    for gname, entries in manifest.items():
        fields = described.get(gname, {})
        on = sum(1 for _, e in entries if e)
        totals[0] += on
        totals[1] += len(entries)

        title = "header" if gname.startswith("RecRecordImp") else gname
        parts.append(f"## `{title}` — {on} of {len(entries)} archived\n")
        parts.append(" ".join(blurbs.get(title, "").split()) + "\n")
        parts.append("| | Branch | Meaning |\n|--|--------|---------|")
        for short, enabled in entries:
            key = short.split(".", 1)[1] if short.startswith(f"{title}.") else short
            desc = fields.get(key) or fields.get(short)
            if desc is None:
                desc = "**???**"
                missing.append(f"{gname}/{short}")
            parts.append(f"| {'✓' if enabled else '✗'} | `{short}` | {desc} |")
        parts.append("")

    (HERE / "BRANCHES.md").write_text("\n".join(parts) + "\n")
    print(f"BRANCHES.md: {totals[1]} branches, {totals[0]} archived, "
          f"{len(missing)} undescribed")
    for m in missing[:25]:
        print("   undescribed:", m)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

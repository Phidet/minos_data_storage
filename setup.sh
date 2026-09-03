#!/bin/sh
# Create the virtual environment and install what the tools import.
# Safe to rerun -- it will just confirm what is already there.
set -e
cd "$(dirname "$0")"

python3 - <<'PY'
import sys
if sys.version_info < (3, 9):
    sys.exit(f"need Python 3.9 or newer, this is {sys.version.split()[0]}")
PY

[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet uproot awkward numpy h5py fsspec-xrootd

# Silent unless something is wrong: import everything the tools need and
# confirm fsspec really can handle root://, since a working install that
# cannot stream would only show up part way into a run.
.venv/bin/python - <<'PY'
import sys, uproot, awkward, numpy, h5py, fsspec
if "root" not in fsspec.available_protocols():
    sys.exit("fsspec cannot handle root:// -- dCache streaming will not work")
PY
echo "activate with:  source .venv/bin/activate"

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
.venv/bin/pip install --quiet uproot awkward numpy h5py

.venv/bin/python - <<'PY'
import sys, uproot, awkward, numpy, h5py
print(f"ready: python {sys.version.split()[0]}, uproot {uproot.__version__}, "
      f"awkward {awkward.__version__}, numpy {numpy.__version__}, h5py {h5py.__version__}")
PY
echo "activate with:  source .venv/bin/activate"

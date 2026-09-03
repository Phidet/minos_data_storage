#!/bin/bash
# Prepare a gpvm session to run this tool. Safe to source every time --
# first run does the one-off install, every run does the same environment
# setup and activation.
#
#   source ./setup.sh
#
# Must be sourced, not executed: it sets SAM_EXPERIMENT and activates the
# venv in *your* shell, and a child process cannot do that for its parent.

if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
    echo "run this as: source ./setup.sh   (not ./setup.sh)" >&2
    return 1 2>/dev/null || exit 1
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- SAM / UPS, every session -------------------------------------------
# Brings `samweb` onto PATH. This tool no longer stages files itself --
# `samweb prestage-dataset` and the SAM definition it reads are set up by
# hand first; see the README.
if [ -f /grid/fermiapp/products/common/etc/setups.sh ]; then
    source /grid/fermiapp/products/common/etc/setups.sh
    setup sam_web_client
else
    echo "warning: /grid/fermiapp/products/common/etc/setups.sh not found -- " \
         "samweb will not be set up" >&2
fi
export SAM_EXPERIMENT="${SAM_EXPERIMENT:-minos}"

if command -v samweb >/dev/null 2>&1; then
    : # good -- samweb is on PATH
else
    echo "warning: samweb is still not on PATH after setup" >&2
fi

# --- Python venv, first time only ----------------------------------------
if [ ! -d "$HERE/.venv" ]; then
    python3 - <<'PY' || return 1 2>/dev/null || exit 1
import sys
if sys.version_info < (3, 9):
    sys.exit(f"need Python 3.9 or newer, this is {sys.version.split()[0]}")
PY
    python3 -m venv "$HERE/.venv" || { rm -rf "$HERE/.venv"; return 1 2>/dev/null || exit 1; }
    "$HERE/.venv/bin/pip" install --quiet --upgrade pip \
        && "$HERE/.venv/bin/pip" install --quiet uproot awkward numpy h5py fsspec-xrootd \
        || { rm -rf "$HERE/.venv"; return 1 2>/dev/null || exit 1; }

    # Silent unless something is wrong: import everything the tools need and
    # confirm fsspec really can handle root://, since a working install that
    # cannot stream would only show up part way into a run.
    "$HERE/.venv/bin/python" - <<'PY' || return 1 2>/dev/null || exit 1
import sys, uproot, awkward, numpy, h5py, fsspec
if "root" not in fsspec.available_protocols():
    sys.exit("fsspec cannot handle root:// -- dCache streaming will not work")
PY
fi

# --- activate, every session ----------------------------------------------
source "$HERE/.venv/bin/activate"

echo "environment ready: $(python3 --version), SAM_EXPERIMENT=$SAM_EXPERIMENT" \
     "$(command -v samweb >/dev/null 2>&1 && echo ', samweb on PATH')"

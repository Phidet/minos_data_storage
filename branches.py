#!/usr/bin/env python3
"""Read the branch manifest.

`branches.txt` lists every branch in the NtpSt tree, one per line. A line
that is not commented out is exported; a leading '#' excludes it. Anything
after a '#' on a live line is a note, not part of the name.

Keeping the list in a text file rather than in code means changing what the
archive contains is an edit, not a patch -- and the reasons for each
exclusion sit next to the branch they apply to.
"""

from __future__ import annotations

import re
from pathlib import Path

DEFAULT_MANIFEST = Path(__file__).with_name("branches.txt")


def load(manifest: Path | str = DEFAULT_MANIFEST) -> list[str]:
    """Branch names to export, in manifest order.

    The count the header claims is checked against the count actually
    enabled. It is a comment, so nothing stops the two drifting apart, and
    the whole point of the file is that a reader can trust what it says
    about itself.
    """
    text = Path(manifest).read_text()
    names = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            names.append(line)

    claim = re.search(r"^#\s*(\d+) of (\d+) branches enabled", text, re.M)
    if claim and int(claim.group(1)) != len(names):
        raise ValueError(
            f"{manifest}: the header says {claim.group(1)} branches are "
            f"enabled, but {len(names)} are. Update the header comment."
        )
    return names


def summarise(manifest: Path | str = DEFAULT_MANIFEST) -> dict[str, tuple[int, int]]:
    """Per branch group, how many leaves are enabled out of the total.

    Useful for a one-line report before a long run, and for spotting a
    manifest edit that enabled far more than intended.
    """
    counts: dict[str, list[int]] = {}
    for line in Path(manifest).read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("# "):
            continue
        enabled = not stripped.startswith("#")
        name = stripped.lstrip("#").split("#", 1)[0].strip()
        if not name:
            continue
        parts = name.split("/")
        group = parts[1] if len(parts) > 1 else name
        slot = counts.setdefault(group, [0, 0])
        slot[0] += int(enabled)
        slot[1] += 1
    return {k: (v[0], v[1]) for k, v in counts.items()}


if __name__ == "__main__":
    import sys

    manifest = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_MANIFEST
    names = load(manifest)
    groups = summarise(manifest)
    total = sum(t for _, t in groups.values())
    print(f"{len(names)} of {total} branches enabled\n")
    for group, (on, tot) in sorted(groups.items(), key=lambda kv: -kv[1][0]):
        mark = " " if on else "-"
        print(f"  {mark} {group:30s} {on:4d} / {tot:4d}")

#!/usr/bin/env python3
"""Normalise a capture log for inclusion in samples/.

Applied transforms:
  - Rebase `unix_ms` timestamps so the first data line starts at 0.
    Inter-frame deltas are preserved exactly, which is all the protocol
    spec and the reference decoders care about. Absolute wall-clock
    values don't add information to a published fixture.
  - Pass comment lines through unchanged.

Run:
    python3 tools/normalize_sample.py samples/3204-overtake-sample.log
    # prints to stdout; redirect to overwrite if the diff looks right.

Idempotent: running on an already-normalised file leaves timestamps
starting at 0 and produces byte-identical output.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def normalise(lines: list[str]) -> list[str]:
    base: int | None = None
    out: list[str] = []
    for line in lines:
        stripped = line.rstrip("\n")
        if not stripped or stripped.startswith("#"):
            out.append(line)
            continue
        parts = stripped.split(maxsplit=2)
        if len(parts) < 3:
            out.append(line)
            continue
        try:
            ts = int(parts[0])
        except ValueError:
            out.append(line)
            continue
        if base is None:
            base = ts
        rebased = ts - base
        out.append(f"{rebased} {parts[1]} {parts[2]}\n")
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("path", help="capture log to normalise")
    ap.add_argument("--in-place", action="store_true", help="overwrite the file")
    args = ap.parse_args(argv[1:])

    path = Path(args.path)
    lines = path.read_text().splitlines(keepends=True)
    normalised = normalise(lines)
    if args.in_place:
        path.write_text("".join(normalised))
        print(f"normalised {path} in place", file=sys.stderr)
    else:
        sys.stdout.write("".join(normalised))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

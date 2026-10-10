#!/usr/bin/env python3
"""Build sanity check: no makefile may reference a helper that does not exist.

An earlier revision deleted scripts/ensure-gatekeeper-props.py while
BoardConfig.mk and AndroidBoard.mk still invoked it, which fails the build at
the ramdisk step.  This catches that class of mistake before CI does.

Run:  python check-build-refs.py --repo <path>
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# $(DEVICE_PATH)/scripts/x.py  and  $(LOCAL_PATH)/scripts/x.py
PATTERNS = (
    re.compile(r"\$\((?:DEVICE_PATH|LOCAL_PATH)\)/(scripts/[\w.-]+\.py)"),
    re.compile(r"\$\((?:DEVICE_PATH|LOCAL_PATH)\)/(config/[\w.-]+)"),
)

failures: list = []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", type=Path, required=True)
    args = ap.parse_args()
    repo = args.repo

    makefiles = sorted(p for p in repo.rglob("*")
                       if p.is_file() and p.suffix in (".mk", ".bp"))
    print(f"scanning {len(makefiles)} makefiles under {repo}")
    checked = 0
    for mf in makefiles:
        text = mf.read_text(encoding="utf-8", errors="replace")
        for pat in PATTERNS:
            for m in pat.finditer(text):
                rel = m.group(1)
                checked += 1
                target = repo / rel
                ok = target.exists()
                if not ok:
                    failures.append(f"{mf.relative_to(repo)} -> {rel} (missing)")
                print(f"  {'OK  ' if ok else 'MISS'}  "
                      f"{mf.relative_to(repo)} -> {rel}")

    print()
    print(f"checked {checked} helper references")
    if failures:
        print(f"FAILED ({len(failures)}):")
        for f in failures:
            print("   -", f)
        return 1
    print("ALL REFERENCES RESOLVE")
    return 0


if __name__ == "__main__":
    sys.exit(main())

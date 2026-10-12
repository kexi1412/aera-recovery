#!/usr/bin/env python3
"""Regenerate PROVENANCE.json::device_files from the tracked tree.

Canonical form is the git blob, i.e. LF line endings. This checkout lives on
Windows and was materialised with CRLF, so hashing the working-tree bytes
directly produces a different digest for every file and makes the recorded
values repository-state dependent - they would stop matching after any
re-clone. Normalising CRLF to LF makes each digest equal to the git blob for an
unmodified file, which is what the existing entries already were, and keeps
them reproducible on any platform.

Symlinks are recorded from the git index mode (120000) with the blob content as
the target. On a checkout where core.symlinks is false - as on this Windows
volume, where symlinks become small regular files holding the target path - the
index is the only reliable source of truth, so it is used unconditionally.

Run without arguments to print the diff against the current file, or with
--write to update it in place.
"""
import hashlib
import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROV = os.path.join(REPO, "PROVENANCE.json")

# PROVENANCE.json cannot record its own digest: the value is part of the bytes
# being hashed, so no assignment is a fixed point. It is the only exclusion, and
# check-provenance.py knows to expect it.
SELF = "PROVENANCE.json"


def git(*args, binary=False):
    r = subprocess.run(
        ["git", "-c", "safe.directory=*", *args],
        cwd=REPO,
        capture_output=True,
        text=not binary,
    )
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout


def worktree_sha256(path):
    """sha256 of the file with CRLF normalised to LF (i.e. the blob form)."""
    with open(os.path.join(REPO, path), "rb") as fh:
        data = fh.read()
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def index_modes():
    modes = {}
    for line in git("ls-files", "-s").splitlines():
        meta, path = line.split("\t", 1)
        mode = meta.split()[0]
        # git quotes paths containing non-ASCII; undo it
        if path.startswith('"') and path.endswith('"'):
            path = path[1:-1].encode().decode("unicode_escape").encode(
                "latin-1"
            ).decode("utf-8")
        modes[path] = mode
    return modes


def main():
    write = "--write" in sys.argv
    modes = index_modes()

    with open(PROV, encoding="utf-8") as fh:
        prov = json.load(fh)
    old = {e["path"]: e for e in prov.get("device_files", [])}

    entries = []
    changed = []
    skipped = []
    for path in sorted(modes):
        if path == SELF:
            skipped.append(path)
            continue
        if modes[path] == "120000":
            target = git("show", f":{path}").rstrip("\n")
            entry = {"path": path, "symlink": target}
            if old.get(path, {}).get("symlink") != target:
                changed.append(f"  {path}: symlink target {target!r} (new/changed)")
        else:
            digest = worktree_sha256(path)
            entry = {"path": path, "sha256": digest}
            prev = old.get(path, {}).get("sha256")
            if prev is None:
                changed.append(f"  {path}: added")
            elif prev != digest:
                changed.append(f"  {path}: {prev[:12]}.. -> {digest[:12]}..")
        entries.append(entry)

    removed = sorted(set(old) - set(modes))
    for path in removed:
        changed.append(f"  {path}: removed (no longer tracked)")

    print(f"tracked files: {len(modes)}  (symlinks: "
          f"{sum(1 for m in modes.values() if m == '120000')})")
    print(f"declared now:  {len(entries)}   before: {len(old)}")
    if skipped:
        print(f"excluded (cannot self-reference): {', '.join(skipped)}")
    print("changes:")
    print("\n".join(changed) if changed else "  (none)")

    if not write:
        print("\ndry run; pass --write to update PROVENANCE.json")
        return 0

    prov["device_files"] = entries
    with open(PROV, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(prov, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print(f"\nwrote {PROV}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

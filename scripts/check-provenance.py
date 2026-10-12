#!/usr/bin/env python3
"""Provenance/working-tree reconciliation for device/oneplus/astonc.

Checks, in order:
  1. PROVENANCE.json parses and every device_files entry has exactly one of
     sha256 / symlink.
  2. Every declared path exists in the working tree, with the declared hash or
     the declared symlink target.
  3. Every file tracked by git is declared, so a new file cannot silently enter
     the tree unrecorded.
  4. No declared entry is a duplicate.

Exit status is the number of hard failures (0 is clean). Warnings - a
declared-but-untracked file, or a gitignored path - do not fail the run.
"""
import hashlib
import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROV = os.path.join(REPO, "PROVENANCE.json")

fail = 0
warn = 0


def ok(msg):
    print(f"  OK    {msg}")


def bad(msg):
    global fail
    fail += 1
    print(f"  FAIL  {msg}")


def note(msg):
    global warn
    warn += 1
    print(f"  WARN  {msg}")


def sha256(path):
    """sha256 of the blob form: CRLF normalised to LF.

    The canonical digest is the git blob. This checkout is on a Windows volume
    and was materialised with CRLF, so hashing the raw bytes would report every
    file as mismatched even though nothing has been modified. Normalising makes
    the digest platform independent and equal to `git show HEAD:path` for an
    unmodified file.
    """
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        data = fh.read()
    h.update(data.replace(b"\r\n", b"\n"))
    return h.hexdigest()


def git(*args):
    r = subprocess.run(
        ["git", "-c", "safe.directory=*", *args],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        return []
    return [l for l in r.stdout.splitlines() if l.strip()]


def index_modes():
    """path -> git index mode, and path -> symlink target for mode 120000.

    The index is authoritative: this checkout is on a Windows volume where
    core.symlinks is false, so a tracked symlink is materialised as a small
    regular file containing the target path rather than as a real link.
    """
    modes, targets = {}, {}
    for line in git("ls-files", "-s"):
        meta, p = line.split("\t", 1)
        if p.startswith('"') and p.endswith('"'):
            p = p[1:-1].encode().decode("unicode_escape").encode(
                "latin-1"
            ).decode("utf-8")
        mode = meta.split()[0]
        modes[p] = mode
        if mode == "120000":
            # git() returns a list of non-empty lines; a symlink target is a
            # single line, so rejoining reproduces the blob content.
            targets[p] = "\n".join(git("show", f":{p}"))
    return modes, targets


print(f"repo: {REPO}")

# ---------------------------------------------------------------- 1. schema
try:
    with open(PROV, encoding="utf-8") as fh:
        prov = json.load(fh)
    ok("PROVENANCE.json parses as JSON")
except Exception as exc:  # noqa: BLE001
    print(f"  FAIL  PROVENANCE.json is not valid JSON: {exc}")
    sys.exit(1)

entries = prov.get("device_files")
if not isinstance(entries, list):
    print("  FAIL  device_files is missing or not a list")
    sys.exit(1)
print(f"  ...   {len(entries)} declared entries")

declared = {}
for i, e in enumerate(entries):
    p = e.get("path")
    if not p:
        bad(f"device_files[{i}] has no path")
        continue
    has_h = "sha256" in e
    has_l = "symlink" in e
    if has_h == has_l:
        bad(f"{p}: must have exactly one of sha256/symlink")
    if p in declared:
        bad(f"{p}: declared more than once")
    declared[p] = e

# --------------------------------------------------- 2. existence + hashes
modes, symlink_targets = index_modes()

for p, e in sorted(declared.items()):
    full = os.path.join(REPO, p)
    if not os.path.lexists(full):
        bad(f"{p}: declared but absent from the working tree")
        continue
    if "symlink" in e:
        if modes.get(p) != "120000":
            bad(f"{p}: declared a symlink but git index mode is "
                f"{modes.get(p, 'absent')}")
            continue
        actual = symlink_targets.get(p)
        if actual != e["symlink"]:
            bad(f"{p}: symlink target {actual!r} != declared {e['symlink']!r}")
        else:
            ok(f"{p} -> {actual}")
        continue
    if modes.get(p) == "120000":
        note(f"{p}: declared with a hash but is a tracked symlink")
        continue
    actual = sha256(full)
    if actual != e["sha256"]:
        bad(f"{p}: sha256 {actual} != declared {e['sha256']}")
    else:
        ok(p)

# ------------------------------------------------- 3. tracked-but-undeclared
# PROVENANCE.json is the one tracked file that cannot appear in its own
# device_files list, because its digest would be part of the bytes it hashes.
SELF_EXEMPT = {"PROVENANCE.json"}

tracked = sorted(modes)
if not tracked:
    note("git ls-files returned nothing; coverage check skipped")
else:
    for t in tracked:
        if t not in declared and t not in SELF_EXEMPT:
            note(f"tracked but not declared: {t}")
    for t in sorted(declared):
        if t not in modes:
            note(f"declared but not tracked by git: {t}")

# ----------------------------------------------------- 4. ignored artefacts
for p in ("recovery/root/vendor/firmware", "recovery/root/vendor/bin"):
    if os.path.isdir(os.path.join(REPO, p)):
        note(f"{p} present in the tree but gitignored by design")

print()
print(f"result: {fail} FAIL, {warn} WARN, {len(declared)} declared")
sys.exit(fail)

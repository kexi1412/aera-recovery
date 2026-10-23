#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Stage the post-decrypt Wi-Fi modules on demand in Load_Post_Decrypt_Modules.

Why this patch exists
---------------------
Run #34 (2026-10-24, user-flashed) proved that loading the QCA6490 stack
during fstab processing breaks this device: decryption stopped working and
wlan0 never appeared, while the run #33 configuration without the loader
decrypts fine.

The mechanism is visible in bootable/recovery/kernel_module_loader.cpp::

    KernelModuleLoader::Load_Vendor_Modules()      // runs at fstab parse
        -> TWFunc::RunFoxScript("/system/bin/beforemodules.sh", "")
        -> Try_And_Load_Modules("/vendor/lib/modules", ...)   // symlink!
             Copy_Modules_To_Tmpfs()   // the WHOLE vendor_dlkm set -> tmpfs
             mount(/tmp copy, /vendor/lib/modules, MS_BIND)
        -> Try_And_Load_Modules("/vendor_dlkm/lib/modules", ...)  // again
        -> exit: ven->UnMount(); ven_dlkm->UnMount(); sys_dlkm->UnMount()

Unmounting /vendor after fstab processing kills keymint's lazy vendor-library
loads (decrypt fails) and cnss2's firmware resolution through
/vendor/firmware_mnt (wlan0 never appears).

This tree therefore sets TW_POST_DECRYPT_MODULES instead of
TW_LOAD_VENDOR_MODULES. With the loader list unset, partitionmanager.cpp:764
compiles the fstab-parse-time call out entirely and the pre-decrypt boot path
is the verified run #33 one. The post-decrypt path
(KernelModuleLoader::Load_Post_Decrypt_Modules, called from the
Decrypt_Data() success path at partitionmanager.cpp:2665) loads each module
with finit_module -- no fork, no bind-mount, no tmpfs directory copies.

The gap this patch closes: upstream stages the post-decrypt modules from
inside Load_Vendor_Modules() (kernel_module_loader.cpp:391 and :416), which
is compiled out here, so the stage directory /tmp/post_decrypt_modules would
never be populated and Load_Post_Decrypt_Modules() would log "staged module
missing" for every entry and load nothing.

What the patch does
-------------------
Adds stage-on-demand at the top of Load_Post_Decrypt_Modules(): if the stage
marker file is absent, mount /vendor, /vendor_dlkm and /mnt/vendor/persist
(the sources the loader and the firmware_class resolver read), then call
Stage_Post_Decrypt_Modules(). /vendor stays mounted afterwards -- the same
state the verified run #33 session had, and the one cnss2 needs at probe
time, which happens immediately after the first finit_module() below this
code. The stage marker makes the replay path (partitionmanager.cpp:4045)
idempotent: a re-run sees the marker and skips straight to the load.

The module list itself comes from TW_POST_DECRYPT_MODULES (BoardConfig.mk),
whose order is dependency order -- finit_module does not resolve
modules.dep.

Usage::

    python3 scripts/fix-post-decrypt-stage.py bootable/recovery
"""

from __future__ import annotations

import argparse
import difflib
import sys

FILE = "kernel_module_loader.cpp"

# The head of Load_Post_Decrypt_Modules() as upstream wrote it. Anchored on
# the whole early-return block so a partial reshape cannot be patched blind.
LOAD_HEAD_OLD = """bool KernelModuleLoader::Load_Post_Decrypt_Modules() {
\tif (post_decrypt_modules_requested.empty()) {
\t\tLOGINFO("No post-decrypt modules requested for loading\\n");
\t\tproperty_set("post.decrypt.modules", "true");
\t\treturn true;
\t}
"""

LOAD_HEAD_NEW = """bool KernelModuleLoader::Load_Post_Decrypt_Modules() {
\tif (post_decrypt_modules_requested.empty()) {
\t\tLOGINFO("No post-decrypt modules requested for loading\\n");
\t\tproperty_set("post.decrypt.modules", "true");
\t\treturn true;
\t}

\t// astonc: this device loads Wi-Fi strictly after decryption. The
\t// pre-decrypt loader unmounts /vendor and /vendor_dlkm when it finishes,
\t// which kills keymint's lazy vendor-library loads (decrypt) and cnss2's
\t// firmware resolution (wlan0), so TW_LOAD_VENDOR_MODULES stays unset and
\t// only this path runs. Upstream stages from Load_Vendor_Modules(), which
\t// is compiled out here, so stage on demand: /vendor first, because cnss2
\t// resolves amss.bin/bdwlan.elf through /vendor/firmware_mnt at probe
\t// time, immediately after the finit_module() calls below; persist holds
\t// wlan/WCNSS_qcom_cfg.ini. Leaving them mounted matches the verified
\t// no-loader session. The marker keeps the partitionmanager replay path
\t// idempotent.
\tif (!TWFunc::Path_Exists(post_decrypt_stage_dir + "/modules.load.postdecrypt")) {
\t\tLOGINFO("astonc: staging post-decrypt modules on demand\\n");
\t\tPartitionManager.Mount_By_Path("/vendor", false);
\t\tPartitionManager.Mount_By_Path("/vendor_dlkm", false);
\t\tPartitionManager.Mount_By_Path("/mnt/vendor/persist", false);
\t\tStage_Post_Decrypt_Modules();
\t}
"""

MARKER = "astonc: staging post-decrypt modules on demand"


def die(msg):
    sys.stderr.write("::error::%s\n" % msg)
    sys.exit(1)


def main():
    if len(sys.argv) != 2:
        die("usage: fix-post-decrypt-stage.py <path-to-bootable/recovery>")
    root = sys.argv[1]
    path = root.rstrip("/\\") + "/" + FILE
    try:
        with open(path, "r", encoding="utf-8", newline="") as f:
            original = f.read()
    except FileNotFoundError:
        die("%s not found" % path)

    patched = original

    if MARKER in patched:
        # Idempotent re-run: verify the shape is the patched one, then stop.
        if patched.count(LOAD_HEAD_NEW) != 1:
            die("already patched but the patched head is not unique; "
                "upstream reshaped the file, review by hand")
        print("fix-post-decrypt-stage: already patched; nothing to do")
        return 0

    if patched.count(LOAD_HEAD_OLD) != 1:
        die("expected exactly 1 Load_Post_Decrypt_Modules() head, found %d -- "
            "upstream reshaped the function, review before re-applying"
            % patched.count(LOAD_HEAD_OLD))

    patched = patched.replace(LOAD_HEAD_OLD, LOAD_HEAD_NEW, 1)

    # Post-conditions. Upstream already calls Stage_Post_Decrypt_Modules()
    # twice from Load_Vendor_Modules(); assert the DELTA, not an absolute
    # count, so the assertion survives upstream reshaping that function.
    if patched.count(MARKER) != 1:
        die("the stage-on-demand marker is not present exactly once")
    if patched.count("Stage_Post_Decrypt_Modules();") \
            != original.count("Stage_Post_Decrypt_Modules();") + 1:
        die("expected exactly one new Stage_Post_Decrypt_Modules() call "
            "(%d -> %d expected, found %d)"
            % (original.count("Stage_Post_Decrypt_Modules();"),
               original.count("Stage_Post_Decrypt_Modules();") + 1,
               patched.count("Stage_Post_Decrypt_Modules();")))
    for needle in ('Mount_By_Path("/vendor", false)',
                   'Mount_By_Path("/vendor_dlkm", false)',
                   'Mount_By_Path("/mnt/vendor/persist", false)'):
        if patched.count(needle) != 1:
            die("expected exactly 1 occurrence of %r, found %d"
                % (needle, patched.count(needle)))
    # The patch must not touch the pre-decrypt function or its call site.
    if "TW_LOAD_VENDOR_MODULES" not in patched:
        die("kernel_module_loader.cpp lost TW_LOAD_VENDOR_MODULES references; "
            "the wrong function may have been edited")
    if patched.count("bool KernelModuleLoader::Load_Post_Decrypt_Modules() {") != 1:
        die("Load_Post_Decrypt_Modules definition count changed")
    # Brace balance must be unchanged (one balanced if-block added).
    if patched.count("{") != original.count("{") + 1:
        die("opening brace count changed unexpectedly")
    if patched.count("}") != original.count("}") + 1:
        die("closing brace count changed unexpectedly")
    # The insertion must sit inside Load_Post_Decrypt_Modules, i.e. after its
    # definition and before the first finit_module call of that function.
    head = patched.find("bool KernelModuleLoader::Load_Post_Decrypt_Modules() {")
    marker = patched.find(MARKER)
    body = patched.find("Loading post-decrypt module:", marker)
    if not (0 <= head < marker < body):
        die("the stage-on-demand block must sit between the function head "
            "and its first load call")

    diff = list(difflib.unified_diff(
        original.splitlines(), patched.splitlines(),
        "a/" + FILE, "b/" + FILE, lineterm="", n=2))
    changed = sum(1 for l in diff if l.startswith(("+", "-"))
                  and not l.startswith(("+++", "---")))
    print("fix-post-decrypt-stage: %s changed lines: %d" % (FILE, changed))
    if not (14 <= changed <= 45):
        die("diff size %d outside the expected 20..45 band" % changed)

    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(patched)

    print("fix-post-decrypt-stage: Load_Post_Decrypt_Modules now stages on "
          "demand with /vendor, /vendor_dlkm and persist mounted")
    return 0


if __name__ == "__main__":
    sys.exit(main())

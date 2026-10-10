#!/usr/bin/env python3
"""Make the recovery ramdisk declare its AIDL HALs to servicemanager.

Root cause this fixes
---------------------
`/system/bin/servicemanager` refuses `addService()` for any AIDL interface that
is not declared in the VINTF manifest it can read.  AOSP ServiceManager.cpp:

    static bool isVintfDeclared(const Access::CallingContext& ctx,
                                const std::string& name) {
        ...
        bool found = forEachManifest([&](const ManifestWithDescription& mwd) {
            if (mwd.manifest->hasAidlInstance(aname.package, aname.iface,
                                              aname.instance)) { ... }
        });
        if (!found) { ALOGI("... Could not find %s in the VINTF manifest.", ...); }
        return found;
    }

`registerClientCallback`/`addService` then return EX_ILLEGAL_ARGUMENT
("VINTF declaration error."), keystore2 treats a failed addService as fatal and
dies, and the recovery decrypt path hangs forever retrying getService:

    servicemanager: Caller(pid=2885,...) Could not find
        android.system.keystore2.IKeystoreService/default in the VINTF manifest
    init: Service 'keystore2' (pid 2885) received signal 6
    servicemanager: Caller(pid=919,...) Tried to start aidl service
        android.system.keystore2.IKeystoreService/default as a lazy service,
        but was unable to.

Why the declaration was invisible
---------------------------------
The framework manifest loader (VintfObject.cpp) is:

    status_t VintfObject::fetchUnfilteredFrameworkHalManifest(HalManifest* out, ...) {
        auto systemEtcStatus = fetchOneHalManifest(kSystemManifest, out, error);
        if (systemEtcStatus == OK) {
            // ... and only then ...
            addDirectoryManifests(kSystemManifestFragmentDir, out, false, error);
            ...
        }
        return out->fetchAllInformation(getFileSystem().get(), kSystemLegacyManifest, error);
    }

with

    kSystemManifest            = /system/etc/vintf/manifest.xml
    kSystemManifestFragmentDir = /system/etc/vintf/manifest/
    kSystemLegacyManifest      = /system/manifest.xml

The fragment directory is parsed ONLY when the main framework manifest exists.
This tree ships no /system/etc/vintf/manifest.xml at all:

    $ cat /system/etc/vintf/manifest.xml
    cat: /system/etc/vintf/manifest.xml: No such file or directory

so the loader takes the legacy branch and the framework manifest is
/system/manifest.xml alone.  That file declares only hidl.manager and
hidl.token -- no AIDL HAL at all -- so every AIDL `addService` in recovery is
rejected.

The same rule explains the asymmetry seen on the device, which is the strongest
confirmation of this diagnosis.  The vendor side DOES ship its main manifest
(the empty stub /vendor/etc/vintf/manifest.xml), so the vendor fragment
directory IS parsed and its HALs register fine; the framework side has no main
manifest, so its fragment directory is dead:

    vendor domain  -> gatekeeper-1-0 running, lshal: gatekeeper@1.0 registered
                      keymint-service-qti running, AIDL keymint registered
    system domain  -> keystore2 SIGABRT, IBootControl never registered

Same libvintf, same recovery init, opposite outcomes -- purely because one
directory has its main manifest and the other does not.  Declaring the two
interfaces in /system/manifest.xml is therefore the fix, and it is the
configuration that was verified working on hardware.

The two fragment files are repaired as well, for the day a
/system/etc/vintf/manifest.xml appears (a future AERA revision, or a device that
ships one):

  * AERA's prebuilt etc/init/android.system.keystore2-service.xml sets
    format="aidl" but describes the interface with the HIDL
    <interface><name>/<instance> shape.  libvintf does convert that shape
    (parse_xml.cpp turns <version> x <interface> x <instance> into <fqname>s,
    explicitly including HalFormat::AIDL), so this is hygiene rather than the
    boot blocker -- but <fqname> is the shape AOSP itself emits and the one
    that survives future meta-version changes.

  * /system/etc/vintf/manifest/android.hardware.boot-service.default.xml
    carries type="device" while sitting in the framework fragment directory.

NOTE: this script deliberately does NOT create /system/etc/vintf/manifest.xml to
switch the fragment directory on.  That directory also contains
astonc.keymint.xml, which is type="device"; merging a device-typed fragment into
a framework manifest fails the whole framework load, which would take every
framework AIDL registration down with it.  Wiring the directory up is a larger
change than this bug needs, and the legacy-manifest fix is already verified.

Observed after the fix, on device:

    servicemanager: Caller(pid=7974,...) Found
        android.system.keystore2.IKeystoreService/default in framework VINTF manifest.
    $ service check android.system.keystore2.IKeystoreService/default
    Service android.system.keystore2.IKeystoreService/default: found
    $ service list
    Found 13 services        # keystore2 + the six android.security.* children

Why this runs as a post-processing step
---------------------------------------
The device tree's copy of the ramdisk and AERA's own `etc/Android.mk`
prebuilt install both write these paths, in an order that is not part of any
contract.  `BOARD_RECOVERY_IMAGE_PREPARE` is the last hook to touch
TARGET_RECOVERY_ROOT_OUT, so rewriting here is deterministic instead of
order-dependent.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

FRAMEWORK_DECLS = (
    # (name, version, fqname)
    ("android.system.keystore2", "1", "IKeystoreService/default"),
    ("android.hardware.boot", "1", "IBootControl/default"),
)

KEYSTORE2_FRAGMENT = """<manifest version="1.0" type="framework">
    <!--
        Declared as AIDL.  libvintf reads an AIDL HAL from <fqname>; the HIDL
        <interface><name>/<instance> shape is silently dropped, which left
        keystore2 unable to register and killed it with SIGABRT on boot.
    -->
    <hal format="aidl">
        <name>android.system.keystore2</name>
        <version>1</version>
        <fqname>IKeystoreService/default</fqname>
    </hal>
</manifest>
"""

BOOT_FRAGMENT = """<manifest version="1.0" type="framework">
    <!--
        This fragment lives in the framework fragment directory, so it must be
        type="framework" to be merged at all.  AIDL HALs need <fqname>.
    -->
    <hal format="aidl">
        <name>android.hardware.boot</name>
        <version>1</version>
        <fqname>IBootControl/default</fqname>
    </hal>
</manifest>
"""

AIDL_BLOCK = """    <hal format="aidl">
        <name>{name}</name>
        <version>{version}</version>
        <fqname>{fqname}</fqname>
    </hal>
"""

# init can only lazy-start a service it knows an interface name for.  Without
# this line `setprop ctl.interface_start aidl/<iface>` fails with
# "Control message: Could not find 'aidl/...' for ctl.interface_start".
INTERFACE_LINE = "    interface aidl android.system.keystore2.IKeystoreService/default"


def write_text(path: Path, text: str, created: list, updated: list) -> None:
    """Write LF-only UTF-8, only when the content actually changes."""
    existed = path.is_file()
    if existed and path.read_text(encoding="utf-8") == text:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    (updated if existed else created).append(str(path))


def ensure_framework_manifest(root: Path, created: list, updated: list) -> None:
    """Declare the AIDL HALs in the framework manifest servicemanager reads."""
    manifest = root / "system" / "manifest.xml"
    if not manifest.is_file():
        # The framework reader has no /system/etc/vintf/manifest.xml on this
        # build, so this fallback path is the one that is actually consulted.
        text = '<manifest version="1.0" type="framework">\n</manifest>\n'
    else:
        text = manifest.read_text(encoding="utf-8")

    missing = [d for d in FRAMEWORK_DECLS if f"<name>{d[0]}</name>" not in text]
    if not missing:
        return

    if "</manifest>" not in text:
        raise SystemExit(f"{manifest}: no closing </manifest>, refusing to edit")

    block = "".join(AIDL_BLOCK.format(name=n, version=v, fqname=f)
                    for n, v, f in missing)
    text = text.replace("</manifest>", block + "</manifest>", 1)
    write_text(manifest, text, created, updated)


def ensure_keystore2_rc(root: Path, created: list, updated: list) -> None:
    """Let init lazy-start keystore2 by interface name."""
    rc = root / "system" / "etc" / "init" / "keystore2.rc"
    if not rc.is_file():
        return
    text = rc.read_text(encoding="utf-8")
    if "interface aidl" in text:
        return
    if not text.endswith("\n"):
        text += "\n"
    write_text(rc, text + INTERFACE_LINE + "\n", created, updated)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("recovery_root", type=Path,
                    help="TARGET_RECOVERY_ROOT_OUT")
    args = ap.parse_args()

    root = args.recovery_root
    if not root.is_dir():
        raise SystemExit(f"missing recovery root: {root}")

    created: list = []
    updated: list = []

    # 1. The declaration that actually fixes the registration.
    ensure_framework_manifest(root, created, updated)

    # 2. Repair the two fragments that libvintf currently discards.
    write_text(
        root / "system" / "etc" / "vintf" / "manifest"
        / "android.system.keystore2-service.xml",
        KEYSTORE2_FRAGMENT, created, updated)
    write_text(
        root / "system" / "etc" / "vintf" / "manifest"
        / "android.hardware.boot-service.default.xml",
        BOOT_FRAGMENT, created, updated)

    # 3. Make init able to lazy-start keystore2.
    ensure_keystore2_rc(root, created, updated)

    for label, items in (("created", created), ("updated", updated)):
        for item in items:
            print(f"vintf fix: {label} {item}")
    if not created and not updated:
        print("vintf fix: already applied, nothing to do")
    return 0


if __name__ == "__main__":
    sys.exit(main())

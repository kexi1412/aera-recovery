#!/usr/bin/env python3
"""Verify scripts/fix-recovery-vintf.py against a fake TARGET_RECOVERY_ROOT_OUT.

Reproduces the exact broken state observed on the device (AERA's shipped
keystore2 fragment, the mis-typed boot fragment, a framework main manifest
without AIDL declarations, and a keystore2.rc without an interface line),
runs the fixer, then asserts the reader-visible result.

Run:  python verify-vintf-fix.py
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXER = HERE / "fix-recovery-vintf.py"

# Byte-identical to AERA bootable/recovery/etc/init/android.system.keystore2-service.xml
BROKEN_KS2 = """<!-- SPDX-License-Identifier: Apache-2.0 -->
<manifest version="1.0" type="framework">
    <hal format="aidl">
        <name>android.system.keystore2</name>
        <interface>
            <name>IKeystoreService</name>
            <instance>default</instance>
        </interface>
    </hal>
</manifest>
"""

BROKEN_BOOT = """<manifest version="1.0" type="device">
    <hal format="aidl">
        <name>android.hardware.boot</name>
        <interface>
            <name>IBootControl</name>
            <instance>default</instance>
        </interface>
    </hal>
</manifest>
"""

BROKEN_RC = """on late-init
    start keystore2

service keystore2 /system/bin/keystore2 /tmp/misc/keystore
    class early_hal
    user root
    group keystore readproc log
    seclabel u:r:recovery:s0
"""

BROKEN_MAIN = """<manifest version="1.0" type="framework">
    <hal>
        <name>android.hidl.manager</name>
        <transport>hwbinder</transport>
        <version>1.2</version>
        <interface>
            <name>IServiceManager</name>
            <instance>default</instance>
        </interface>
    </hal>
</manifest>
"""

failures: list = []


def check(cond: bool, label: str) -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        manifest_dir = root / "system" / "etc" / "vintf" / "manifest"
        init_dir = root / "system" / "etc" / "init"
        manifest_dir.mkdir(parents=True)
        init_dir.mkdir(parents=True)

        ks2 = manifest_dir / "android.system.keystore2-service.xml"
        boot = manifest_dir / "android.hardware.boot-service.default.xml"
        rc = init_dir / "keystore2.rc"
        main_xml = root / "system" / "manifest.xml"
        for path, body in ((ks2, BROKEN_KS2), (boot, BROKEN_BOOT),
                           (rc, BROKEN_RC), (main_xml, BROKEN_MAIN)):
            path.write_text(body, encoding="utf-8")

        print("[1] apply the fix")
        r = subprocess.run([sys.executable, str(FIXER), str(root)],
                           capture_output=True, text=True)
        print("   " + "\n   ".join(r.stdout.strip().splitlines()))
        check(r.returncode == 0, "fixer exits 0")

        print("[2] the framework main manifest declares both AIDL hal")
        text = main_xml.read_text(encoding="utf-8")
        check('format="aidl"' in text, "main manifest uses format=aidl")
        check("<name>android.system.keystore2</name>" in text,
              "declares android.system.keystore2")
        check("IKeystoreService/default" in text,
              "declares IKeystoreService/default as fqname")
        check("<name>android.hardware.boot</name>" in text,
              "declares android.hardware.boot")
        check("IBootControl/default" in text,
              "declares IBootControl/default as fqname")
        check("android.hidl.manager" in text,
              "preserves the pre-existing hidl.manager hal")
        check(text.count("</manifest>") == 1, "exactly one closing manifest tag")

        print("[3] both fragments are reparsed by libvintf")
        kt = ks2.read_text(encoding="utf-8")
        check("<fqname>IKeystoreService/default</fqname>" in kt,
              "keystore2 fragment uses <fqname> not <interface>")
        # Strip comments first: the fragment's explanatory comment names the
        # rejected shape, and only real markup decides what libvintf parses.
        kt_markup = re.sub(r"<!--.*?-->", "", kt, flags=re.S)
        check("<interface>" not in kt_markup,
              "no live HIDL <interface> element remains")
        bt = boot.read_text(encoding="utf-8")
        check('type="framework"' in bt,
              "boot fragment retyped device -> framework")
        check("<fqname>IBootControl/default</fqname>" in bt,
              "boot fragment uses <fqname>")

        print("[4] init can lazy-start keystore2 by interface name")
        rt = rc.read_text(encoding="utf-8")
        check("interface aidl android.system.keystore2.IKeystoreService/default" in rt,
              "keystore2.rc declares interface aidl")
        check("start keystore2" in rt, "on late-init trigger preserved")

        print("[5] LF endings only (a CRLF manifest is not parsed)")
        for path in (ks2, boot, rc, main_xml):
            raw = path.read_bytes()
            check(b"\r\n" not in raw, f"{path.name} has no CRLF")

        print("[6] idempotent: a second run changes nothing")
        before = {p: p.read_bytes() for p in (ks2, boot, rc, main_xml)}
        r2 = subprocess.run([sys.executable, str(FIXER), str(root)],
                            capture_output=True, text=True)
        check(r2.returncode == 0, "second run exits 0")
        check("nothing to do" in r2.stdout, "second run reports no changes")
        after = {p: p.read_bytes() for p in (ks2, boot, rc, main_xml)}
        check(before == after, "byte-identical after re-run")

    print()
    if failures:
        print(f"FAILED ({len(failures)}):")
        for f in failures:
            print("   -", f)
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())

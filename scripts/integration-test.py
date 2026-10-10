#!/usr/bin/env python3
"""Integration test: run the fixer against the REAL file set.

Unlike verify-vintf-fix.py (which uses synthetic inputs), this assembles a
recovery root out of the actual repository tree plus the files AERA's own
bootable/recovery build installs, then runs fix-recovery-vintf.py and asserts
the reader-visible result.

Inputs:
  --repo   aera-twrp / aera-recovery-push checkout (device tree)
  --aera   AERA android_bootable_recovery checkout (prebuilt etc/ files)

Run:
  python integration-test.py --repo <path> --aera <path>
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXER = HERE / "fix-recovery-vintf.py"

# AOSP system/core/healthd / boot HAL manifest, as installed by the AOSP
# boot-control module into /system/etc/vintf/manifest/.  The declared type is
# "device" here, which is what this tree actually shipped.
AOSP_BOOT_FRAGMENT = """<manifest version="1.0" type="device">
    <hal format="aidl">
        <name>android.hardware.boot</name>
        <version>1</version>
        <fqname>IBootControl/default</fqname>
    </hal>
</manifest>
"""

failures: list = []


def check(cond: bool, label: str) -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        failures.append(label)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", type=Path, required=True)
    ap.add_argument("--aera", type=Path, required=True)
    args = ap.parse_args()

    repo, aera = args.repo, args.aera
    src_root = repo / "recovery" / "root"
    aera_init = aera / "etc" / "init"
    for p in (src_root, aera_init):
        if not p.is_dir():
            raise SystemExit(f"not a directory: {p}")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "root"
        # 1. the device tree's ramdisk, exactly as it is in git
        shutil.copytree(src_root, root, symlinks=True)

        # 2. the files AERA's etc/Android.mk installs to TARGET_RECOVERY_ROOT_OUT
        manifest_dir = root / "system" / "etc" / "vintf" / "manifest"
        init_dir = root / "system" / "etc" / "init"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        init_dir.mkdir(parents=True, exist_ok=True)
        for name in ("android.system.keystore2-service.xml", "keystore2.rc"):
            shutil.copy2(aera_init / name, (manifest_dir if name.endswith(".xml")
                                            else init_dir) / name)
        # the AOSP boot fragment, which is present on device but lives outside
        # these two trees
        (manifest_dir / "android.hardware.boot-service.default.xml").write_text(
            AOSP_BOOT_FRAGMENT, encoding="utf-8", newline="\n")

        print("[0] reproduce: keystore2 must NOT be declared anywhere yet")
        main_xml = root / "system" / "manifest.xml"
        fragment = manifest_dir / "android.system.keystore2-service.xml"
        pre_main = main_xml.read_text(encoding="utf-8")
        pre_frag = fragment.read_text(encoding="utf-8")
        check("android.system.keystore2" not in pre_main,
              "repo /system/manifest.xml does not declare keystore2")
        check("android.hardware.boot" not in pre_main,
              "repo /system/manifest.xml does not declare boot")
        check("<fqname>" not in pre_frag,
              "AERA prebuilt fragment has no <fqname> (as shipped)")
        check(not (root / "system" / "etc" / "vintf" / "manifest.xml").exists(),
              "no /system/etc/vintf/manifest.xml, so the fragment dir is dead")
        check('type="device"' in (manifest_dir
                                  / "android.hardware.boot-service.default.xml")
              .read_text(encoding="utf-8"),
              "AOSP boot fragment is type=device (wrong for a framework dir)")

        print("[1] apply the fix to the real file set")
        r = subprocess.run([sys.executable, str(FIXER), str(root)],
                           capture_output=True, text=True)
        for line in r.stdout.strip().splitlines():
            print("   " + line.replace(str(tmp), "<tmp>"))
        check(r.returncode == 0, "fixer exits 0")

        print("[2] the framework manifest now declares both AIDL hal")
        after = main_xml.read_text(encoding="utf-8")
        check('type="framework"' in after,
              "/system/manifest.xml is still a framework manifest")
        check("android.system.keystore2" in after, "keystore2 declared")
        check(re.search(r"<fqname>IKeystoreService/default</fqname>", after)
              is not None, "IKeystoreService/default as fqname")
        check("android.hardware.boot" in after, "boot declared")
        check(re.search(r"<fqname>IBootControl/default</fqname>", after)
              is not None, "IBootControl/default as fqname")
        check("android.hidl.manager" in after and "android.hidl.token" in after,
              "existing hidl.manager + hidl.token preserved")
        check(after.count("</manifest>") == 1, "one closing manifest tag")

        print("[3] the legacy branch stays the active branch")
        check(not (root / "system" / "etc" / "vintf" / "manifest.xml").exists(),
              "fixer did NOT create the main framework manifest")

        print("[4] keymint fragment is left alone (still type=device)")
        km = manifest_dir / "astonc.keymint.xml"
        if km.exists():
            check('type="device"' in km.read_text(encoding="utf-8"),
                  "astonc.keymint.xml untouched and still type=device")

        print("[5] init can lazy-start keystore2")
        rc = (init_dir / "keystore2.rc").read_text(encoding="utf-8")
        check("interface aidl android.system.keystore2.IKeystoreService/default"
              in rc, "interface aidl line added to keystore2.rc")

        print("[6] every manifest is LF-only")
        for p in (main_xml, fragment,
                  manifest_dir / "android.hardware.boot-service.default.xml",
                  init_dir / "keystore2.rc"):
            check(b"\r\n" not in p.read_bytes(), f"{p.name} has no CRLF")

        print("[7] idempotent")
        before = {p: p.read_bytes() for p in
                  (main_xml, fragment,
                   manifest_dir / "android.hardware.boot-service.default.xml",
                   init_dir / "keystore2.rc")}
        r2 = subprocess.run([sys.executable, str(FIXER), str(root)],
                            capture_output=True, text=True)
        check("nothing to do" in r2.stdout, "second run reports no changes")
        check(before == {p: p.read_bytes() for p in before},
              "byte-identical after re-run")

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

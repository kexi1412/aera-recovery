#!/usr/bin/env python3
"""Force the vendor.gatekeeper properties into the recovery ramdisk prop file.

WHY THIS EXISTS
---------------
device.mk sets them with `PRODUCT_VENDOR_PROPERTIES`:

    PRODUCT_VENDOR_PROPERTIES += \
        vendor.gatekeeper.disable_spu=true \
        vendor.gatekeeper.is_security_level_spu=0

PRODUCT_VENDOR_PROPERTIES is packaged into /vendor/build.prop, i.e. the vendor
image. This device builds a dedicated recovery ramdisk with

    PRODUCT_BUILD_VENDOR_IMAGE := false

and a dedicated recovery partition (PRODUCT_BUILD_RECOVERY_IMAGE := true, and
recovery's init.rc symlinks /system/etc -> /etc). No vendor partition is mounted
in recovery, so those two properties are never present in the recovery
environment at all.

astonc.gatekeeper.rc gates the Gatekeeper service on exactly that property:

    on property:vendor.gatekeeper.is_security_level_spu=0
        start gatekeeper-1-0

=> the trigger never fires, `gatekeeper-1-0` stays `disabled` forever, and
TWRP's decrypt path gets "fail to get Gatekeeper service" after it has already
read the DE/SP metadata. That is the observed stuck point (recorded in
config/gatekeeper-inputs.json: "build-20 user PIN attempts read DE SP metadata
then fail to get Gatekeeper service; verify not reached").

Recovery reads its properties from the ramdisk's /prop.default, which
BOARD_RECOVERY_IMAGE_PREPARE already rewrites for the KeyMint OS identity via
scripts/prepare-crypto-properties.py. This script applies the same, already
proven technique to the two gatekeeper properties.

Verified property name: `vendor.gatekeeper.disable_spu` is read directly by the
target HAL (string present in android.hardware.gatekeeper@1.0-impl-qti.so).
`vendor.gatekeeper.is_security_level_spu` is the target rc's trigger property.

Host packaging only: no device access, no runtime setprop, no compiled
SDK/API/boot/AVB metadata change.
"""
import argparse
from pathlib import Path

WANTED = {
    "vendor.gatekeeper.disable_spu": "true",
    "vendor.gatekeeper.is_security_level_spu": "0",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("properties", type=Path)
    args = ap.parse_args()

    path = args.properties
    if not path.is_file():
        raise SystemExit(f"missing recovery property file: {path}")

    lines = path.read_text().splitlines(keepends=True)
    out, seen = [], set()
    for line in lines:
        if not line.startswith("#") and "=" in line:
            name = line.split("=", 1)[0].strip()
            if name in WANTED:
                # Rewrite in place, never duplicate: a duplicate key would make
                # the effective value depend on property-service ordering.
                out.append(f"{name}={WANTED[name]}\n")
                seen.add(name)
                continue
        out.append(line)
    for name, value in WANTED.items():
        if name not in seen:
            out.append(f"{name}={value}\n")
    path.write_text("".join(out))
    print("gatekeeper props ensured:", ", ".join(sorted(WANTED)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

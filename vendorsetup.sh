#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Also explicitly sourced by the host build wrapper before envsetup/lunch.
FDEVICE="astonc"

aera_get_target_device() {
    local chkdev=$(echo "$BASH_SOURCE" | grep -w $FDEVICE)
    if [ -n "$chkdev" ]; then
        AERA_BUILD_DEVICE="$FDEVICE"
    else
        chkdev=$(set | grep BASH_ARGV | grep -w $FDEVICE)
        [ -n "$chkdev" ] && AERA_BUILD_DEVICE="$FDEVICE"
    fi
}

if [ -z "$1" -a -z "$AERA_BUILD_DEVICE" ]; then
    aera_get_target_device
fi

if [ "$1" = "$FDEVICE" -o "$AERA_BUILD_DEVICE" = "$FDEVICE" ]; then
    export LC_ALL="C"

    # A/B recovery-partition device; keep the installed boot chain intact.
    export AERA_AB_DEVICE=1
    export AERA_VIRTUAL_AB_DEVICE=1
    export AERA_AB_DEVICE_WITH_RECOVERY_PARTITION=1
    export AERA_VANILLA_BUILD=1
    export AERA_PRODUCT_PREFIX=AERA
    export AERA_BUILD_STATUS=Unofficial
    export AERA_BUILD_TYPE=Beta

    # AERA begins at R1.0; do not append a legacy maintainer patch suffix.
    unset AERA_MAINTAINER_PATCH_VERSION

    # Keep magiskboot/repacking support, but omit the bundled installer ZIP and
    # the AROMA file manager. AERA owns the WPE runtime; this device's recovery
    # partition is a fixed 100 MiB.
    export AERA_DELETE_MAGISK_ADDON=1
    export AERA_USE_UPDATED_MAGISKBOOT=1

    # Runtime toolset staged in the ramdisk, not on encrypted /sdcard.
    export AERA_USE_TAR_BINARY=1
    export AERA_USE_SED_BINARY=1
    export AERA_USE_LZ4_BINARY=1
    export AERA_USE_ZSTD_BINARY=1
    export AERA_USE_DATE_BINARY=1
    export AERA_USE_GREP_BINARY=1
    export AERA_USE_BUSYBOX_BINARY=1
    export AERA_USE_XZ_UTILS=1
    export AERA_USE_FSCK_EROFS_BINARY=1
    export AERA_USE_PATCHELF_BINARY=1
    export AERA_USE_NANO_EDITOR=1

    # AERA keeps user settings on the data partition, misc files on /sdcard.
    export AERA_SETTINGS_ROOT_DIRECTORY=/data/recovery
    export AERA_MISCELLANEOUS_ROOT_DIRECTORY=/sdcard
    export AERA_ALLOW_EARLY_SETTINGS_LOAD=1

    # Root add-ons verified on this device.
    export AERA_USE_DMSETUP=1
    export AERA_ENABLE_KERNELSU_SUPPORT=1
    export AERA_ENABLE_KERNELSU_NEXT_SUPPORT=1
    export AERA_ENABLE_SUKISU_SUPPORT=1

    # Advanced security prompt for a user-facing recovery.
    export AERA_ADVANCED_SECURITY=1

    # Keep the installed splash; do not replace the boot splash partition.
    export AERA_NO_SPLASH_CHANGE=1
    export AERA_NO_REFLASH_CURRENT_RECOVERY=1

    # Actual hole y=40..112, center=76. Runtime scale is x=1264/1080, y=1.
    # Center the 56px status line at y=76; leave 40px below the hole for chrome.
    export AERA_SCREEN_H=2780
    export AERA_STATUS_H=152
    # Fox's right anchor is 1060, already 20 logical px inside a 1080px theme.
    # 60 on the left and 20+40 on the right give equal ~70px native margins.
    export AERA_STATUS_INDENT_LEFT=60
    export AERA_STATUS_INDENT_RIGHT=40
    export AERA_STATUS_ICONS_ALIGN=center

    # Beijing, UTC+8. POSIX TZ uses the opposite sign; saved user settings win.
    export AERA_DEFAULT_TIMEZONE="CST-8"

    # China rom identifiers for OTA/device assertions.
    export TARGET_DEVICE_ALT="astonc,aston,OP5CF9L1,PJE110"
    export AERA_TARGET_DEVICES="$TARGET_DEVICE_ALT"
fi
#

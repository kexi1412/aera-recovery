#
# Copyright (C) 2026 AERA Recovery Project contributors
# SPDX-License-Identifier: Apache-2.0
#
# AERA device configuration for OnePlus Ace 3 (PJE110 / astonc).
# Public device knobs use the AERA_ namespace exclusively; the shared AERA
# build system translates them to their backend names.
#

# Maintainer identity shown in the AERA UI. This string is what the About page
# renders in its MAINTAINER row:
#   aera_astonc.mk (here) -> aera_build.mk appends -DAERA_MAINTAINER
#   -> data.cpp stores it as the "aera_maintainer" constant
#   -> aera_backend.cpp reads it -> about_scene.cpp IdentityRow("MAINTAINER", ...)
# AERA begins at R1.0; no legacy maintainer patch suffix is appended.
AERA_MAINTAINER := kexi1412

# OnePlus Ace 3 is 1264x2780. The punch-hole occupies y=40..112 (center 76);
# leave 40px below the hole for the status chrome.
AERA_SCREEN_H := 2780
AERA_HIDE_NOTCH := 1
# Runtime scale is x=1264/1080, y=1. Center the 56px status line at y=76.
AERA_STATUS_H := 152
# Right anchor 1060 is already 20 logical px inside a 1080px theme; 60/40 give
# equal ~70px native margins at the boot resolution.
AERA_STATUS_INDENT_LEFT := 60
AERA_STATUS_INDENT_RIGHT := 40
AERA_STATUS_ICONS_ALIGN := center
AERA_OPTIONS_LIST_NUM := 6
AERA_USE_GREEN_LED := 0

# Beijing, UTC+8. POSIX TZ uses the opposite sign; saved user settings win.
AERA_DEFAULT_TIMEZONE := CST-8

# Device behaviour ported from the verified astonc configuration.
AERA_ENABLE_ALL_PARTITION_TOOLS := 1
AERA_WORKAROUND_BACKUP_BUG := 1
AERA_USE_AIDL_BOOT_CONTROL := 1
AERA_FORCE_DATA_FORMAT_F2FS := 1
AERA_UNBIND_SDCARD_F2FS := 1
AERA_WIPE_METADATA_AFTER_DATAFORMAT := 1
AERA_DISPLAY_FORMAT_FILESYSTEMS_DEBUG_INFO := 1
AERA_NO_RELOAD_AFTER_DECRYPTION := 1
AERA_NO_TREBLE_COMPATIBILITY_CHECK := 1
AERA_AB_DEVICE_WITH_RECOVERY_PARTITION := 1
AERA_ENABLE_FRP_ADDON := 1
AERA_USE_LZ4_COMPRESSION := 1

# Keep firmware/module loading on the installed vendor_boot provider; the
# matched ABI modules already live there (see config/kernel-modules.json).
#
# The Wi-Fi counterparts (AERA_LOAD_VENDOR_MODULES, AERA_ENABLE_WLAN) are not
# set. They cannot be enabled independently of the recovery module loader, and
# that loader is deliberately off in BoardConfig.mk - see the note there.
AERA_LOAD_VENDOR_MODULES_EXCLUDE_GKI := 1

# Root Manager, the offline KernelSU / KernelSU-Next / SukiSU-Ultra providers.
#
# Upstream commit 2782981 ("root: add a unified Root Manager flag for device
# and PC tools") merged the three per-provider switches into this single knob
# and defaulted it to 0 in aera_config.mk:7. The three switches this file used
# to export through vendorsetup.sh are no longer read anywhere, so without
# this line the Menu row is skipped at runtime, the providers are not staged
# into system/etc/aera/root-providers and the PC Root tools are hidden.
#
# 1 is the only value that enables anything: the flag reaches the UI as
# -DAERA_ENABLE_ROOT_MANAGER (aera_build.mk:823 and aeraui_defaults.go:35, the
# latter reading it back through the twrpVarsPlugin vendor config) and gates
# the packaging copy at aera_build.mk:913. All three providers travel together
# in prebuilt/aera/root, so this is a single on/off decision, not a per-provider
# one.
AERA_ENABLE_ROOT_MANAGER := 1


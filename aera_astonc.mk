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

# The backend compares this against the literal string "true"
# (Android.mk:377: ifeq ($(TW_LOAD_VENDOR_MODULES_EXCLUDE_GKI),true)), so the
# value must be literally true; `1` would silently not match and leave the
# switch inert. It is set anyway because with the module loader now enabled the
# switch does take effect: leaving it unset adds /vendor/lib/modules/<maj>.<min>-gki
# and /lib/modules/<maj>.<min>-gki to the search list, and neither exists here
# (/vendor/lib/modules is a symlink to /vendor_dlkm/lib/modules, and the
# recovery ramdisk has no /lib/modules). Only the vendor_boot and vendor_dlkm
# copies of the stack matter.
AERA_LOAD_VENDOR_MODULES_EXCLUDE_GKI := true

# Wi-Fi: QCA6490 attached over PCIe, driven by the kiwi_v2 host driver.
#
# The module loader is MANDATORY for Wi-Fi. AERA_ENABLE_WLAN alone only compiles
# wlan.cpp in; the driver stack still has to reach the kernel.
#
# Note the two ramdisks are different, which is easy to conflate (see the
# EXCLUDE_GKI comment above, where the recovery ramdisk is the one with no
# /lib/modules):
#
#   recovery.img ramdisk      no /lib/modules at all.
#   vendor_boot ramdisk       mounted at /lib/modules and DOES carry a module
#                             tree (config/kernel-modules.json lists 450 files),
#                             but the only Wi-Fi entries in it are cfg80211.ko
#                             and mac80211.ko (modules.load.recovery:405-406).
#
# So cfg80211/mac80211 may already be present, while the CNSS service layer, the
# PCIe host driver and kiwi_v2 are NOT in that tree at all - they exist only in
# /vendor_dlkm/lib/modules, which nothing mounts or loads unless the recovery
# module loader does it. With the stack absent, wlan0 never appears and
# Wlan::StartInitSupplicantService() times out on init.svc.wpa_supplicant.
#
# KernelModuleLoader::Load_Vendor_Modules() matches this list by EXACT filename
# including the .ko suffix against the entries present in its module directories,
# and libmodprobe then resolves dependencies itself from modules.dep with
# strict=false, so the order below is not load order. All of these live in
# /vendor_dlkm/lib/modules, which is where the loader looks once
# /vendor/lib/modules yields nothing useful.
#
# qca_cld3_kiwi.ko is deliberately ABSENT: it refuses this silicon with "Driver
# built for chip ver 0x1, enumerated ver 0x2, reject unsupported driver", while
# qca_cld3_kiwi_v2.ko binds and owns cnss2 (live /proc/modules:
# "cnss2 606208 1 kiwi_v2, Live").
#
# rmnet_wlan.ko is deliberately ABSENT: it fails with "Unknown symbol
# rmnet_module_hook_register (err -2)" because rmnet_core.ko is not in this
# module directory, and the rmnet data path is not needed for wlan0 to appear.
#
# The order reproduces this device's stock /vendor_dlkm/lib/modules/modules.load:
# cfg80211:264, mac80211:265, qca_cld3_kiwi_v2:380, cnss2:382,
# cnss_plat_ipc_qmi_svc:383, wlan_firmware_service:384, cnss_nl:385,
# cnss_prealloc:386, cnss_utils:387. cfg80211/mac80211 first is what makes the
# rest resolvable; the QCA stack then follows in vendor order. This ordering only
# matters for manual insmod probing, where loading cnss2 before its QMI
# dependencies fails with "Unknown symbol wlfw_*_ind_msg_v01_ei".
#
# The value is a make string and is passed to LOCAL_CFLAGS as
# -DTW_LOAD_VENDOR_MODULES=<value>. Keep the quotes so the whole list stays one
# compiler argument: kernel_module_loader.cpp recovers it with
# EXPAND()/STRINGIFY, and an unquoted list would make the compiler see the words
# after the first as separate arguments.
# This is the public AERA_* interface value. It is NOT what gates the build.
#
# The build is gated by TW_LOAD_VENDOR_MODULES, which bootable/recovery/
# Android.mk:369 tests as
#     ifneq ($(strip $(TW_LOAD_VENDOR_MODULES) $(TW_POST_DECRYPT_MODULES)),)
# to decide whether kernel_module_loader.cpp is compiled at all. BoardConfig.mk
# sets that internal name with the identical list; the aera_config.mk mapping
# runs after BoardConfig.mk and overwrites it, which is harmless exactly as long
# as the two stay in sync - the CI gate compares them.
#
# What actually makes the modules load is recovery/root/system/bin/
# beforemodules.sh, which kernel_module_loader.cpp:314 runs before it resolves
# any module directory (still present in the aera-16.0 upstream of 2026-10-23,
# which now loads the staged modules with finit_module). See that file for why
# libmodprobe alone is not enough.
AERA_LOAD_VENDOR_MODULES := "cfg80211.ko mac80211.ko cnss_prealloc.ko cnss_utils.ko cnss_plat_ipc_qmi_svc.ko wlan_firmware_service.ko cnss_nl.ko cnss2.ko qca_cld3_kiwi_v2.ko"

# Wi-Fi UI and backend. wlan.cpp / aera_wifi_dispatcher.cpp /
# aera_supplicant_link.cpp / aera_adbd.cpp / the Network scenes and
# nas/NasManager.cpp are only compiled when OF_ENABLE_WLAN == 1, and
# aera_build.mk:876 tests `ifeq ($(OF_ENABLE_WLAN),1)`, so the value must be
# literally 1 - not true. BoardConfig.mk sets OF_ENABLE_WLAN for that reason,
# using the same mapping this line feeds; it is kept here as the public
# interface value.
#
# Enabling this also makes aera_build.mk require external/wpa_supplicant_8 (it
# hard-errors if absent; the AERA manifest provides it) and adds
# wpa_supplicant, wpa_cli, dhcptool, aera-netfs, the supplicant VINTF manifest
# and the wifi keystore/keymint/common NDK libraries to the recovery image.
#
# AERA_ENABLE_MOBILE_DATA is deliberately NOT set: the cellular provider needs
# a device-side RIL/QMI implementation this tree does not ship.
AERA_ENABLE_WLAN := 1

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


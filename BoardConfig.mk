# SPDX-License-Identifier: Apache-2.0
DEVICE_PATH := device/oneplus/astonc

# Lineage astonc/sm8550-common, confirmed by current PJE110 hardware.
TARGET_ARCH := arm64
TARGET_ARCH_VARIANT := armv8-2a-dotprod
TARGET_CPU_ABI := arm64-v8a
TARGET_CPU_VARIANT := generic
TARGET_CPU_VARIANT_RUNTIME := kryo300
TARGET_BOARD_PLATFORM := kalama
TARGET_BOOTLOADER_BOARD_NAME := kalama
TARGET_NO_BOOTLOADER := true

# Actual current vendor/build.prop + normal Android getprop; inventory records
# both source hashes. Empty vendor patch was parsed as 0 by QTI configure.
VENDOR_SECURITY_PATCH := 2026-09-01

# Dedicated recovery uses the current boot kernel and vendor_boot ramdisk.
TARGET_NO_KERNEL := true
TARGET_KERNEL_ARCH := arm64
BOARD_BOOT_HEADER_VERSION := 4
BOARD_KERNEL_PAGESIZE := 4096
BOARD_MKBOOTIMG_ARGS += --header_version 4
BOARD_RAMDISK_USE_LZ4 := true
BOARD_EXCLUDE_KERNEL_FROM_RECOVERY_IMAGE := true

# The generic init_boot ramdisk is thin. Dedicated recovery needs these base
# mountpoints before init.rc actions: init.cpp mounts /apex with a fatal check;
# TWRP init.rc mounts /acct and /config. Stock/current vendor_boot provides none.
BOARD_RECOVERY_IMAGE_PREPARE += mkdir -p $(addprefix $(TARGET_RECOVERY_ROOT_OUT)/,acct apex config)
# QTI reads OS identity during its init-time constructor, before TWRP's later
# AERA_OVERRIDE_SYSTEM_PROPS can run. Only rewrite the recovery property file;
# compiled SDK/API and boot/AVB version metadata remain the pinned AOSP values.
BOARD_RECOVERY_IMAGE_PREPARE += && python3 $(DEVICE_PATH)/scripts/prepare-crypto-properties.py $(TARGET_RECOVERY_ROOT_OUT)/prop.default $(DEVICE_PATH)/config/crypto-version-inputs.json
# The two vendor.gatekeeper.* properties are NOT injected here.
# An earlier revision did that (scripts/ensure-gatekeeper-props.py), on the
# theory that they only exist in /vendor/build.prop and therefore could not
# reach recovery, leaving gatekeeper-1-0 disabled forever. That was measured and
# falsified: the properties are already present in the ramdisk prop.default of
# the shipped image, the trigger fires, and the service is up and registered.
# See the note in recovery/root/system/etc/init/astonc.gatekeeper.rc.
# AERA recovery resolves almost every config path through /etc (recovery.fstab,
# twrp.flags, twrp.fstab, task_profiles.json, cgroups.json, ...), and its
# init.rc does `symlink /system/etc /etc` at runtime. That symlink fails if /etc
# already exists as a real directory in the ramdisk, which is what happens on
# this tree: the AERA build script `mkdir -p $RAMDISK/etc` runs before the
# device-tree copy, so a bare /etc/ (holding only aera.cfg) ends up in the
# cpio. Boot logs confirm the fallout: `init: failed to read
# '/etc/recovery.fstab'` and logd crash-looping on missing
# /etc/task_profiles.json. Make /etc a real symlink to /system/etc in the final
# ramdisk, relocating the few files the script left there. Runs last, after the
# AERA script and the device-tree copy.
BOARD_RECOVERY_IMAGE_PREPARE += && if [ ! -L $(TARGET_RECOVERY_ROOT_OUT)/etc ]; then cp -af $(TARGET_RECOVERY_ROOT_OUT)/etc/. $(TARGET_RECOVERY_ROOT_OUT)/system/etc/ 2>/dev/null; rm -rf $(TARGET_RECOVERY_ROOT_OUT)/etc; ln -s /system/etc $(TARGET_RECOVERY_ROOT_OUT)/etc; fi
# Recovery stalled at the PIN step on this tree.  Verified on hardware:
# servicemanager rejected keystore2's registration because
# android.system.keystore2.IKeystoreService/default was not declared in any
# VINTF manifest it could read, so the process aborted and Decrypt_DE() spun
# forever:
#   servicemanager: Could not find android.system.keystore2.IKeystoreService/
#       default in the VINTF manifest
#   init: Service 'keystore2' (pid 2885) received signal 6
# The framework manifest loader reads /system/etc/vintf/manifest/*.xml ONLY if
# /system/etc/vintf/manifest.xml exists (VintfObject: if fetchOneHalManifest(
# kSystemManifest) == OK then addDirectoryManifests(kSystemManifestFragmentDir)
# else kSystemLegacyManifest).  This tree ships no
# /system/etc/vintf/manifest.xml, so the whole framework fragment directory is
# dead and the framework manifest is /system/manifest.xml alone -- which
# declared only hidl.manager and hidl.token, i.e. no AIDL HAL at all.
# The vendor side is the control: it DOES ship /vendor/etc/vintf/manifest.xml,
# so its fragment directory is parsed and gatekeeper/keymint register normally.
# Same loader, opposite outcome, decided solely by the presence of the main
# manifest.  Declare the AIDL interfaces there.  Runs last so the AERA prebuilt
# install and the device-tree copy cannot overwrite the repaired files.
BOARD_RECOVERY_IMAGE_PREPARE += && python3 $(DEVICE_PATH)/scripts/fix-recovery-vintf.py $(TARGET_RECOVERY_ROOT_OUT)

TARGET_USERIMAGES_USE_EXT4 := true
TARGET_USERIMAGES_USE_F2FS := true
TARGET_COPY_OUT_VENDOR := vendor
TARGET_COPY_OUT_ODM := odm
TARGET_COPY_OUT_PRODUCT := product
TARGET_COPY_OUT_SYSTEM_EXT := system_ext
TARGET_COPY_OUT_VENDOR_DLKM := vendor_dlkm
TARGET_COPY_OUT_SYSTEM_DLKM := system_dlkm

# Metadata/DE/PIN CE passed TWRP build-21. AERA requires its own tests;
# final enforcing SELinux policy remains work.
AERA_INCLUDE_CRYPTO := true
AERA_INCLUDE_CRYPTO_FBE := true
AERA_USE_FSCRYPT_POLICY := 2
AERA_THEME := portrait_hdpi
AERA_DEVICE_VERSION := astonc-aera-bringup
TARGET_USES_LOGD := true
AERA_INCLUDE_LOGCAT := true

# Actual 512 GB PJE110 capacities, not the Lineage 256 GB userdata constant.
BOARD_BOOTIMAGE_PARTITION_SIZE := 201326592
BOARD_INIT_BOOT_IMAGE_PARTITION_SIZE := 8388608
BOARD_VENDOR_BOOTIMAGE_PARTITION_SIZE := 201326592
BOARD_RECOVERYIMAGE_PARTITION_SIZE := 104857600
BOARD_DTBOIMG_PARTITION_SIZE := 25165824
BOARD_FLASH_BLOCK_SIZE := 262144

AB_OTA_UPDATER := true
AB_OTA_PARTITIONS := boot init_boot vendor_boot recovery dtbo vbmeta vbmeta_system vbmeta_vendor system system_ext product vendor odm vendor_dlkm system_dlkm
BOARD_SUPER_PARTITION_SIZE := 16642998272
BOARD_SUPER_PARTITION_GROUPS := qti_dynamic_partitions
BOARD_QTI_DYNAMIC_PARTITIONS_SIZE := 16638803968
BOARD_QTI_DYNAMIC_PARTITIONS_PARTITION_LIST := system system_ext vendor product my_product odm my_engineering vendor_dlkm system_dlkm my_stock my_heytap my_carrier my_region my_bigball my_manifest
BOARD_VENDORIMAGE_FILE_SYSTEM_TYPE := erofs
BOARD_ODMIMAGE_FILE_SYSTEM_TYPE := erofs
BOARD_PRODUCTIMAGE_FILE_SYSTEM_TYPE := erofs
BOARD_SYSTEM_EXTIMAGE_FILE_SYSTEM_TYPE := erofs
BOARD_SYSTEMIMAGE_FILE_SYSTEM_TYPE := erofs
BOARD_VENDOR_DLKMIMAGE_FILE_SYSTEM_TYPE := ext4
BOARD_SYSTEM_DLKMIMAGE_FILE_SYSTEM_TYPE := ext4
BOARD_USES_METADATA_PARTITION := true

# Hash footer for image integrity, no OEM signing identity and no vbmeta writes.
BOARD_AVB_ENABLE := true
BOARD_AVB_RECOVERY_ALGORITHM := NONE

TARGET_RECOVERY_FSTAB := $(DEVICE_PATH)/recovery/root/system/etc/recovery.fstab
BOARD_SUPPRESS_SECURE_ERASE := true
RECOVERY_SDCARD_ON_DATA := true

# Lineage 1264x2780 and current backlight max=4094; DRM uses native mode.
TARGET_RECOVERY_PIXEL_FORMAT := RGBX_8888
AERA_BRIGHTNESS_PATH := /sys/class/backlight/panel0-backlight/brightness
AERA_MAX_BRIGHTNESS := 4094
AERA_DEFAULT_BRIGHTNESS := 200
# Approved build-6 sysfs capture: cpuss-0 reports millidegrees Celsius;
# zone0 is the unavailable radio sensor "pa". Resolve by type at boot.
AERA_CUSTOM_CPU_TEMP_PATH := /tmp/astonc-cpu-temp
AERA_DEFAULT_LANGUAGE := zh_CN
AERA_EXTRA_LANGUAGES := true
# Approved build-10 capture: pm8xxx RTC counts from 1970 and the ROM stores
# ATS offsets under /data/vendor/time. kalama is absent from the auto-list.
# Use upstream RTC + ATS logic; never pin an offset to a host/build date.
TARGET_RECOVERY_QCOM_RTC_FIX := true
# The device owns recovery/root/init.recovery.usb.rc, so the legacy
# /sys/class/android_usb/android0 actions in TWRP etc/init.recovery.usb.rc must
# stay excluded. This kernel has no android0 gadget: ro.boot.usbcontroller is
# a600000.dwc3 and sys.usb.configfs=1, so the legacy writes would silently fail
# and the MTP handlers below would never run.
AERA_EXCLUDE_DEFAULT_USB_INIT := true

# MTP is NOT excluded. This knob is only half of what MTP needs:
#   * leaving AERA_EXCLUDE_MTP unset keeps TW_EXCLUDE_MTP empty, which is what
#     makes Android.mk pass -DTW_HAS_MTP and link libtwrpmtp-ffs.so;
#   * the FunctionFS node, the /dev/usb-ffs/mtp mount and the configfs bind
#     handlers that expose ffs.mtp to the host live in
#     recovery/root/init.recovery.usb.rc.
# Excluding it here would instead compile out the whole MTP path: without
# TW_HAS_MTP, TWPartitionManager::Enable_MTP() returns
# gui_err("no_mtp=MTP support not included").
#
# MTP additionally requires AERA_ADVANCED_SECURITY to stay unset. That knob maps
# to OF_ADVANCED_SECURITY, and twrp.cpp:317-321 turns it into
# tw_mtp_enabled=0 plus a stopped adbd ("ADB & MTP disabled by maintainer")
# before the autostart block at twrp.cpp:322-343 is even reached, so no configfs
# work below can bring MTP back at runtime. See vendorsetup.sh.
AERA_EXCLUDE_APEX := true
# XML is only needed by QSEE; package its supported AOSP vendor variant.
AERA_EXCLUDE_LIBXML2 := true

AERA_INCLUDE_FASTBOOTD := true
AERA_INCLUDE_LPDUMP := true
# The native AERA UI scales its 1440x3168 design surface to this panel.
AERA_UI_ADAPTIVE_RESOLUTION := true

# Missing DT_NEEDED closure of the upstream lpdump and unconditional keystore CLI.
# AOSP providers; target vendor HALs are separately pinned in crypto assets.
AERA_RECOVERY_ADDITIONAL_RELINK_LIBRARY_FILES += \
    $(TARGET_OUT_SHARED_LIBRARIES)/android.hardware.security.keymint-V4-ndk.so \
    $(TARGET_OUT_SHARED_LIBRARIES)/android.hardware.security.secureclock-V1-ndk.so \
    $(TARGET_OUT_SHARED_LIBRARIES)/android.security.apc-ndk.so \
    $(TARGET_OUT_SHARED_LIBRARIES)/android.system.keystore2-V5-ndk.so \
    $(TARGET_OUT_SHARED_LIBRARIES)/libfs_mgr_binder.so \
    $(TARGET_OUT_SHARED_LIBRARIES)/libkeymint_support.so \
    $(TARGET_OUT_SHARED_LIBRARIES)/libsnapshot.so \
    $(TARGET_OUT_SHARED_LIBRARIES)/libbinderdebug.so \
    $(TARGET_OUT_SHARED_LIBRARIES)/libhidl-gen-hash.so \
    $(TARGET_OUT_SHARED_LIBRARIES)/android.hardware.security.sharedsecret-V1-ndk.so \
    $(TARGET_OUT_SHARED_LIBRARIES)/android.hidl.memory@1.0.so \
    $(TARGET_OUT_SHARED_LIBRARIES)/android.hidl.memory.token@1.0.so \
    $(TARGET_OUT_SHARED_LIBRARIES)/libhidlmemory.so \
    $(TARGET_OUT_SHARED_LIBRARIES)/libdmabufheap.so \
    $(TARGET_OUT_SHARED_LIBRARIES)/libion.so

BOARD_VENDOR_SEPOLICY_DIRS += $(DEVICE_PATH)/sepolicy

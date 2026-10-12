#!/system/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# beforemodules.sh - deterministic QCA6490 (kiwi_v2) bring-up for astonc.
#
# WHY THIS FILE EXISTS
# --------------------
# KernelModuleLoader::Load_Vendor_Modules() calls
#
#     TWFunc::RunFoxScript("/system/bin/beforemodules.sh", "");   // line 301
#
# unconditionally, as the very first thing it does - before it resolves a
# single module directory and before libmodprobe is constructed. RunFoxScript()
# is a no-op when the path does not exist (twrp-functions.cpp:317), so upstream
# AERA and TWRP ship without this hook and nothing breaks. This tree adds it
# because the Wi-Fi stack should not depend on libmodprobe's dependency-file
# resolution succeeding.
#
# THE FAILURE MODE THIS GUARDS
# ----------------------------
# Everything downstream is silent-by-design:
#
#   * libmodprobe's InsmodWithDeps() does
#         if (dependencies.empty()) {
#             LOG(ERROR) << "Module " << module_name << " not in dependency file";
#             return false;
#         }
#     so if modules.dep is missing from the module directory - which is easy,
#     because Try_And_Load_Modules() bind-mounts a /tmp copy over the real
#     directory and Copy_Modules_To_Tmpfs() can only copy what it could read -
#     then EVERY requested module fails.
#   * LoadListedModules() is called with strict=false, so a failing module is
#     logged and skipped rather than aborting the build or the load.
#   * LoadWithAliases() does `if (!ModuleExists(module)) continue;`, so a module
#     that is not present in that directory is skipped with no error at all.
#
# Net effect: the loader can report success while cfg80211/mac80211 - the two
# modules without which not one symbol of the QCA6490 stack resolves - never
# reach the kernel. The only visible symptom is that /sys/class/net/wlan0 never
# appears and Wlan::Enable() times out in
# StartInitSupplicantService()/WaitForSupplicantReady().
#
# Loading them here, with insmod, is immune to all of the above. The loader's
# own post-processing then cooperates instead of fighting: 
# Skip_Loaded_Kernel_Modules() reads /proc/modules, removes anything already
# resident from kernel_modules_requested, and Write_Module_List() returns false
# when nothing is left, which Try_And_Load_Modules() translates into "already
# loaded" and Load_Vendor_Modules() into the expected module count. So a
# successful run here short-circuits the libmodprobe path cleanly.
#
# CONTRACT
# --------
# Never fail the boot. Every step is best-effort; the script always exits 0 and
# logs what it did to /tmp/recovery/beforemodules.log (and to stderr, which
# lands in the recovery log). A failure here degrades to upstream behaviour,
# it does not regress it.

LOG_FILE=/tmp/recovery/beforemodules.log

log() {
    echo "beforemodules: $*"
    [ -n "$LOG_FILE" ] && echo "beforemodules: $*" >> "$LOG_FILE" 2>/dev/null
    return 0
}

# mkdir -p, without relying on the flag being supported.
mkpath() {
    [ -d "$1" ] && return 0
    mkdir -p "$1" 2>/dev/null || mkdir "$1" 2>/dev/null
    return 0
}

mkpath /tmp/recovery 2>/dev/null
: > "$LOG_FILE" 2>/dev/null
log "start (pid $$)"

# ---------------------------------------------------------------------------
# 1. insmod / lsmod
# ---------------------------------------------------------------------------
# /system/bin/insmod is a toybox symlink in the AERA ramdisk and /sbin/busybox
# is always staged, so at least one of these resolves on every build variant.
INSMOD=""
for c in /system/bin/insmod /sbin/insmod; do
    if [ -x "$c" ]; then INSMOD="$c"; break; fi
done
if [ -z "$INSMOD" ] && [ -x /sbin/busybox ]; then
    log "using /sbin/busybox insmod"
    insmod_via_busybox() { /sbin/busybox insmod "$@"; }
    INSMOD=insmod_via_busybox
fi
if [ -z "$INSMOD" ]; then
    log "no insmod available; leaving module loading to the recovery binary"
    exit 0
fi
log "insmod: $INSMOD"

# The loaded-module name is not always the file name. The QCA6490 host driver
# is filed as qca_cld3_kiwi_v2.ko but registers as `kiwi_v2` - the live device
# reports "cnss2 606208 1 kiwi_v2, Live". Probing /proc/modules for the file
# name would therefore always say "not loaded", the retry loop would keep
# re-insmodding it, and every attempt after the first would fail with EEXIST and
# be reported as a failure. Map the exceptions; everything else matches.
modname() {
    case "$1" in
        qca_cld3_kiwi_v2.ko) echo "kiwi_v2" ;;
        qca_cld3_kiwi.ko)    echo "kiwi" ;;
        *)                   echo "${1%.ko}" ;;
    esac
}

is_loaded() {
    [ -r /proc/modules ] || return 1
    grep -q "^$(modname "$1") " /proc/modules 2>/dev/null
}

# ---------------------------------------------------------------------------
# 2. Make the module directories reachable
# ---------------------------------------------------------------------------
# This runs at kernel_module_loader.cpp:301, which is BEFORE the loader's own
# ven->Mount(true) / ven_dlkm->Mount(true) at :340/:370. So /vendor and
# /vendor_dlkm cannot be assumed to be mounted yet and have to be brought up
# here. Setup_Super_Devices() has already run - it is called from Process_Fstab
# (partitionmanager.cpp:541) before the fstab loop that ends in the
# Load_Vendor_Modules() call - so the dm-linear mapper nodes exist.
#
# /vendor is EROFS (recovery.fstab:7) and /vendor_dlkm is ext4 (:9); mounting
# one with the other's type just fails harmlessly, so every candidate type is
# tried rather than guessing. Slot-suffixed and mapper paths are both tried
# because the exact by-name layout depends on whether slotselect was applied.
mounted_at() {
    grep -q " $1 " /proc/mounts 2>/dev/null && return 0
    mountpoint -q "$1" 2>/dev/null && return 0
    return 1
}

try_mount() {
    _mp="$1"; _dev="$2"
    [ -b "$_dev" ] || return 1
    mkpath "$_mp"
    for _t in ext4 erofs f2fs vfat auto; do
        if mount -t "$_t" -o ro "$_dev" "$_mp" 2>/dev/null; then
            log "mounted $_dev -> $_mp ($_t)"
            return 0
        fi
    done
    return 1
}

ensure_mount() {
    _mp="$1"; shift
    if mounted_at "$_mp"; then return 0; fi
    for _d in "$@"; do
        try_mount "$_mp" "$_d" && return 0
    done
    return 1
}

_slot=$(getprop ro.boot.slot_suffix 2>/dev/null)
# The by-name entries on this device already resolve to the active slot, so an
# explicit suffix is only a second chance for layouts that keep both.
ensure_mount /vendor \
    /dev/block/mapper/vendor \
    /dev/block/by-name/vendor \
    "/dev/block/by-name/vendor${_slot}" || \
    log "could not mount /vendor"

ensure_mount /vendor_dlkm \
    /dev/block/mapper/vendor_dlkm \
    /dev/block/by-name/vendor_dlkm \
    "/dev/block/by-name/vendor_dlkm${_slot}" || \
    log "could not mount /vendor_dlkm"

# ---------------------------------------------------------------------------
# 3. Locate the module directory
# ---------------------------------------------------------------------------
# config/README.md names /lib/modules (the vendor_boot platform ramdisk) as the
# provider of record. /vendor/lib/modules is a symlink to
# /vendor_dlkm/lib/modules on this device, so it is the second choice; the
# dlkm mount point itself is the last resort because it is the only path that
# still works if the symlink's target is not mounted.
MODULE_DIR=""
for d in /lib/modules /vendor/lib/modules /vendor_dlkm/lib/modules; do
    if [ -d "$d" ]; then
        n=$(ls "$d"/*.ko 2>/dev/null | wc -l)
        log "candidate $d: $n .ko"
        if [ -z "$MODULE_DIR" ] && [ "$n" -gt 0 ] 2>/dev/null; then
            MODULE_DIR="$d"
        fi
    fi
done
if [ -z "$MODULE_DIR" ]; then
    log "no module directory with .ko files; nothing to do"
    exit 0
fi
log "module dir: $MODULE_DIR"

# ---------------------------------------------------------------------------
# 4. Load order
# ---------------------------------------------------------------------------
# Dependencies first. This is not the stock modules.load order (which is
# dependency-resolved by libmodprobe and therefore unconstrained): loading
# cnss2 before its QMI providers fails with
# "Unknown symbol wlfw_*_ind_msg_v01_ei", so the QMI service modules go first.
#
# cfg80211 must precede mac80211, and both must precede the QCA stack, or every
# subsequent insmod fails to resolve cfg80211_* symbols.
MODULES="cfg80211.ko mac80211.ko cnss_prealloc.ko cnss_utils.ko cnss_plat_ipc_qmi_svc.ko wlan_firmware_service.ko cnss_nl.ko cnss2.ko qca_cld3_kiwi_v2.ko"

# qca_cld3_kiwi.ko is deliberately absent: it refuses this silicon with
# "Driver built for chip ver 0x1, enumerated ver 0x2, reject unsupported driver",
# while qca_cld3_kiwi_v2.ko binds and owns cnss2.
# rmnet_wlan.ko is deliberately absent: it needs rmnet_module_hook_register,
# which lives in rmnet_core.ko outside this module directory.

# Optional prerequisites of cnss2 that also live in this directory. Attempted
# first and only when not already resident, so a kernel that already brought
# them up (or has them built in) is left untouched. A failure here is expected
# and harmless when no cnss2 provider in this directory needs them.
PREREQS="qmi_helpers.ko mhi.ko"

# ---------------------------------------------------------------------------
# 5. insmod with retry passes
# ---------------------------------------------------------------------------
# One pass loads whatever can be loaded right now; a module that failed only
# because a dependency had not landed yet succeeds on a later pass. This is the
# whole point of doing it in shell instead of trusting modules.dep to be
# present and correct.
insmod_one() {
    _name="$1"
    _path=""
    for d in "$MODULE_DIR" /lib/modules /vendor/lib/modules /vendor_dlkm/lib/modules; do
        if [ -f "$d/$_name" ]; then _path="$d/$_name"; break; fi
    done
    if [ -z "$_path" ]; then
        return 2   # absent here; not an error
    fi
    if is_loaded "$_name"; then
        return 3   # already resident
    fi
    if err=$($INSMOD "$_path" 2>&1); then
        log "loaded $_name from $_path"
        return 0
    fi
    LAST_ERR="$err"
    return 1
}

for p in $PREREQS; do
    insmod_one "$p"
done

pass=1
while [ "$pass" -le 4 ]; do
    progressed=0
    failed=""
    for m in $MODULES; do
        insmod_one "$m"
        case $? in
            0) progressed=1 ;;
            1) failed="$failed $m" ;;
        esac
    done
    log "pass $pass done (progressed=$progressed, still failing:$failed)"
    [ "$progressed" = "0" ] && break
    pass=$((pass + 1))
done

# ---------------------------------------------------------------------------
# 6. Report
# ---------------------------------------------------------------------------
loaded_count=0
missing=""
for m in $MODULES; do
    if is_loaded "$m"; then
        loaded_count=$((loaded_count + 1))
    else
        missing="$missing $m"
    fi
done
log "resident: $loaded_count / 9"
[ -n "$missing" ] && log "NOT resident:$missing"

# The driver binds cnss2 to the PCIe endpoint; the node is the only positive
# proof that the host driver really attached rather than merely loaded.
if [ -d /sys/kernel/cnss ]; then
    log "/sys/kernel/cnss present"
else
    log "/sys/kernel/cnss absent (cnss2 did not probe)"
fi

# ---------------------------------------------------------------------------
# 7. Firmware / calibration precondition
# ---------------------------------------------------------------------------
# cnss2 requests firmware by relative name through the kernel firmware_class
# loader, which searches ueventd's accumulated firmware_directories (the
# vendor copy already lists /vendor/firmware_mnt/image/, /mnt/vendor/persist/
# and /mnt/vendor/persist/copy/). Checked, not created: a missing calibration
# file is a provisioning problem, and fabricating one would hide it.
if [ -d /vendor/firmware_mnt/image ]; then
    n=$(ls /vendor/firmware_mnt/image 2>/dev/null | wc -l)
    log "firmware_mnt/image entries: $n"
else
    log "WARNING: /vendor/firmware_mnt/image not mounted - modem${ro.boot.slot_suffix} missing?"
fi
if [ -f /mnt/vendor/persist/wlan/WCNSS_qcom_cfg.ini ]; then
    log "calibration ini present"
else
    log "WARNING: /mnt/vendor/persist/wlan/WCNSS_qcom_cfg.ini missing"
fi

# Last words from the driver, in the recovery log where they are actually
# readable - dmesg is otherwise only reachable with a shell on the device.
dmesg 2>/dev/null | grep -i -E 'cnss|wlan|kiwi|wlfw' | tail -15 | while read -r l; do
    log "dmesg: $l"
done

log "done"
exit 0

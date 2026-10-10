#!/system/bin/sh
# Verify the recovery VINTF/AIDL fix on the device itself.
#
# Run from recovery (adb shell) after flashing the patched image, or after
# applying scripts/fix-recovery-vintf.py to the live ramdisk.
#
# exit 0 = fix is in effect; exit 1 = still broken.

echo "=== AERA recovery decrypt-fix verification ==="
date
echo

FAIL=0

# The framework loader reads /system/etc/vintf/manifest/*.xml ONLY if
# /system/etc/vintf/manifest.xml exists (libvintf: if fetchOneHalManifest(
# kSystemManifest) == OK then addDirectoryManifests(kSystemManifestFragmentDir)
# else kSystemLegacyManifest). Decide which location is authoritative here.
if [ -f /system/etc/vintf/manifest.xml ]; then
    LIVE_DIR=1
else
    LIVE_DIR=0
fi

echo "--- [1] which manifest does the framework loader actually use? ---"
if [ "$LIVE_DIR" = "1" ]; then
    echo "  /system/etc/vintf/manifest.xml EXISTS"
    echo "  -> fragment dir /system/etc/vintf/manifest/ is live"
else
    echo "  /system/etc/vintf/manifest.xml ABSENT"
    echo "  -> fragment dir is DEAD; the framework manifest is"
    echo "     /system/manifest.xml alone (legacy branch)"
fi
echo

echo "--- [2] is android.system.keystore2 declared where it will be read? ---"
KS2_OK=0
if [ "$LIVE_DIR" = "1" ]; then
    for f in /system/etc/vintf/manifest/*.xml; do
        if grep -l 'android.system.keystore2' "$f" >/dev/null 2>&1; then
            echo "  found in $f"
            grep -q '<fqname>IKeystoreService/default</fqname>' "$f" \
                && KS2_OK=1
        fi
    done
else
    if grep -q 'android.system.keystore2' /system/manifest.xml 2>/dev/null; then
        echo "  found in /system/manifest.xml"
        grep -q 'IKeystoreService/default' /system/manifest.xml \
            && KS2_OK=1
    fi
fi
if [ "$KS2_OK" = "1" ]; then
    echo "  OK: IKeystoreService/default is declared"
else
    echo "  MISSING: IKeystoreService/default is not declared in the"
    echo "           manifest the framework loader reads -> registration"
    echo "           will be rejected and keystore2 will SIGABRT."
    FAIL=1
fi
echo

echo "--- [3] is android.hardware.boot declared? ---"
if grep -q 'android.hardware.boot' /system/manifest.xml 2>/dev/null \
   || grep -q 'android.hardware.boot' \
        /system/etc/vintf/manifest/android.hardware.boot-service.default.xml \
        2>/dev/null; then
    echo "  OK: android.hardware.boot declared"
else
    echo "  MISSING: android.hardware.boot not declared"
    FAIL=1
fi
echo

echo "--- [4] can init lazy-start keystore2 by interface name? ---"
if grep -q 'interface aidl android.system.keystore2.IKeystoreService/default' \
        /system/etc/init/keystore2.rc 2>/dev/null; then
    echo "  OK: keystore2.rc declares the aidl interface"
else
    echo "  WARN: keystore2.rc has no 'interface aidl' line;"
    echo "        ctl.interface_start cannot start it lazily"
fi
echo

echo "--- [5] is keystore2 alive? ---"
SVC=$(getprop init.svc.keystore2)
echo "  init.svc.keystore2 = [$SVC]"
if [ "$SVC" = "running" ]; then
    PID=$(pidof keystore2 2>/dev/null)
    [ -z "$PID" ] && PID=$(pgrep -x keystore2 2>/dev/null)
    if [ -n "$PID" ]; then
        echo "  keystore2 pid = $PID"
        echo "  wchan (want binder_wait_for_work on at least one tid):"
        for t in /proc/$PID/task/*; do
            printf '    tid=%s wchan=%s\n' \
                "$(basename $t)" "$(cat $t/wchan 2>/dev/null)"
        done
        echo "  binder fds:"
        ls -l /proc/$PID/fd 2>/dev/null | grep -i binder
    fi
else
    echo "  FAIL: keystore2 is not running."
    echo "        It aborts when servicemanager rejects its registration."
    FAIL=1
fi
echo

echo "--- [6] the decisive check: is the service registered? ---"
# NB: `service check` prints "not found" on failure, which also contains the
# substring "found", so a bare `grep found` would false-positive. Anchor on the
# ": found" / ": not found" forms instead.
CHK=$(service check android.system.keystore2.IKeystoreService/default 2>/dev/null)
echo "  $CHK"
case "$CHK" in
    *": not found"*) KS2_REG=0 ;;
    *": found"*)     KS2_REG=1 ;;
    *)               KS2_REG=0 ;;
esac
if [ "$KS2_REG" = "1" ]; then
    echo "  OK: registered"
else
    echo "  FAIL: not registered"
    FAIL=1
fi
echo
echo "  full service list:"
service list 2>/dev/null
echo "  (expect 'Found 13 services' when healthy; 2 when broken)"
echo

echo "--- [7] what does servicemanager say? (dmesg) ---"
dmesg 2>/dev/null | grep -i 'VINTF manifest' | tail -20
echo

echo "--- [8] did keystore2 abort in this boot? ---"
if dmesg 2>/dev/null | grep -q "Service 'keystore2'.*received signal 6"; then
    echo "  keystore2 aborted at least once:"
    dmesg 2>/dev/null | grep "Service 'keystore2'.*received signal 6" | tail -5
    echo "  (stale if the service is now running AND found above)"
else
    echo "  OK: no keystore2 SIGABRT in this boot"
fi
echo

echo "=== RESULT ==="
if [ "$FAIL" = "0" ]; then
    echo "PASS - keystore2 is registered; Decrypt_DE() can get its binder."
    exit 0
fi
echo "FAIL - fix not in effect; Decrypt_DE() will spin forever."
exit 1

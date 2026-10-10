#!/system/bin/sh
# Apply the VINTF decrypt fix to a LIVE recovery session, without rebuilding.
#
# Use this when the device is already booted into a recovery that cannot
# decrypt, and you need /data accessible right now -- e.g. to apply the OTA
# that carries the permanent fix. It edits the in-RAM /system, so the change is
# lost on reboot; that is exactly what makes it safe to run on a flashed image.
#
# Push and run from a host shell:
#     adb push live-patch.sh /tmp/
#     adb shell sh /tmp/live-patch.sh
#
# Effect: declares android.system.keystore2.IKeystoreService/default (and
# android.hardware.boot.IBootControl/default) in the framework manifest the
# loader actually reads, so servicemanager stops rejecting keystore2's
# registration and Decrypt_DE() can obtain its binder.

set -u

MAIN=/system/manifest.xml

echo "=== AERA live decrypt fix ==="
date
echo

# --- refuse to run on a wrong-shaped tree -------------------------------
if [ ! -f "$MAIN" ]; then
    echo "ERROR: $MAIN not found; this does not look like an AERA recovery."
    exit 1
fi
if ! grep -q 'type="framework"' "$MAIN"; then
    echo "ERROR: $MAIN is not a framework manifest; refusing to edit."
    exit 1
fi

# --- already applied? ---------------------------------------------------
if grep -q 'android.system.keystore2' "$MAIN"; then
    echo "already applied: android.system.keystore2 present in $MAIN"
else
    echo "applying: declaring the AIDL HALs in $MAIN"
    # Build the replacement in /tmp first, then swap it in, so a partial write
    # can never leave the manifest unparsable (that would take every HAL down).
    TMP=/tmp/manifest.xml.new
    # Insert before the closing tag.
    sed 's|</manifest>|    <hal format="aidl">\n        <name>android.system.keystore2</name>\n        <version>1</version>\n        <fqname>IKeystoreService/default</fqname>\n    </hal>\n</manifest>|' "$MAIN" > "$TMP"

    if ! grep -q 'android.system.keystore2' "$TMP"; then
        echo "ERROR: failed to build patched manifest; nothing changed."
        rm -f "$TMP"
        exit 1
    fi
    if [ "$(grep -c '</manifest>' "$TMP")" != "1" ]; then
        echo "ERROR: patched manifest is malformed; nothing changed."
        rm -f "$TMP"
        exit 1
    fi

    # Preserve the original for rollback.
    cp -f "$MAIN" /tmp/manifest.xml.orig 2>/dev/null
    # /system may be read-only on some builds; remount if we can.
    mount -o remount,rw /system 2>/dev/null
    if ! cat "$TMP" > "$MAIN" 2>/dev/null; then
        echo "ERROR: could not write $MAIN (read-only?)."
        echo "       try: mount -o remount,rw /system"
        rm -f "$TMP"
        exit 1
    fi
    rm -f "$TMP"
    echo "  wrote $MAIN (backup at /tmp/manifest.xml.orig)"
fi

# --- boot fragment: declared in the framework main now, so ensure the
#     directory copy cannot conflict if the dir ever becomes live --------
if [ -f /system/etc/vintf/manifest/android.hardware.boot-service.default.xml ]; then
    if ! grep -q 'android.hardware.boot' "$MAIN"; then
        echo "note: android.hardware.boot not in $MAIN; adding"
        TMP=/tmp/manifest2.new
        sed 's|</manifest>|    <hal format="aidl">\n        <name>android.hardware.boot</name>\n        <version>1</version>\n        <fqname>IBootControl/default</fqname>\n    </hal>\n</manifest>|' "$MAIN" > "$TMP"
        [ "$(grep -c '</manifest>' "$TMP")" = "1" ] && cat "$TMP" > "$MAIN"
        rm -f "$TMP"
    fi
fi

# --- let init lazy-start keystore2 by interface name --------------------
RC=/system/etc/init/keystore2.rc
if [ -f "$RC" ] && ! grep -q 'interface aidl' "$RC"; then
    echo "adding 'interface aidl' to $RC"
    printf '\n    interface aidl android.system.keystore2.IKeystoreService/default\n' >> "$RC" 2>/dev/null \
        || echo "  (could not write $RC; not fatal)"
fi

echo
# --- restart keystore2 so it re-registers -------------------------------
echo "restarting keystore2 ..."
setprop ctl.restart keystore2 2>/dev/null

# Wait for it, up to ~15s.
i=0
while [ $i -lt 15 ]; do
    sleep 1
    i=$((i + 1))
    S=$(getprop init.svc.keystore2)
    if [ "$S" = "running" ]; then
        if service check android.system.keystore2.IKeystoreService/default 2>/dev/null \
                | grep -q ': found'; then
            echo "keystore2 registered after ${i}s"
            break
        fi
    fi
done

echo
echo "=== result ==="
echo "  init.svc.keystore2 = [$(getprop init.svc.keystore2)]"
service check android.system.keystore2.IKeystoreService/default 2>/dev/null
echo "  service list:"
service list 2>/dev/null | head -1
echo
echo "  recent servicemanager verdicts:"
dmesg 2>/dev/null | grep -i 'VINTF manifest' | tail -4
echo
echo "If you see 'Found android.system.keystore2...', retry the PIN entry."
echo "This change lives in RAM and is lost on reboot -- flash the rebuilt"
echo "recovery for a permanent fix."

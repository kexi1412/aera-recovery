#!/bin/sh
# Test the sed substitution used by live-patch.sh, on a realistic manifest.
tmp=$(mktemp -d)
cat > "$tmp/manifest.xml" <<'EOF'
<manifest version="1.0" type="framework">
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
EOF

echo "=== input ==="
cat "$tmp/manifest.xml"
echo

MAIN="$tmp/manifest.xml"
TMP="$tmp/manifest.xml.new"
sed 's|</manifest>|    <hal format="aidl">\n        <name>android.system.keystore2</name>\n        <version>1</version>\n        <fqname>IKeystoreService/default</fqname>\n    </hal>\n</manifest>|' "$MAIN" > "$TMP"

echo "=== output ==="
cat "$TMP"
echo

fail=0
grep -q 'android.system.keystore2' "$TMP" || { echo "FAIL: keystore2 not inserted"; fail=1; }
grep -q '<fqname>IKeystoreService/default</fqname>' "$TMP" || { echo "FAIL: fqname missing"; fail=1; }
[ "$(grep -c '</manifest>' "$TMP")" = "1" ] || { echo "FAIL: closing tag count wrong"; fail=1; }
grep -q 'android.hidl.manager' "$TMP" || { echo "FAIL: existing hal lost"; fail=1; }
grep -q 'IServiceManager' "$TMP" || { echo "FAIL: existing interface lost"; fail=1; }
# the inserted <hal> must be inside <manifest>, i.e. before the closing tag
awk '/android.system.keystore2/{k=NR} /<\/manifest>/{m=NR} END{exit !(k<m)}' "$TMP" \
    || { echo "FAIL: inserted hal is not inside manifest"; fail=1; }

rm -rf "$tmp"
if [ "$fail" = 0 ]; then echo "ALL PASSED"; else echo "FAILED"; fi
exit $fail

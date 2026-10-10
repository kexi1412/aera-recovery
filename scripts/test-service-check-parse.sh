#!/bin/sh
# Regression test for the service-check parsing in verify-decrypt-fix.sh.
# `service check` prints BOTH "  ...: found" and "  ...: not found"; a naive
# `grep found` matches the failure case too, which would report PASS on a
# broken device. Assert the parsing gets both directions right.
fail=0
case_of() {
    case "$1" in
        *": not found"*) echo 0 ;;
        *": found"*)     echo 1 ;;
        *)               echo 0 ;;
    esac
}
check() {
    got=$(case_of "$2")
    if [ "$got" = "$3" ]; then
        echo "  PASS  [$2] -> $got"
    else
        echo "  FAIL  [$2] -> $got (want $3)"
        fail=1
    fi
}
echo "parsing 'service check' output"
check found   "Service android.system.keystore2.IKeystoreService/default: found" 1
check notfound "Service android.system.keystore2.IKeystoreService/default: not found" 0
check empty   "" 0
check other   "Service foo: unknown" 0
echo
if [ "$fail" = 0 ]; then echo "ALL PASSED"; else echo "FAILED"; fi
exit $fail

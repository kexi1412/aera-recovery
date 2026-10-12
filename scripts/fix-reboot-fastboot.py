#!/usr/bin/env python3
"""Give astonc a working "restart in hardware fastboot" path.

Why this patch exists
---------------------
The user-visible symptom is that Menu -> Reboot -> Bootloader flashes the
transition screen and then nothing happens: the device stays in recovery.

The reboot request itself is fine. twrp.cpp:417 maps tw_reboot_arg
"bootloader" to tw_reboot(rb_bootloader), which ends at
    request_power_control("reboot,bootloader")            twrp-functions.cpp:1185
and that only sets sys.powerctl and parks the calling thread in pause().

The failure is one step further down, inside init. This device's recovery
ramdisk ships /system/bin/init built from AOSP, and that binary carries both
strings of the abort path:

    Bootloader command pending.
    reboot-bootloader: Error writing bootloader_message:

which live in bootable/recovery/bootloader_message/bootloader_message.cpp:

    bool write_reboot_bootloader(std::string* err) {
      bootloader_message boot{};
      if (!read_bootloader_message(&boot, err)) return false;
      if (boot.command[0] != '\0') {                 // <-- :234
        *err = "Bootloader command pending.";
        return false;
      }
      strlcpy(boot.command, "bootonce-bootloader", sizeof(boot.command));
      ...

init calls that to queue the request for the bootloader. When it returns
false init logs and gives up, so the power-control request is dropped and the
thread that issued it is already parked forever in pause(). Nothing reboots.

misc is not empty at that moment. install/get_args.cpp:65-73 re-writes the
parsed arguments back into the BCB on every start that is not an AERA soft
switch, and update_bootloader_message() unconditionally sets
    strlcpy(boot->command, "boot-recovery", ...)       // :216
so the command field is "boot-recovery" whenever recovery is running
normally. twrp.cpp:747 clears it once at startup, but that is a single clear
at boot, not a guarantee at the moment the user presses Reboot, and any
failure to reach /misc leaves the field populated for the whole session.

tw_reboot() already knows about this. rb_system calls
Clear_Bootloader_Message() first and its comment says exactly why: "A fastboot
'reboot recovery' request can leave a boot-recovery command behind on some A/B
devices. Clear it again at the final system handoff so Android cannot loop
straight back into AERA."  rb_bootloader and rb_fastboot never got the same
treatment, which is why the loop-free path works and the fastboot ones do not.

What the patch does
-------------------
Adds Clear_Bootloader_Message() as the first statement of the rb_bootloader
and rb_fastboot cases, mirroring rb_system. Clear_Bootloader_Message() is
defined at twrp-functions.cpp:1082, i.e. above tw_reboot(), and is a static
member of the same class, so no new include or declaration is needed.

rb_recovery is deliberately left alone: it must keep a boot-recovery command
in misc if one is there.

Usage:
    python3 device/oneplus/astonc/scripts/fix-reboot-fastboot.py bootable/recovery
"""

import difflib
import os
import re
import sys

FILE = os.path.join("twrp-functions.cpp")
# Anchored on the rb_system comment in twrp.cpp rather than on the phrase
# "Clear it again at the final system handoff", which that comment splits
# across two lines.
MARKER = '"reboot recovery" request can leave a boot-recovery'

BOOTLOADER_OLD = (
    'case rb_bootloader:\n'
    '\t\t\tcheck_and_run_script("/system/bin/rebootbootloader.sh", "reboot bootloader");\n'
    '\t\t\treturn request_power_control("reboot,bootloader");'
)

BOOTLOADER_NEW = (
    'case rb_bootloader:\n'
    '\t\t\t// astonc: init queues this request with write_reboot_bootloader(),\n'
    '\t\t\t// which refuses to touch misc while a command is still pending\n'
    '\t\t\t// ("Bootloader command pending.") and then drops the request.\n'
    '\t\t\t// install/get_args.cpp writes "boot-recovery" back on every normal\n'
    '\t\t\t// start, so clear it here exactly like rb_system does, otherwise\n'
    '\t\t\t// the power-control request never reaches the bootloader and the\n'
    '\t\t\t// device just sits in recovery.\n'
    '\t\t\tClear_Bootloader_Message();\n'
    '\t\t\tcheck_and_run_script("/system/bin/rebootbootloader.sh", "reboot bootloader");\n'
    '\t\t\treturn request_power_control("reboot,bootloader");'
)

FASTBOOT_OLD = (
    'case rb_fastboot:\n'
    '\t\t\treturn request_power_control("reboot,fastboot");'
)

FASTBOOT_NEW = (
    'case rb_fastboot:\n'
    '\t\t\t// astonc: same pending-BCB abort as rb_bootloader. "reboot,fastboot"\n'
    '\t\t\t// has to reach init with an empty misc command field or the\n'
    '\t\t\t// request is silently discarded.\n'
    '\t\t\tClear_Bootloader_Message();\n'
    '\t\t\treturn request_power_control("reboot,fastboot");'
)


def die(msg):
    sys.stderr.write("::error::%s\n" % msg)
    sys.exit(1)


def main():
    if len(sys.argv) != 2:
        die("usage: fix-reboot-fastboot.py <path-to-bootable/recovery>")
    root = sys.argv[1]
    path = os.path.join(root, FILE)
    if not os.path.isfile(path):
        die("%s not found" % path)

    with open(path, "r", encoding="utf-8", newline="") as f:
        original = f.read()

    patched = original

    # Idempotency: both edits present means nothing to do.
    already = (patched.count("Clear_Bootloader_Message();") >= 1
               and BOOTLOADER_NEW in patched and FASTBOOT_NEW in patched)
    if already:
        print("fix-reboot-fastboot: already patched; nothing to do")
        return 0

    if MARKER not in patched:
        die("rb_system no longer carries the boot-recovery comment; "
            "upstream rewrote tw_reboot() and this patch is no longer safe")

    n_boot = patched.count(BOOTLOADER_OLD)
    n_fast = patched.count(FASTBOOT_OLD)
    if n_boot != 1:
        die("expected exactly 1 rb_bootloader case, found %d" % n_boot)
    if n_fast != 1:
        die("expected exactly 1 rb_fastboot case, found %d" % n_fast)

    patched = patched.replace(BOOTLOADER_OLD, BOOTLOADER_NEW, 1)
    patched = patched.replace(FASTBOOT_OLD, FASTBOOT_NEW, 1)

    # Post-conditions.
    if patched.count("case rb_bootloader:") != 1:
        die("rb_bootloader case vanished")
    if patched.count("case rb_fastboot:") != 1:
        die("case rb_fastboot vanished")
    if patched.count('return request_power_control("reboot,bootloader");') != 1:
        die("reboot,bootloader request was not preserved")
    if patched.count('return request_power_control("reboot,fastboot");') != 1:
        die("reboot,fastboot request was not preserved")
    if patched.count("check_and_run_script(\"/system/bin/rebootbootloader.sh\"") != 1:
        die("rebootbootloader.sh hook was not preserved")
    # rb_recovery must still be untouched.
    if "case rb_recovery:" not in patched:
        die("rb_recovery case vanished")
    # Brace balance must be unchanged.
    if patched.count("{") != original.count("{"):
        die("opening brace count changed")
    if patched.count("}") != original.count("}"):
        die("closing brace count changed")
    # The rb_system clear must still be the only other one, plus our two.
    if patched.count("Clear_Bootloader_Message();") != 3:
        die("expected 3 Clear_Bootloader_Message() calls (rb_system + 2 new), "
            "found %d" % patched.count("Clear_Bootloader_Message();"))

    diff = list(difflib.unified_diff(
        original.splitlines(), patched.splitlines(),
        "a/" + FILE, "b/" + FILE, lineterm="", n=2))
    changed = sum(1 for l in diff if l.startswith(("+", "-"))
                  and not l.startswith(("+++", "---")))
    print("fix-reboot-fastboot: %s changed lines: %d" % (FILE, changed))
    if not (10 <= changed <= 40):
        die("diff size %d outside the expected 10..40 band" % changed)

    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(patched)

    print("fix-reboot-fastboot: rb_bootloader and rb_fastboot now clear the BCB "
          "before requesting the reboot")
    return 0


if __name__ == "__main__":
    sys.exit(main())

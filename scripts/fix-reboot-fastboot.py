#!/usr/bin/env python3
"""Give astonc a working "restart in hardware fastboot" path.

Why this patch exists
---------------------
The user-visible symptom is that Menu -> Reboot -> Bootloader flashes the
transition screen and then nothing happens: the device stays in recovery.

The reboot request itself is fine. twrp.cpp:419 maps tw_reboot_arg
"bootloader" to tw_reboot(rb_bootloader), which ends at
    request_power_control("reboot,bootloader")            twrp-functions.cpp:1192
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
      if (boot.command[0] != '\\0') {                 // <-- :234
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

v2 -- the flashed build that carried the BCB clear (plus the graphics-thread
handoff from fix-power-handoff.py) still did not leave recovery, so the
property handoff needs a floor under it. Both fastboot-related cases now also
arm a one-shot watchdog child: fork() a process that sleeps 15 s and, if the
device is somehow still running recovery at that point, logs the current misc
command to /dev/kmsg (the one evidence channel that always survives), writes
the same BCB command init would have written ("bootonce-bootloader" /
"boot-fastboot") and issues the reboot syscall directly. When init does its
job the device is gone long before the timer fires; when it does not, the
device still reaches the target instead of sitting in recovery forever.

The watchdog child deliberately uses no heap allocation: it runs in a forked,
multithreaded process (the battery monitor thread and the parked graphics
thread are alive), so only stack buffers and raw fds are touched, and the
function is file-local to twrp-functions.cpp so no header changes are needed.

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

# The anchor the watchdog function is inserted before. tw_reboot() is the only
# definition with this exact signature line, so the insertion point is stable.
TW_REBOOT_DEF = "int TWFunc::tw_reboot(RebootCommand command)\n{"

WATCHDOG_FN = """// astonc: last-resort watchdog for the reboot-to-bootloader/fastboot paths.
// The parent keeps the normal request_power_control() handoff; this child only
// fires if init has still not rebooted the device 15 s later. 15 s is far
// longer than init needs to consume sys.powerctl and far shorter than a user
// would wait before calling the feature broken. It runs in a forked child of a
// multithreaded process, so it must not rely on heap allocation: stack buffers
// and raw fds only, and the evidence line goes to /dev/kmsg, which survives
// every userspace failure and is where the triage checklist looks first.
void Astonc_Reboot_Watchdog(const char *bcb_command) {
\tpid_t pid = fork();
\tif (pid != 0) return;

\tsleep(15);

\tchar probe[32] = {};
\tconst int misc_rd = open("/dev/block/by-name/misc", O_RDONLY);
\tif (misc_rd >= 0) {
\t\tif (read(misc_rd, probe, sizeof(probe) - 1) < 0) probe[0] = '\\0';
\t\tclose(misc_rd);
\t}
\tprobe[sizeof(probe) - 1] = '\\0';

\tconst int kmsg = open("/dev/kmsg", O_WRONLY);
\tif (kmsg >= 0) {
\t\tchar line[192];
\t\tsnprintf(line, sizeof(line),
\t\t\t\t"astonc watchdog: init did not consume the reboot in 15 s "
\t\t\t\t"(misc command \\"%s\\"); rebooting directly as %s\\n",
\t\t\t\tprobe, bcb_command);
\t\tconst ssize_t logged = write(kmsg, line, strlen(line));
\t\t(void)logged;
\t\tclose(kmsg);
\t}

\t// The command field is the first 32 bytes of misc; the parent has already
\t// cleared the struct, so writing this one slot is the exact delta init
\t// would have produced.
\tconst int misc_wr = open("/dev/block/by-name/misc", O_WRONLY);
\tif (misc_wr >= 0) {
\t\tchar command[32] = {};
\t\tstrlcpy(command, bcb_command, sizeof(command));
\t\tconst ssize_t wrote = pwrite(misc_wr, command, sizeof(command), 0);
\t\tconst int write_errno = errno;
\t\tclose(misc_wr);
\t\tif (wrote != sizeof(command) && kmsg >= 0) {
\t\t\tconst int kmsg2 = open("/dev/kmsg", O_WRONLY);
\t\t\tif (kmsg2 >= 0) {
\t\t\t\tchar line2[128];
\t\t\t\tsnprintf(line2, sizeof(line2),
\t\t\t\t\t\t"astonc watchdog: BCB write failed: %s\\n",
\t\t\t\t\t\tstrerror(write_errno));
\t\t\t\tconst ssize_t logged2 = write(kmsg2, line2, strlen(line2));
\t\t\t\t(void)logged2;
\t\t\t\tclose(kmsg2);
\t\t\t}
\t\t}
\t}

\tsync();
\treboot(RB_AUTOBOOT);
\t_exit(127);
}

"""

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
    '\t\t\t// astonc v2: the BCB clear alone was not enough on a flashed\n'
    '\t\t\t// build, so arm the 15 s watchdog. "bootonce-bootloader" is the\n'
    '\t\t\t// command write_reboot_bootloader() would have written.\n'
    '\t\t\tAstonc_Reboot_Watchdog("bootonce-bootloader");\n'
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
    '\t\t\t// astonc v2: watchdog as in rb_bootloader. "boot-fastboot" is the\n'
    '\t\t\t// BCB command that makes the bootloader enter fastbootd.\n'
    '\t\t\tAstonc_Reboot_Watchdog("boot-fastboot");\n'
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

    # Idempotency: both edits plus the watchdog present means nothing to do.
    already = (patched.count("Clear_Bootloader_Message();") >= 1
               and BOOTLOADER_NEW in patched and FASTBOOT_NEW in patched
               and "Astonc_Reboot_Watchdog" in patched)
    if already:
        print("fix-reboot-fastboot: already patched; nothing to do")
        return 0

    if MARKER not in patched:
        die("rb_system no longer carries the boot-recovery comment; "
            "upstream rewrote tw_reboot() and this patch is no longer safe")
    if patched.count(TW_REBOOT_DEF) != 1:
        die("expected exactly 1 tw_reboot definition, found %d -- the "
            "watchdog insertion point moved" % patched.count(TW_REBOOT_DEF))

    n_boot = patched.count(BOOTLOADER_OLD)
    n_fast = patched.count(FASTBOOT_OLD)
    if n_boot != 1:
        die("expected exactly 1 rb_bootloader case, found %d" % n_boot)
    if n_fast != 1:
        die("expected exactly 1 rb_fastboot case, found %d" % n_fast)

    # The watchdog function is inserted immediately before tw_reboot() so it
    # is in the same translation unit scope and readable next to its only
    # callers. File-local: no twrp-functions.hpp change, and the name is
    # unique in the tree, so a subsequent run recognises its own output.
    patched = patched.replace(TW_REBOOT_DEF, WATCHDOG_FN + TW_REBOOT_DEF, 1)
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
    # The watchdog must be defined exactly once and called exactly twice.
    if patched.count("void Astonc_Reboot_Watchdog(const char *bcb_command) {") != 1:
        die("the watchdog function was not inserted exactly once")
    if patched.count('Astonc_Reboot_Watchdog("') != 2:
        die("expected exactly 2 watchdog call sites, found %d"
            % patched.count('Astonc_Reboot_Watchdog("'))
    if patched.count('Astonc_Reboot_Watchdog("bootonce-bootloader");') != 1:
        die("the rb_bootloader watchdog call is missing")
    if patched.count('Astonc_Reboot_Watchdog("boot-fastboot");') != 1:
        die("the rb_fastboot watchdog call is missing")
    # Brace balance must be unchanged beyond the watchdog itself (the case
    # edits are comment-only). The expected delta is counted from the inserted
    # text at runtime rather than hardcoded, so the assertion cannot drift
    # from the constant above.
    want_open = WATCHDOG_FN.count("{")
    want_close = WATCHDOG_FN.count("}")
    if want_open != want_close:
        die("the watchdog text itself is brace-imbalanced (%d vs %d)"
            % (want_open, want_close))
    if patched.count("{") != original.count("{") + want_open:
        die("opening brace count changed unexpectedly")
    if patched.count("}") != original.count("}") + want_close:
        die("closing brace count changed unexpectedly")
    # The rb_system clear must still be the only other one, plus our two.
    if patched.count("Clear_Bootloader_Message();") != 3:
        die("expected 3 Clear_Bootloader_Message() calls (rb_system + 2 new), "
            "found %d" % patched.count("Clear_Bootloader_Message();"))
    # The watchdog must sit before its first caller in the file (C++ requires
    # the declaration to precede use).
    wd_def = patched.find("void Astonc_Reboot_Watchdog(const char *bcb_command) {")
    wd_call = patched.find('Astonc_Reboot_Watchdog("bootonce-bootloader");')
    if not (0 <= wd_def < wd_call):
        die("the watchdog definition must precede its first call site")

    diff = list(difflib.unified_diff(
        original.splitlines(), patched.splitlines(),
        "a/" + FILE, "b/" + FILE, lineterm="", n=2))
    changed = sum(1 for l in diff if l.startswith(("+", "-"))
                  and not l.startswith(("+++", "---")))
    print("fix-reboot-fastboot: %s changed lines: %d" % (FILE, changed))
    if not (50 <= changed <= 110):
        die("diff size %d outside the expected 50..110 band" % changed)

    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(patched)

    print("fix-reboot-fastboot: rb_bootloader and rb_fastboot now clear the BCB "
          "before requesting the reboot, with a 15 s direct-reboot watchdog")
    return 0


if __name__ == "__main__":
    sys.exit(main())

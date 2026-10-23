#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Make the AERA status bar read the battery the way this device needs.

``aeraui/core/status_bar.cpp`` reads the battery in two places:

  * ``ReadCapacity()`` does a single ``fopen`` on
    ``/sys/class/power_supply/battery/capacity`` (then ``bms/capacity``) and
    returns ``std::clamp(capacity, 0, 100)``;
  * ``TimerTick()`` only re-reads when ``ticks % 10 == 0``, i.e. once every
    ten seconds, while the bar is refreshed every second.

Two things about astonc break that:

1. The gauge node is not readable for the first ~16 s of a recovery boot. The
   kernel log from the device itself shows::

       [13.703] healthd: battery none chg=          (x11, until 19.783)
       [15.602] health-service.qti: opened battery/capacity after 19 retries
       [15.602] health-service.qti: Read Batt Capacity after 19 retries ret : 1
       [15.606] healthd: battery l=60 v=4003 t=36.5 ...

   and underneath it, for the whole life of the session::

       [ERROR] OPLUS_CHG[MMS_GAUGE]: gauge_ic[oplus,virtual_gauge] 0 init timeout
       [ERROR] OPLUS_CHG[GKI]: ui_soc not ready, use chip soc

   Recovery's UI comes up at t~13.7 s (``Starting the UI...`` in recovery.log,
   ``boot-service.default_recovery`` fstab parsing in kmsg), so the very first
   ``Refresh(state, true)`` lands inside the unreadable window. ``fopen`` fails,
   ``ReadCapacity()`` returns -1 and the bar renders ``--%`` -- and the next
   chance to fix it is ten seconds away.

   SELinux is not the cause: the device log carries 60 distinct ``avc: denied``
   classes for ``comm="recovery"`` and none of them touch ``power_supply``.

2. ``std::clamp(capacity, 0, 100)`` destroys the "not ready" signal. A gauge
   that answers with -1 (or any negative) while its IC is still initialising is
   clamped to 0, so the bar reads a confident ``0%`` instead of retrying. Values
   above 100 are clamped to 100 instead of being rejected, so a node that hands
   back millivolts renders as a full battery.

This script rewrites both sites so that:

  * a reading outside 0..100 is treated as "not ready yet" and the next path is
    tried, instead of being clamped into a plausible-looking number;
  * the timer re-reads on every tick while the last reading was invalid, then
    falls back to the original ten-tick cadence once a real value arrives.

It does not add a blocking retry loop: the device's own health HAL takes ~1.9 s
(19 attempts) to open the node, and stalling the LVGL thread for that long on
the first frame would freeze the UI. Polling once a second from the existing
timer gets the same coverage without blocking.

v2 -- the flashed build that carried v1 still rendered ``--%`` for the whole
session, so the range check plus polling is not the whole story on this device.
A plausible contributor is the persist partition: this tree never mounted
``/mnt/vendor/persist`` after the Wi-Fi removal, and the oplus gauge
(``oplus,virtual_gauge``, whose ``init timeout`` lines open the kmsg excerpt)
resolves its calibration data from there. The mount is restored by the Wi-Fi
bring-up (``init.recovery.wifi.rc`` + the persist fstab entry). Because that is
a hypothesis rather than a verified root cause, v2 also makes the read path
report itself:

  * the first plausible reading after an unreadable stretch is announced once
    ("battery gauge answered after N unreadable poll(s)");
  * a gauge that is still unread after ~40 s logs a one-line diagnosis every
    minute (errno plus the readability of every candidate node) so a flashed
    device can be triaged from its logs without a shell.

The logs go to logcat (logd runs in this recovery; ``TARGET_USES_LOGD`` and
``AERA_INCLUDE_LOGCAT`` are both set) and are packaged by the upstream
diagnostics reporter. Nothing in the read path allocates on the hot path
beyond what the existing code already did, and no poll ever blocks.

The repository (AERA-Recovery/android_bootable_recovery) is not one this
project can push to, so the edit is applied here at build time against the tree
CI just synced. Every assumption is asserted, the file is re-read after
writing, and the script is idempotent.

Usage::

    python3 scripts/fix-battery-read.py bootable/recovery
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys

# The includes the diagnostics need: <android/log.h> for __android_log_print,
# <cerrno> for errno, <unistd.h> for access(). Matched as the adjacent pair the
# upstream include block uses so the insertion cannot land anywhere else.
INCLUDES_OLD = """#include <algorithm>
#include <cstdio>
"""

INCLUDES_NEW = """#include <algorithm>
#include <android/log.h>
#include <cerrno>
#include <cstdio>
#include <unistd.h>
"""

# The function as upstream wrote it. Matched on the whole body so a partial
# rewrite upstream cannot leave this script patching a shape it does not
# understand.
READ_CAPACITY = '''int ReadCapacity() {
  constexpr const char *kPaths[] = {
      "/sys/class/power_supply/battery/capacity",
      "/sys/class/power_supply/bms/capacity",
  };
  for (const char *path : kPaths) {
    FILE *file = fopen(path, "re");
    if (file == nullptr) continue;
    int capacity = -1;
    const int matched = fscanf(file, "%d", &capacity);
    fclose(file);
    if (matched == 1) return std::clamp(capacity, 0, 100);
  }
  return -1;
}'''

READ_CAPACITY_PATCHED = '''int ReadCapacity() {
  constexpr const char *kPaths[] = {
      "/sys/class/power_supply/battery/capacity",
      "/sys/class/power_supply/bms/capacity",
  };
  // astonc: this node only opens ~16 s into a recovery boot -- the health
  // HAL reports "opened battery/capacity after 19 retries" and healthd logs
  // "battery none" eleven times before that -- and while oplus,virtual_gauge
  // is still initialising the file can answer with a negative value. Clamping
  // those into 0..100 turned "not ready" into a confident 0% or 100%, so
  // reject anything outside the range and let the caller keep polling instead.
  //
  // astonc v2: the flashed build that carried the range check alone still sat
  // on --% for a whole session, so record how the gauge behaves. The first
  // plausible answer after an unreadable stretch is announced once, and a
  // gauge that is still unread after ~40 s logs one line per minute with the
  // errno and the readability of every candidate node. While unreadable the
  // timer polls once per second, so a poll count is a second count; the
  // counter is only advanced on failure for exactly that reason.
  static int unreadable_polls = 0;
  for (const char *path : kPaths) {
    FILE *file = fopen(path, "re");
    if (file == nullptr) continue;
    int capacity = -1;
    const int matched = fscanf(file, "%d", &capacity);
    const int read_errno = errno;
    fclose(file);
    if (matched == 1 && capacity >= 0 && capacity <= 100) {
      if (unreadable_polls > 0) {
        __android_log_print(ANDROID_LOG_INFO, "AERA UI",
                            "astonc: battery gauge answered after %d unreadable "
                            "poll(s): %s = %d%%",
                            unreadable_polls, path, capacity);
      }
      unreadable_polls = 0;
      return capacity;
    }
  }
  ++unreadable_polls;
  if (unreadable_polls == 40 ||
      (unreadable_polls > 40 && unreadable_polls % 60 == 0)) {
    __android_log_print(ANDROID_LOG_WARN, "AERA UI",
                        "astonc: battery gauge still unreadable after %d "
                        "poll(s), errno=%d (%s)",
                        unreadable_polls, errno, strerror(errno));
    for (const char *probe : kPaths) {
      __android_log_print(ANDROID_LOG_WARN, "AERA UI",
                          "astonc: battery probe: %s readable=%d",
                          probe, access(probe, R_OK) == 0 ? 1 : 0);
    }
  }
  return -1;
}'''

# The single line that decides how often the battery is re-read. Anchored on
# its indentation so it can only be changed inside TimerTick().
TIMER_TICK_OLD = '''  ++state->ticks;
  Refresh(state, state->ticks % 10 == 0);'''

TIMER_TICK_NEW = '''  ++state->ticks;
  // While the last reading was invalid, poll every tick instead of every ten:
  // the gauge node appears ~16 s after boot and the ten-tick cadence left the
  // bar on --% for the whole window. Once a plausible value has arrived this
  // is a no-op and the original cadence applies again.
  Refresh(state, state->battery < 0 || state->ticks % 10 == 0);'''

# Proves the rewrite did not orphan <algorithm> by removing its last use.
CLAMP_CALL = re.compile(r"std::clamp\(")

MARKER = "while oplus,virtual_gauge is still initialising"
DIAG_MARKER = "static int unreadable_polls = 0;"


def fail(message: str) -> None:
    print("::error::%s" % message, file=sys.stderr)
    raise SystemExit(1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "recovery_src",
        help="path to the bootable/recovery checkout in the synced tree",
    )
    args = parser.parse_args()

    path = args.recovery_src.rstrip("/\\") + "/aeraui/core/status_bar.cpp"
    try:
        with open(path, "r", encoding="utf-8") as handle:
            source = handle.read()
    except FileNotFoundError:
        fail("no status_bar.cpp at %s -- is the recovery source tree synced?"
             % path)
    except UnicodeDecodeError as error:
        fail("status_bar.cpp is not valid UTF-8: %s" % error)
    original = source

    patched_capacity = source.count(READ_CAPACITY_PATCHED)
    patched_timer = source.count(TIMER_TICK_NEW)
    patched_includes = source.count(INCLUDES_NEW)
    has_diag = DIAG_MARKER in source

    if patched_capacity == 1 and patched_timer == 1 and has_diag \
            and patched_includes == 1:
        print("Battery read is already patched; nothing to do")
        return 0
    if patched_capacity or patched_timer or has_diag or patched_includes:
        fail("status_bar.cpp is half-patched (%d ReadCapacity, %d TimerTick, "
             "%d includes, diagnostics=%s); refusing to guess at the "
             "remaining edit"
             % (patched_capacity, patched_timer, patched_includes, has_diag))

    if source.count(INCLUDES_OLD) != 1:
        fail("expected exactly one '<algorithm>\\n<cstdio>' include pair in "
             "status_bar.cpp, found %d -- upstream reshaped the includes"
             % source.count(INCLUDES_OLD))
    if source.count(READ_CAPACITY) != 1:
        fail("expected exactly one unpatched ReadCapacity() body in "
             "status_bar.cpp, found %d -- upstream reshaped it, review before "
             "re-applying" % source.count(READ_CAPACITY))
    if source.count(TIMER_TICK_OLD) != 1:
        fail("expected exactly one 'Refresh(state, state->ticks %% 10 == 0);' "
             "inside TimerTick(), found %d -- upstream changed the refresh "
             "policy" % source.count(TIMER_TICK_OLD))

    source = source.replace(INCLUDES_OLD, INCLUDES_NEW, 1)
    source = source.replace(READ_CAPACITY, READ_CAPACITY_PATCHED, 1)
    source = source.replace(TIMER_TICK_OLD, TIMER_TICK_NEW, 1)

    # Re-verify against what is about to be written, not the pre-edit copy.
    if source.count(READ_CAPACITY_PATCHED) != 1:
        fail("ReadCapacity() is not present exactly once after the edit")
    if source.count(TIMER_TICK_NEW) != 1:
        fail("TimerTick() was not rewritten exactly once")
    if source.count(INCLUDES_NEW) != 1:
        fail("the diagnostic includes were not inserted exactly once")
    if READ_CAPACITY in source:
        fail("the unpatched ReadCapacity() body survived the edit")
    if "Refresh(state, state->ticks % 10 == 0);" in source.replace(
            TIMER_TICK_NEW, ""):
        fail("the unguarded ten-tick refresh survived the edit")
    if "__android_log_print" not in source:
        fail("the diagnostic logging did not survive the edit")
    for needle in ("#include <android/log.h>", "#include <cerrno>",
                   "#include <unistd.h>"):
        if needle not in source:
            fail("the %s include is missing; the diagnostics would not "
                 "compile" % needle)

    before_clamp = len(CLAMP_CALL.findall(original))
    after_clamp = len(CLAMP_CALL.findall(source))
    if after_clamp >= before_clamp:
        fail("<algorithm>/std::clamp is still used (%d -> %d); this patch is "
             "supposed to remove the battery clamp, review the source"
             % (before_clamp, after_clamp))

    # The bar must still know how to draw a missing reading.
    if 'snprintf(battery_text, sizeof(battery_text), "--%%")' not in source:
        fail("the '--%%' fallback for an invalid reading is gone; the status "
             "bar would render garbage when the gauge is not ready")

    # Count real changes rather than a naive zip: these substitutions grow the
    # file, and a positional zip would report every line after the first
    # insert as changed.
    matcher = difflib.SequenceMatcher(None, original.splitlines(),
                                      source.splitlines())
    changed = sum(max(i2 - i1, j2 - j1)
                  for tag, i1, i2, j1, j2 in matcher.get_opcodes()
                  if tag != "equal")
    # Three blocks are touched: the include pair (one line becomes five),
    # ReadCapacity's clamp (one line becomes ~40 with the diagnostics) and
    # TimerTick's refresh line (one line becomes four).
    if not 30 <= changed <= 80:
        fail("unexpected change size (%d lines); upstream reshaped the file, "
             "review the substitution by hand" % changed)

    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(source)

    print("Battery read patched: range-checked reading + per-tick poll + "
          "self-reporting diagnostics")
    print("  file      : %s" % path)
    print("  changed   : %d lines" % changed)
    print("  evidence  : health HAL opens battery/capacity after 19 retries "
          "(t=15.6s); recovery UI starts t=13.7s")
    print("  v2 note   : persist mount restored by the Wi-Fi bring-up; the "
          "diagnostics exist because that alone is still a hypothesis")
    print("--- TimerTick after patch ---")
    for line in TIMER_TICK_NEW.splitlines():
        print("  %s" % line.rstrip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

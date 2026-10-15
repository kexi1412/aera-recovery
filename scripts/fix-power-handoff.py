#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Keep the graphics thread alive while init reboots or powers off.

Ports the power handoff repair from
KAI-Miru/android_device_oneplus_guacamole-AERA commit 8e9db27 to astonc.

Why this patch exists
---------------------
The user-visible symptom is identical to the BCB defect fixed by
``fix-reboot-fastboot.py`` -- Menu -> Reboot -> Bootloader flashes the
transition screen and then nothing happens -- but the cause is one layer
higher, inside the native UI's thread lifecycle.

``aeraui/core/runner.cpp`` starts the native renderer on a background
thread inside ``StartAeraUiEarly()``. When the user picks a terminal action
(reboot, bootloader, fastboot, power-off) ``RunLoop()`` returns that result
and the lambda finishes, which lets the thread exit. Exiting runs thread-local
storage destructors, and Adreno installs one for its EGL context. On this
device that destructor blocks inside the vendor library while it tears down
live scanout state, and it blocks *before* the calling thread in
``RunAeraUi()`` can return the result to the recovery core. The core never
gets to call ``request_power_control()`` and the device stays in recovery.

The BCB fix cleared the earlier obstruction (init dropping the request),
but without this patch the request still never reaches init because the
graphics thread is wedged in vendor EGL cleanup.

What the patch does
-------------------
Mirrors the guacamole repair exactly:

1. **``StartAeraUiEarly()`` lambda**: stores the ``RunLoop()`` result in a
   local ``const RunResult result`` instead of writing it straight to the
   global. After signalling the decrypt waiter, it checks
   ``IsTerminalResult(result)``: if true, it logs
   ``"terminal action handed off with graphics thread alive"``, publishes
   the result through ``gEarlyFinished`` / ``gEarlyCondition``, and then
   parks the thread in ``for (;;) pause();`` so its TLS destructors never
   run. init receives the power request and reboots; the thread dies with
   the process.

2. **``RunAeraUi()``**: waits on ``gEarlyCondition`` for the early thread
   to publish its result (instead of ``move``-ing the thread out and
   ``join``-ing it). If the result is terminal the thread is ``detach``-ed
   (it will never exit on its own, so joining would deadlock); otherwise it
   is joined exactly as before.

3. **``IsTerminalResult()``**: a new helper that classifies reboot/fastboot/
   power-off as terminal. ``kUnexpectedExit`` and ``kEngineFailure`` are not
   terminal, so the thread still joins and cleans up normally on those paths.

A marker string -- ``"terminal action handed off with graphics thread
alive"`` -- is embedded in the recovery binary so a build-time or flash-time
check can prove the patch landed.

Usage::

    python3 scripts/fix-power-handoff.py bootable/recovery
"""

from __future__ import annotations

import argparse
import difflib
import sys

FILE = "aeraui/core/runner.cpp"

# ---------------------------------------------------------------------------
# Block 1: globals -- add the condition variable and finished flag.
# ---------------------------------------------------------------------------
GLOBALS_OLD = (
    "std::mutex gEarlyMutex;\n"
    "std::mutex gDecryptMutex;\n"
)

GLOBALS_NEW = (
    "std::mutex gEarlyMutex;\n"
    "std::condition_variable gEarlyCondition;\n"
    "std::mutex gDecryptMutex;\n"
)

EARLY_FLAG_OLD = (
    "bool gEarlyStarted = false;\n"
    "bool gDecryptResolved = false;\n"
)

EARLY_FLAG_NEW = (
    "bool gEarlyStarted = false;\n"
    "bool gEarlyFinished = false;\n"
    "bool gDecryptResolved = false;\n"
)

# ---------------------------------------------------------------------------
# Block 2: IsTerminalResult helper, inserted before ToRunResult.
# ---------------------------------------------------------------------------
MARKER_CAPTURE = '"frame capture finished with result %d", result);'

IS_TERMINAL_INSERT = (
    "}\n"
    "\n"
    "bool IsTerminalResult(RunResult result) {\n"
    "    switch (result) {\n"
    "        case RunResult::kRebootSystem:\n"
    "        case RunResult::kRebootRecovery:\n"
    "        case RunResult::kRebootBootloader:\n"
    "        case RunResult::kRebootFastbootd:\n"
    "        case RunResult::kPowerOff:\n"
    "            return true;\n"
    "        default:\n"
    "            return false;\n"
    "    }\n"
    "}\n"
    "\n"
    "RunResult ToRunResult(Action action) {\n"
)

IS_TERMINAL_ANCHOR = (
    MARKER_CAPTURE + "\n"
    "}\n"
    "\n"
    "RunResult ToRunResult(Action action) {\n"
)

# ---------------------------------------------------------------------------
# Block 3: StartAeraUiEarly lambda -- capture result, hand off, pause.
# ---------------------------------------------------------------------------
LAMBDA_OLD = """    gEarlyThread = std::thread([] {
        gEarlyResult = RunLoop(false, gDisplayMetrics);
        {
            std::lock_guard<std::mutex> lock(gDecryptMutex);
            if (gDecryptRequested.load(std::memory_order_acquire) &&
                !gDecryptResolved) {
                switch (gEarlyResult) {
                    case RunResult::kRebootSystem:
                    case RunResult::kRebootRecovery:
                    case RunResult::kRebootBootloader:
                    case RunResult::kRebootFastbootd:
                    case RunResult::kPowerOff:
                        gDecryptResult = DecryptionResult::kTerminalAction;
                        break;
                    default:
                        gDecryptResult = DecryptionResult::kUnavailable;
                        break;
                }
                gDecryptResolved = true;
            }
        }
        gDecryptCondition.notify_all();
    });
}"""

LAMBDA_NEW = """    gEarlyThread = std::thread([] {
        const RunResult result = RunLoop(false, gDisplayMetrics);
        {
            std::lock_guard<std::mutex> lock(gDecryptMutex);
            if (gDecryptRequested.load(std::memory_order_acquire) &&
                !gDecryptResolved) {
                switch (result) {
                    case RunResult::kRebootSystem:
                    case RunResult::kRebootRecovery:
                    case RunResult::kRebootBootloader:
                    case RunResult::kRebootFastbootd:
                    case RunResult::kPowerOff:
                        gDecryptResult = DecryptionResult::kTerminalAction;
                        break;
                    default:
                        gDecryptResult = DecryptionResult::kUnavailable;
                        break;
                }
                gDecryptResolved = true;
            }
        }
        gDecryptCondition.notify_all();
        const bool terminal = IsTerminalResult(result);
        if (terminal) {
            __android_log_print(ANDROID_LOG_INFO, kLogTag,
                                "terminal action handed off with graphics thread alive");
        }
        {
            std::lock_guard<std::mutex> lock(gEarlyMutex);
            gEarlyResult = result;
            gEarlyFinished = true;
        }
        gEarlyCondition.notify_all();
        // Adreno also owns thread-local EGL state. Abandoning Engine avoids
        // its explicit teardown, but exiting this thread still runs vendor
        // TLS destructors. Keep it alive until init completes the power request.
        if (terminal) {
            for (;;) pause();
        }
    });
}"""

# ---------------------------------------------------------------------------
# Block 4: RunAeraUi -- condition_variable wait replaces lock_guard join.
# ---------------------------------------------------------------------------
RUN_AERA_UI_OLD = """RunResult RunAeraUi(const DisplayMetrics& metrics) {
    std::thread early_thread;
    bool run_inline = false;
    {
        std::lock_guard<std::mutex> lock(gEarlyMutex);
        if (!gEarlyStarted) {
            gBackendReady.store(true, std::memory_order_release);
            run_inline = true;
        } else {
            gBackendReady.store(true, std::memory_order_release);
            early_thread = std::move(gEarlyThread);
        }
    }

    if (run_inline) return RunLoop(false, metrics);

    if (early_thread.joinable()) early_thread.join();

    std::lock_guard<std::mutex> lock(gEarlyMutex);
    gEarlyStarted = false;
    return gEarlyResult;
}"""

RUN_AERA_UI_NEW = """RunResult RunAeraUi(const DisplayMetrics& metrics) {
    std::thread early_thread;
    RunResult result;
    {
        std::unique_lock<std::mutex> lock(gEarlyMutex);
        if (!gEarlyStarted) {
            gBackendReady.store(true, std::memory_order_release);
            lock.unlock();
            return RunLoop(false, metrics);
        }
        gBackendReady.store(true, std::memory_order_release);
        gEarlyCondition.wait(lock, [] { return gEarlyFinished; });
        result = gEarlyResult;
        if (IsTerminalResult(result))
            gEarlyThread.detach();
        else
            early_thread = std::move(gEarlyThread);
        gEarlyStarted = false;
    }
    if (early_thread.joinable()) early_thread.join();
    return result;
}"""

# ---------------------------------------------------------------------------
# Block 5 (optional): runner.hpp comment update.
# ---------------------------------------------------------------------------
HPP_OLD = (
    "// Marks the recovery backend ready, then joins the already-running "
    "native UI.\n"
    "// Hardware Back and ordinary workflows stay inside the native engine.\n"
)

HPP_NEW = (
    "// Marks the recovery backend ready and waits for the native UI result. "
    "Terminal\n"
    "// actions keep its graphics thread alive until init reboots or powers "
    "off.\n"
    "// Hardware Back and ordinary workflows stay inside the native engine.\n"
)

# Build verification marker embedded in the binary.
MARKER = "terminal action handed off with graphics thread alive"


def fail(message: str) -> None:
    print("::error::%s" % message, file=sys.stderr)
    raise SystemExit(1)


def apply_block(source: str, old: str, new: str, label: str,
                patched_marker: str | None = None) -> str:
    """Replace *old* with *new* exactly once, asserting idempotency."""
    count = source.count(old)
    if count == 0:
        if patched_marker and patched_marker in source:
            return source  # already patched
        fail("%s: expected exactly one match, found 0 -- upstream reshaped "
             "the code, review before re-applying" % label)
    if count > 1:
        fail("%s: expected exactly one match, found %d" % (label, count))
    return source.replace(old, new, 1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "recovery_src",
        help="path to the bootable/recovery checkout in the synced tree",
    )
    args = parser.parse_args()

    import os
    path = os.path.join(args.recovery_src.rstrip("/\\"), FILE)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            source = handle.read()
    except FileNotFoundError:
        fail("no runner.cpp at %s -- is the recovery source tree synced?"
             % path)
    except UnicodeDecodeError as error:
        fail("runner.cpp is not valid UTF-8: %s" % error)

    original = source

    # --- Idempotency: all markers present means nothing to do. ----------
    already = (
        "gEarlyCondition" in source
        and "gEarlyFinished" in source
        and "IsTerminalResult" in source
        and MARKER in source
        and "for (;;) pause();" in source
    )
    if already:
        print("fix-power-handoff: already patched; nothing to do")
        return 0

    # pause() needs <unistd.h>. The upstream runner.cpp already includes it
    # (RunLoop uses it for usleep etc.), but assert it so a future tree that
    # dropped the include fails here rather than at compile time.
    if "#include <unistd.h>" not in source:
        fail("runner.cpp does not include <unistd.h>; pause() would not "
             "compile -- add the include upstream or review this patch")

    # --- Block 1: globals -----------------------------------------------
    source = apply_block(source, GLOBALS_OLD, GLOBALS_NEW,
                         "globals (gEarlyCondition)")
    source = apply_block(source, EARLY_FLAG_OLD, EARLY_FLAG_NEW,
                         "globals (gEarlyFinished)")

    # --- Block 2: IsTerminalResult helper --------------------------------
    if IS_TERMINAL_ANCHOR not in source:
        fail("IsTerminalResult anchor not found -- CaptureFrameIfRequested "
             "or ToRunResult moved upstream")
    source = source.replace(IS_TERMINAL_ANCHOR, MARKER_CAPTURE + "\n" +
                            IS_TERMINAL_INSERT, 1)

    # --- Block 3: StartAeraUiEarly lambda --------------------------------
    source = apply_block(source, LAMBDA_OLD, LAMBDA_NEW,
                         "StartAeraUiEarly lambda")

    # --- Block 4: RunAeraUi ----------------------------------------------
    source = apply_block(source, RUN_AERA_UI_OLD, RUN_AERA_UI_NEW,
                         "RunAeraUi")

    # --- Block 5: runner.hpp comment (optional, non-fatal) ---------------
    # path is .../aeraui/core/runner.cpp, so go up one level from core/ to
    # reach aeraui/include/aeraui/runner.hpp.
    hpp_path = os.path.join(os.path.dirname(os.path.dirname(path)),
                            "include", "aeraui", "runner.hpp")
    hpp_changed = False
    if os.path.isfile(hpp_path):
        with open(hpp_path, "r", encoding="utf-8") as h:
            hpp = h.read()
        if HPP_OLD in hpp:
            hpp = hpp.replace(HPP_OLD, HPP_NEW, 1)
            with open(hpp_path, "w", encoding="utf-8", newline="") as h:
                h.write(hpp)
            hpp_changed = True

    # --- Post-conditions -------------------------------------------------
    if source.count("gEarlyCondition") < 2:
        fail("gEarlyCondition appears too few times (%d)" %
             source.count("gEarlyCondition"))
    if source.count("gEarlyFinished") < 3:
        fail("gEarlyFinished appears too few times (%d)" %
             source.count("gEarlyFinished"))
    if source.count("bool IsTerminalResult(") != 1:
        fail("IsTerminalResult not defined exactly once")
    if source.count("for (;;) pause();") != 1:
        fail("pause() loop not present exactly once")
    if MARKER not in source:
        fail("the build verification marker is missing")
    # The old lock_guard join must be gone from RunAeraUi.
    if "bool run_inline = false;" in source:
        fail("the old run_inline flag survived the edit")
    # Brace balance must be preserved: the patch adds a new function and
    # extra lock scopes, so the absolute count grows, but the net balance
    # (open - close) must stay identical.
    orig_balance = original.count("{") - original.count("}")
    new_balance = source.count("{") - source.count("}")
    if new_balance != orig_balance:
        fail("brace balance changed (%d -> %d); a substitution left an "
             "unmatched brace" % (orig_balance, new_balance))

    # Diff size sanity.
    matcher = difflib.SequenceMatcher(None, original.splitlines(),
                                      source.splitlines())
    changed = sum(max(i2 - i1, j2 - j1)
                  for tag, i1, i2, j1, j2 in matcher.get_opcodes()
                  if tag != "equal")
    if not 25 <= changed <= 70:
        fail("unexpected change size (%d lines); upstream reshaped the file, "
             "review the substitution by hand" % changed)

    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(source)

    print("fix-power-handoff: terminal actions now hand off with the "
          "graphics thread alive")
    print("  file      : %s" % path)
    print("  changed   : %d lines" % changed)
    print("  marker    : %s" % MARKER)
    if hpp_changed:
        print("  hpp       : %s (comment updated)" % hpp_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

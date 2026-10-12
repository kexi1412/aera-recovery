#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Restore the AERA Menu to the arrangement this device was verified against.

Upstream commit b9eddda ("ui2: move PC Connection to Menu and Language to
Preferences with corrected layouts") rewrote slot 9 of the hard-coded Menu
array in ``aeraui/scenes/tool_scene.cpp``:

    -    {"A", "Language", "Choose the recovery interface language", Action::kLanguage},
    +    {LV_SYMBOL_USB, "PC connection", "Transfer and install ZIPs and images from your computer", Action::kPcConnection},

Language was not deleted -- b9eddda re-adds it as a row inside Preferences
(tool_scene.cpp:2290, "Language" with the current language name as its value).
But this device is used with the Menu-only arrangement, and the replacement
row is a *different* destination, not a relocation of the same one.

That repository (AERA-Recovery/android_bootable_recovery) is not one this
project can push to, so the revert is applied here, at build time, against the
tree that CI just synced. The edit is a single line substitution in the other
direction, which is exact: both revisions declare

    const std::array<Item, 12> items{{ ... }};

with twelve rows, so swapping the row text keeps the array size, the pinned
full-width About/Reboot placement and every index the layout code derives from
``visible_index`` intact.

This script is deliberately strict. ``tool_scene.cpp`` is a 2700-line file that
several other upstream commits touch, so a ``sed`` on the row text would either
succeed silently against the wrong array or corrupt an unrelated match. Every
assumption is asserted, and the script re-reads what it wrote:

  1. the source file exists and parses as UTF-8;
  2. the twelve-row Menu array is located by its declaration, not by a global
     search, and the substitution happens only inside it;
  3. the PC connection row has exactly one occurrence inside that array;
  4. the Language row has exactly one occurrence inside that array afterwards;
  5. the array still declares 12 rows and the substitution changed exactly one
     line;
  6. the Preferences Language row is left alone -- b9eddda's relocation is
     legitimate and removing it would delete the only other way to reach the
     language list.

Idempotent: a tree that already carries the Language row is reported and left
untouched, which is what an incremental rebuild or a re-run after a partial
failure needs.
"""

from __future__ import annotations

import argparse
import re
import sys

# The row b9eddda installed. Matched without its indentation and with the exact
# spacing LVGL's array uses, so a reformat of the surrounding file does not
# silently stop matching.
PC_ROW = ('{LV_SYMBOL_USB, "PC connection", '
          '"Transfer and install ZIPs and images from your computer", Action::kPcConnection},')
LANGUAGE_ROW = ('{"A", "Language", '
                '"Choose the recovery interface language", Action::kLanguage},')

# Anchored on the declaration b9eddda and 2782981 both kept. The named group
# captures the row body so the substitution cannot reach any other array.
MENU_ARRAY = re.compile(
    r"(const std::array<Item,\s*(?P<count>\d+)>\s+items\{\{)(?P<body>.*?)(\}\};)",
    re.S,
)

# b9eddda added this inside Preferences. Asserted to still be present so this
# script cannot be mistaken for a change that removes language access.
PREFERENCES_LANGUAGE = 'PreferenceEntry(state, LV_SYMBOL_KEYBOARD, "Language"'


def fail(message: str) -> "None":
    print("::error::%s" % message, file=sys.stderr)
    raise SystemExit(1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "recovery_src",
        help="path to the bootable/recovery checkout in the synced tree",
    )
    args = parser.parse_args()

    path = args.recovery_src.rstrip("/\\") + "/aeraui/scenes/tool_scene.cpp"
    try:
        with open(path, "r", encoding="utf-8") as handle:
            source = handle.read()
    except FileNotFoundError:
        fail("no tool_scene.cpp at %s -- is the recovery source tree synced?"
             % path)
    except UnicodeDecodeError as error:
        fail("tool_scene.cpp is not valid UTF-8: %s" % error)
    original = source

    if PREFERENCES_LANGUAGE not in source:
        fail("tool_scene.cpp no longer has the Preferences Language row; "
             "upstream changed the language entry points again, review "
             "b9eddda before reverting the Menu row")

    match = MENU_ARRAY.search(source)
    if match is None:
        fail("could not find the Menu 'std::array<Item, N> items{{ ... }}' "
             "declaration in tool_scene.cpp")

    declared = int(match.group("count"))
    body = match.group("body")

    pc_count = body.count(PC_ROW)
    language_count = body.count(LANGUAGE_ROW)

    if language_count == 1 and pc_count == 0:
        print("Menu slot 9 is already the Language row; nothing to do")
        return 0
    if pc_count != 1:
        fail("expected exactly one PC connection row inside the Menu array, "
             "found %d -- upstream changed the array, refusing to guess"
             % pc_count)
    if language_count != 0:
        fail("the Menu array already contains a Language row as well as the "
             "PC connection row; the layout is not the one b9eddda produced")

    new_body = body.replace(PC_ROW, LANGUAGE_ROW, 1)
    if new_body == body:
        fail("the substitution was a no-op despite a single PC row match")

    source = source[:match.start("body")] + new_body + source[match.end("body"):]

    # Re-verify against the text that is about to be written rather than
    # against the pre-edit copy, so an indexing mistake cannot pass this check.
    second = MENU_ARRAY.search(source)
    if second is None:
        fail("the Menu array stopped matching after the substitution")
    if int(second.group("count")) != declared:
        fail("the Menu array row count changed from %d to %s"
             % (declared, second.group("count")))
    if second.group("body").count(LANGUAGE_ROW) != 1:
        fail("the Language row is not present exactly once after the edit")
    if PC_ROW in second.group("body"):
        fail("the PC connection row survived the edit")
    if second.group("body").count("\n") != body.count("\n"):
        fail("the edit changed the number of lines in the Menu array")

    changed = sum(1 for a, b in zip(original.splitlines(), source.splitlines())
                  if a != b)
    # A single-line substitution cannot change the line count, so the zip is
    # exhaustive here rather than truncating.
    if changed != 1:
        fail("expected exactly one changed line, counted %d" % changed)

    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(source)

    print("Menu slot 9 restored: PC connection -> Language")
    print("  file      : %s" % path)
    print("  array size: %d rows (unchanged)" % declared)
    print("  changed   : %d line" % changed)
    # Echo the resulting array so the CI log carries the evidence even when the
    # step is read months later.
    print("--- Menu array after patch ---")
    for line in second.group(0).splitlines():
        print("  %s" % line.rstrip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

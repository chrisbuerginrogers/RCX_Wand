#!/usr/bin/env python3
"""Set up a fresh M5StickS3 as an RCX wand: copy every library and
program it needs, then check that they actually import.

Runs on the Mac with the Stick plugged in over USB:

    python3 setup_wand.py
    python3 setup_wand.py --list      # show what would be copied
    python3 setup_wand.py --no-verify # copy only, skip the import check

Assumes MicroPython is already flashed on the board (the Octal-SPIRAM
build -- see micropython/M5StickS3/m5/CLAUDE.md for links and
commands). This copies files; it does not flash MicroPython itself.

The device's filesystem is flat, so everything lands at its root
regardless of where it lives in this repo -- `tools/rcx_ir.py` becomes
`/rcx_ir.py` on the Stick. tools/stick_link.py's WAND_FILES is the
canonical list; this script is deliberately thin so there is only ever
one place to add a file.

After this, `main.py` runs on boot: BtnB starts the highlighted
program, BtnA stops back to the READY screen.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "tools"))
import stick_link

#: Package directories copied wholesale.
DIRS = ["m5"]

#: Modules that must import cleanly on the device once copied. Not the
#: same as the file list: main.py is checked last because importing it
#: pulls in everything else, so a failure anywhere shows up here.
VERIFY = ["m5.m5_power", "m5.m5_display", "m5.m5_buttons",
          "rcx_ir", "rcx_driver", "stick_menu", "stick_ui",
          "lego_card", "cardID", "RCXWand"]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--list", action="store_true",
                    help="print what would be copied and exit")
    ap.add_argument("--no-verify", action="store_true",
                    help="skip the on-device import check")
    ap.add_argument("--port", default=None, help="serial port override")
    args = ap.parse_args(argv)

    if args.list:
        for d in DIRS:
            print("{}/  ->  :/{}/".format(d, d))
        for f in stick_link.WAND_FILES:
            print("{}  ->  :/{}".format(f, Path(f).name))
        return

    port = args.port or stick_link.find_port()
    print("stick on {}".format(port), flush=True)

    for d in DIRS:
        print("copying {}/ ...".format(d), flush=True)
        stick_link.copy_dir(d, port=port)

    for f in stick_link.WAND_FILES:
        print("copying {} ...".format(f), flush=True)
        stick_link.copy([f], port=port)

    if args.no_verify:
        print("copied. skipped verification.")
        return

    print("verifying imports on the device ...", flush=True)
    checks = "\n".join(
        "try:\n"
        "    __import__({!r})\n"
        "    print('  ok   {}')\n"
        "except Exception as e:\n"
        "    print('  FAIL {}:', e)\n"
        "    bad += 1".format(m, m, m) for m in VERIFY)
    stick_link.execute("bad = 0\n" + checks +
                       "\nprint('WAND READY' if not bad else "
                       "'{} MODULE(S) FAILED'.format(bad))",
                       port=port, timeout=180)


if __name__ == "__main__":
    try:
        main()
    except stick_link.StickError as e:
        raise SystemExit("error: {}".format(e))

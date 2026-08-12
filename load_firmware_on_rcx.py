#!/usr/bin/env python3
"""Flash a real RCX with firmware, then load its five default programs.

Runs on the Mac with the M5StickS3 plugged in over USB:

    python3 load_firmware_on_rcx.py

Takes about five minutes -- the image is 23,904 bytes at 2400 baud.
Watch the Stick's screen for progress. If the RCX is already sitting in
boot mode (blank screen), add --no-reset: the ROM does not answer the
boot-mode command a second time.

Not destructive in any lasting sense: the RCX's ROM bootloader runs
independently of whatever firmware is or isn't loaded, so a failed or
interrupted attempt just means running this again.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "tools"))
import stick_link

TOOLS = Path(__file__).resolve().parent / "tools"
DEFAULT_CODES = TOOLS / "default_codes.lasm"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--no-reset", action="store_true",
                    help="RCX is already in boot mode (blank screen)")
    ap.add_argument("--no-codes", action="store_true",
                    help="flash the firmware only, skip the default programs")
    ap.add_argument("--power-down", type=int, default=15,
                    help="idle minutes before the RCX powers off (default 15)")
    ap.add_argument("--port", default=None, help="serial port override")
    ap.add_argument("--blind", action="store_true",
                    help="send without waiting for acks (needed if the "
                         "Stick's IR receiver does not work). Watch the RCX's "
                         "own screen: it counts blocks as they land.")
    args = ap.parse_args(argv)

    port = args.port or stick_link.find_port()
    print("stick on {}".format(port), flush=True)
    stick_link.ensure_runtime(
        extra=[TOOLS / "rcx_firmware_data.py", TOOLS / "flash_rcx.py"], port=port)

    # Phase 1: the image itself, plus post_boot_init(). Five minutes.
    stick_link.execute(
        "import flash_rcx\n"
        "ok = flash_rcx.flash(send_reset={}, power_down_minutes={}, acked={})\n"
        "print('FLASH_OK' if ok else 'FLASH_FAILED')".format(
            not args.no_reset, args.power_down, not args.blind),
        port=port, timeout=1200)

    if args.no_codes:
        return

    # Phase 2: the default programs. Compiled here, not on the Stick --
    # lasm_compiler.py needs dataclasses, pathlib and a 100KB
    # OpCodes.json, none of which belong on the device.
    commands = stick_link.compile_lasm_source(DEFAULT_CODES.read_text())
    print("default codes: {} command(s)".format(len(commands)), flush=True)
    stick_link.execute(
        "import flash_rcx\n"
        "from rcx_driver import RCX, BOARD_M5STICKS3\n"
        "flash_rcx.codes_screen()\n"
        "rcx = RCX(board=BOARD_M5STICKS3)\n"
        "ok = rcx.send_program({}, on_progress=flash_rcx.show_codes, acked={})\n"
        "flash_rcx.done(ok)\n"
        "print('CODES_OK' if ok else 'CODES_FAILED')".format(
            stick_link.commands_literal(commands), not args.blind),
        port=port, timeout=600)


if __name__ == "__main__":
    try:
        main()
    except stick_link.StickError as e:
        raise SystemExit("error: {}".format(e))

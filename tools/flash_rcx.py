"""Device-side firmware flasher, with a big on-screen progress readout.

  >>> Runs ON the M5StickS3. Copied there and invoked by the Mac-side
      load_firmware_on_rcx.py -- you do not normally run this by hand. <<<

It lives in tools/ with the rest of the firmware machinery rather than
at the project root, because it is not part of the wand app: main.py's
menu no longer has a FLASH RCX entry, since flashing takes ~5 minutes,
replaces everything on the brick, and is a deliberate Mac-side act.

Needs rcx_driver.py, rcx_firmware_data.py and m5/ present on the
device; load_firmware_on_rcx.py copies the first two up before running
this. Deliberately does NOT import stick_ui: stick_ui.UI() constructs a
Speaker, which powers the AW8737 amp on, and that amp is exactly what
M5Stack's docs say breaks IR reception (see m5/m5_ir.py). This talks to
m5_display directly and leaves the amp off throughout.
"""
import time

from m5 import m5_power
from m5.m5_display import Display, WHITE, BLACK
from rcx_driver import RCX, BOARD_M5STICKS3, DEFAULT_POWER_DOWN_MINUTES
import rcx_firmware_data as fw

CHUNK = 200


def _screen():
    """Two-line readout: a big number and a smaller counter under it.

    The 135x240 panel fits 5 characters at scale 3 (24px cells) and 8
    at scale 2 (16px), so '100%' and '120/120' each fit their own line
    untruncated. Scale 1 was unreadable at arm's length, which is what
    the previous version used.
    """
    d = Display()
    d.fill(BLACK)
    d.draw_text_centered(20, 'FLASH', WHITE, BLACK, scale=2, spacing=0)

    def show(big, small=''):
        d.draw_text(0, 90, ' ' * 6, WHITE, BLACK, scale=3, spacing=0)
        d.draw_text_centered(90, big, WHITE, BLACK, scale=3, spacing=0)
        d.draw_text(0, 140, ' ' * 9, WHITE, BLACK, scale=2, spacing=0)
        d.draw_text_centered(140, small, WHITE, BLACK, scale=2, spacing=0)

    return show


def flash(send_reset=True, power_down_minutes=DEFAULT_POWER_DOWN_MINUTES,
          acked=True):
    """Download the firmware image and run the post-boot init.

    Returns True if the RCX accepted the whole image. Loading the
    default programs happens afterwards, from the Mac -- see
    load_firmware_on_rcx.py.

    `acked=False` is blind mode, for a Stick whose IR receiver does not
    work: everything is transmitted on the same pacing but nothing is
    verified. The RCX's own screen counts blocks as they land, so watch
    that rather than this function's return value.
    """
    m5_power.power_off_speaker()
    show = _screen()
    show('0%', '')

    total = len(fw.FIRMWARE)
    blocks = (total + CHUNK - 1) // CHUNK
    print('flashing {} bytes to {:#06x} ({} blocks)'.format(
        total, fw.FIRMWARE_START, blocks))

    rcx = RCX(board=BOARD_M5STICKS3)

    def on_progress(sent, grand_total):
        pct = sent * 100 // grand_total
        show('{}%'.format(pct),
             '{}/{}'.format((sent + CHUNK - 1) // CHUNK, blocks))
        print('firmware: {}/{} bytes ({}%)'.format(sent, grand_total, pct))

    ok = rcx.download_firmware(fw.FIRMWARE, start=fw.FIRMWARE_START,
                                chunk_size=CHUNK, on_progress=on_progress,
                                send_reset=send_reset,
                                power_down_minutes=power_down_minutes,
                                acked=acked)
    show('CODES' if ok else 'FAILED', '')
    return ok


def show_codes(step, total):
    """Progress callback for the Mac's default-program download, so the
    screen keeps moving through that phase too."""
    _CODES_SCREEN[0]('CODES', '{}/{}'.format(step, total))


_CODES_SCREEN = [None]


def codes_screen():
    """Re-attach a screen for the default-programs phase, which runs in
    a separate mpremote exec from flash() and so has lost its closure."""
    _CODES_SCREEN[0] = _screen()
    return _CODES_SCREEN[0]


def done(ok):
    show = _CODES_SCREEN[0] or _screen()
    show('DONE' if ok else 'FAILED', '')

"""Flash real RCX firmware over IR - the same job LabVIEW's "RCX
Download Firmware.vi" does. Ported from ESP32-RCX/library/rcx_driver.py
(RCX.download_firmware()), not reimplemented here.
  >>> Runs ON the M5StickS3. Needs m5/, stick_menu.py, stick_ui.py,
      rcx_driver.py, rcx_firmware_data.py, and an RCX's IR window in
      view of the Stick's built-in IR LED *and receiver* - this needs
      the receiver too, since the RCX acks every step of a download. <<<

This replaces whatever's currently running on the RCX. Not
destructive in a lasting sense - the RCX's ROM bootloader implements
delete/begin/download independent of whatever firmware is or isn't
currently loaded, so a failed or interrupted attempt just means trying
again, never a bricked brick (see rcx_driver.py's download_firmware()
docstring) - but real enough that toggling into this program shouldn't
be the same single BtnB press that starts sending. So this adds its
own two-step confirm in front of it:

    1. BtnB once: arms it, shows a countdown.
    2. BtnB again within CONFIRM_TIMEOUT_MS: actually starts.
    3. BtnA at any point (including during the transfer): cancels.

Deliberately does NOT use stick_menu.watch() for that confirm step -
there, BtnB means "go", not "toggle to the next program", which is the
opposite of what it means everywhere else in this menu. Once the
transfer is running, only BtnA is polled (checked between chunks, so
it can take a moment); BtnB is ignored rather than treated as toggle,
since toggling away mid-download and having some *other* program's
main() start touching the same IR pins while a chunk might still be
in flight is worth avoiding, not worth the convenience.
"""

import time

import stick_ui
import stick_menu
from m5.m5_buttons import Buttons
from rcx_driver import RCX, BOARD_M5STICKS3
import rcx_firmware_data as fw

TAG = 'FLASH'

CONFIRM_TIMEOUT_MS = 4000


def _edge(buttons):
    """A tiny local edge-detector, independent of stick_menu.watch() -
    this file needs BtnB to mean something other than "toggle" for
    part of its flow, so it can't share that helper."""
    state = {'a': buttons.a.is_pressed(), 'b': buttons.b.is_pressed()}

    def pressed():
        """(a_pressed, b_pressed) since the last call, edge-triggered."""
        a = buttons.a.is_pressed()
        b = buttons.b.is_pressed()
        pa = a and not state['a']
        pb = b and not state['b']
        state['a'] = a
        state['b'] = b
        return pa, pb

    return pressed


def _confirm(ui, buttons):
    """Block until the two-tap confirm succeeds; raises stick_menu.Stop
    on BtnA or on timing out waiting for the second tap."""
    # 'FLASH RCX?' / 'BtnB arms, BtnA back' both overran this screen's
    # character budget (8 chars for looking()'s headline, 13 for the
    # line below) and got cut off mid-word -- split across looking()'s
    # two lines plus a third via status() instead of one long line.
    ui.looking('FLASH?', 'BTNB=ARM')
    ui.status('BTNA=BACK')
    ui.tag(TAG)
    pressed = _edge(buttons)
    while True:
        a, b = pressed()
        if a:
            raise stick_menu.Stop()
        if b:
            break
        time.sleep_ms(30)

    ui.looking('CONFIRM', 'BTNB=GO')
    ui.tag(TAG)
    deadline = time.ticks_add(time.ticks_ms(), CONFIRM_TIMEOUT_MS)
    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        a, b = pressed()
        if a:
            raise stick_menu.Stop()
        if b:
            return
        time.sleep_ms(30)
    raise stick_menu.Stop()


def _flash(ui, buttons):
    """Actually send it. Only BtnA is live during the transfer -- see
    the module docstring for why BtnB/toggle is ignored here."""
    ui.looking('FLASHING', '0%')  # exactly 8 chars -- fits, unlike the screens above
    ui.tag(TAG)
    print('Downloading {} bytes of firmware to {:#06x}...'.format(
        len(fw.FIRMWARE), fw.FIRMWARE_START))

    rcx = RCX(board=BOARD_M5STICKS3)
    pressed = _edge(buttons)

    def on_progress(sent, total):
        a, _b = pressed()
        pct = sent * 100 // total
        ui.status('{}%'.format(pct))
        print('firmware: {}/{} bytes ({}%)'.format(sent, total, pct))
        if a:
            raise stick_menu.Stop()

    try:
        ok = rcx.download_firmware(fw.FIRMWARE, start=fw.FIRMWARE_START,
                                    on_progress=on_progress)
    except stick_menu.Stop:
        ui.problem('STOPPED', '')  # 'CANCELLED' (9 chars) overran the 8-char budget
        ui.tag(TAG)
        print('firmware download cancelled')
        return

    if ok:
        ui.status('DONE')
        ui.saved()
        print('firmware download complete')
    else:
        ui.problem('FAILED', 'see console')
        ui.tag(TAG)
        print('firmware download failed -- see the message above for '
              'which step did not ack')


def main(buttons=None):
    """Confirm, then flash. Returns 'stop' always (there is no 'toggle'
    path out of this file - see the module docstring); the return
    value is still there so this matches rcx.main()/cardID.main()'s
    signature for the menu.
    """
    buttons = buttons or Buttons()
    ui = stick_ui.UI()
    try:
        _confirm(ui, buttons)
        _flash(ui, buttons)
    except stick_menu.Stop:
        print('stopped')
    finally:
        ui.close()
    return 'stop'


if __name__ == '__main__':
    main()

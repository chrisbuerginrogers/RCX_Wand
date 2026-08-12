"""The menu: side button (BtnB) cycles which program is running; top
button (BtnA) stops the current one and comes back here.
  >>> Runs ON the M5StickS3. Install it as main.py. <<<
  Needs on the Stick: m5/, stick_menu.py, stick_ui.py, lego_card.py,
  rcx.py, cardID.py, flash_rcx.py, rcx_driver.py, rcx_firmware_data.py.

Three programs, cycled through with one button:

    DRIVE RCX   rcx.py         tap a card, find its Controller, drive
                               an RCX's motors A and C over IR
    CARD ID     cardID.py      tap cards, log their FD02 broadcast
    FLASH RCX   flash_rcx.py   flash real RCX firmware over IR

At the idle "READY" screen, BtnB starts whichever name is showing.
Once something is running, BtnB stops it and starts the *next* one
immediately - no idle screen in between, since that is what "toggle"
(now really "cycle", with three) means. BtnA always stops back to the
idle screen instead, showing whichever program you were just on.
flash_rcx.py is the one exception: it doesn't hand back a 'toggle', on
purpose (see its own docstring) - BtnB inside it means something else
entirely (arm/confirm the flash), so from there BtnA is the only way
out, same as everywhere else, just without the BtnB shortcut.

Each program is its own file with its own main(buttons=None) - see
stick_menu.py for the Stop/Toggle convention they use to hand control
back here. Nothing about what any program *does* changed to make this
work; only how each one stops.

gc.collect() between programs is defensive, not confirmed necessary:
rcx.py and flash_rcx.py each construct their own RCX object on the
same IR pins (rcx_ir.RCX vs rcx_driver.RCX - two independent
implementations of the same protocol, see rcx_ir.py's docstring for
why they're not the same class), and neither explicitly releases the
RMT channel it claims. Repeatedly constructing rcx_ir.RCX() alone,
back to back, was tested live without error; cycling between the two
*different* RCX classes on the same pins within one continuously
running process has not been.
"""

import gc
import time

import stick_menu
import stick_ui
from m5.m5_buttons import Buttons

import rcx
import cardID
import flash_rcx

PROGRAMS = (
    ('DRIVE RCX', rcx.main),
    ('CARD ID', cardID.main),
    ('FLASH RCX', flash_rcx.main),
)


def show_ready(buttons, name):
    """The idle screen. Blocks until BtnB starts `name`; BtnA is a
    no-op here since nothing is running yet to stop.

    Its own short-lived UI, closed before main() hands control to
    whichever program is about to run - two live Speaker/Display
    objects at once is exactly the kind of thing that goes wrong
    quietly on this hardware.
    """
    ui = stick_ui.UI()
    ui.looking('READY', name)
    poll = stick_menu.watch(buttons)
    try:
        while True:
            try:
                poll()
            except stick_menu.Toggle:
                return
            except stick_menu.Stop:
                pass
            time.sleep_ms(50)
    finally:
        ui.close()


def main():
    buttons = Buttons()
    index = 0
    show_idle = True
    while True:
        name, run = PROGRAMS[index]
        if show_idle:
            show_ready(buttons, name)

        print('starting', name)
        reason = run(buttons)
        print(name, 'stopped:', reason)
        gc.collect()

        if reason == 'toggle':
            index = (index + 1) % len(PROGRAMS)
            show_idle = False
        else:
            show_idle = True


main()

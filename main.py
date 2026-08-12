"""The menu: side button (BtnB) cycles which program is running; top
button (BtnA) stops the current one and comes back here.
  >>> Runs ON the M5StickS3. Install it as main.py. <<<
  Needs on the Stick: m5/, stick_menu.py, stick_ui.py, lego_card.py,
  RCXWand.py, cardID.py.

Two programs, cycled through with one button:

    DRIVE RCX   RCXWand.py     tap a card, find its Controller, drive
                               an RCX's motors A and C over IR
    CARD ID     cardID.py      tap cards, log their FD02 broadcast

Flashing firmware is deliberately NOT here any more. It takes about
five minutes, replaces everything on the brick, and needs a LASM
compiler that only runs on a Mac -- so it is a Mac-side act now:
`python3 load_firmware_on_rcx.py`, with the Stick plugged in over USB.

At the idle "READY" screen, BtnB starts whichever name is showing.
Once something is running, BtnB stops it and starts the *next* one
immediately - no idle screen in between, since that is what "toggle"
means. BtnA always stops back to the idle screen instead, showing
whichever program you were just on.

Each program is its own file with its own main(buttons=None) - see
stick_menu.py for the Stop/Toggle convention they use to hand control
back here. Nothing about what any program *does* changed to make this
work; only how each one stops.

gc.collect() between programs is defensive, not confirmed necessary:
RCXWand.py constructs an rcx_ir.RCX on the IR pins and never explicitly
releases the RMT channel it claims. Repeatedly constructing
rcx_ir.RCX() back to back was tested live without error.
"""

import gc
import time

import stick_menu
import stick_ui
from m5.m5_buttons import Buttons

import RCXWand
import cardID

PROGRAMS = (
    ('DRIVE RCX', RCXWand.main),
    ('CARD ID', cardID.main),
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

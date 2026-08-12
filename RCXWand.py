"""Tap a card, find the Controller tapped with it, and drive an RCX's
motors A and C over IR - direct command mode, looping as fast as the
Stick can read the controller and re-send.
  >>> Runs ON the M5StickS3. Needs m5/, stick_menu.py, rcx_ir.py, the
      Grove RFID2 Unit, and an RCX brick's IR window in view of the
      Stick's built-in IR LED. <<<

Point the StickS3 at the RCX, tap any LEGO connection card, then tap
the same card on a LEGO Education Controller (or make sure one already
carries it and is switched on). The left stick drives motor A, the
right stick drives motor C.

main(buttons=None) is written to work two ways:

  - Standalone (buttons=None, the __main__ case below): builds its own
    Buttons(), and BtnA/BtnB simply raise stick_menu.Stop/Toggle,
    caught right here and turned into a clean return.
  - Launched from a menu (buttons=<the menu's Buttons()>): same
    button, same behavior, but the caller gets the 'stop'/'toggle'
    string back and decides what runs next.

Either way BtnA stops (motors off, controller disconnected, back to
the caller) and BtnB does the same but says 'toggle' instead of
'stop', for a caller that wants to switch straight to something else.
"""

import time

from m5.m5_buttons import Buttons
from m5.m5_rfid import RFID
import m5.m5_wand as Wand
import stick_menu
from rcx_ir import RCX, MOTOR_A, MOTOR_C

#: A centred stick still reads a few percent; below this it means zero.
DEADZONE = Wand.STICK_DEADZONE

#: Shown pinned to the bottom of the screen so it's clear which of the
#: menu's two programs is running -- Wand.UI already has a line for
#: this (hint()), just never used it until now.
TAG = 'RCX'


class DirectMotor:
    """One RCX motor (or bitmask of motors), driven in direct command
    mode. Only sends an IR packet when the commanded power or direction
    actually changes, so the drive loop isn't spending its time
    re-transmitting a command the RCX is already holding.
    """

    def __init__(self, rcx, motor_mask):
        self._rcx = rcx
        self._mask = motor_mask
        self._power = 0          # last sent power, 0 (off) to 7
        self._forward = True     # last sent direction

    def drive(self, percent):
        """percent: -100..100, straight from a Controller's
        left_percent/right_percent. Negative reverses."""
        if -DEADZONE < percent < DEADZONE:
            percent = 0
        power = round(abs(percent) * 7 / 100)
        forward = percent >= 0

        if power == 0:
            if self._power != 0:
                self._rcx.motor_off(self._mask)
                self._power = 0
            return

        if forward != self._forward or self._power == 0:
            self._rcx.set_direction(self._mask, forward)
            self._forward = forward
        if power != self._power:
            self._rcx.set_power(self._mask, power)
        if self._power == 0:
            self._rcx.motor_on(self._mask)
        self._power = power

    def off(self):
        self._rcx.motor_off(self._mask)
        self._power = 0


def _wait_for_card(ui, rfid, poll):
    """Like m5_wand.wait_for_card, but also polls buttons so Stop/
    Toggle can interrupt an otherwise indefinite wait."""
    # Wand.UI.looking()'s own defaults ("TAP A CARD" / "purple or
    # green") don't fit this screen's character budget (8 chars at
    # this headline's scale, 13 for the line below) and don't apply
    # here anyway -- this flow takes any card, not just purple/green.
    ui.looking('TAP CARD', '')
    ui.hint(TAG)
    print('tap a card')
    while True:
        poll()
        card = Wand.poll_card(rfid)
        if card is not None:
            return card
        time.sleep_ms(150)


def drive_loop(ui, ctrl, rcx, poll):
    """Read the controller and steer the RCX until it disconnects, or
    stick_menu.Stop/Toggle interrupts (propagated to the caller). No
    sleep - this loops as fast as ctrl.update() and the IR sends
    underneath it allow."""
    motor_a = DirectMotor(rcx, MOTOR_A)
    motor_c = DirectMotor(rcx, MOTOR_C)
    shown = [None]

    print('driving -- BtnA stops, BtnB toggles')
    ui.go()
    try:
        while True:
            poll()
            ctrl.update()
            if not ctrl.connected:
                ui.problem('LOST', 'CHECK POWER')
                ui.hint(TAG)
                print('controller disconnected')
                return

            left = ctrl.left_percent
            right = ctrl.right_percent
            motor_a.drive(left)
            motor_c.drive(right)

            readout = ('A {:>4}'.format(left), 'C {:>4}'.format(right))
            if readout != shown[0]:
                ui.big(0, readout[0])
                ui.big(1, readout[1])
                shown[0] = readout
    finally:
        motor_a.off()
        motor_c.off()


def main(buttons=None):
    """Tap one card, find the Controller that shares it, and drive.

    Returns 'stop' or 'toggle' once BtnA/BtnB ends the run (or the
    controller disconnects on its own, which counts as 'stop').
    """
    buttons = buttons or Buttons()
    poll = stick_menu.watch(buttons)

    ui = Wand.UI()
    rcx = RCX()
    rfid = RFID()
    ctrl = None
    reason = 'stop'
    try:
        color, serial = _wait_for_card(ui, rfid, poll)
        print('got {} #{}'.format(Wand.color_name(color), serial))
        ui.card(color, serial)
        ui.hint(TAG)

        print('looking for a controller tapped with this card...')
        ui.brick(0, 'CONTROLLER ?')
        ctrl = Wand.Controller()
        ctrl.connect(color, serial, on_wait=poll)
        ui.brick(0, 'CONTROLLER OK')
        print('connected to the controller')

        drive_loop(ui, ctrl, rcx, poll)
    except stick_menu.Stop:
        print('stopped')
        reason = 'stop'
    except stick_menu.Toggle:
        print('toggling')
        reason = 'toggle'
    except KeyboardInterrupt:
        print('stopped')
        reason = 'stop'
    finally:
        rcx.motor_off(MOTOR_A | MOTOR_C)
        if ctrl is not None:
            Wand.shut_down((ctrl,))
        Wand.close_radio()
        ui.close()
    return reason


if __name__ == '__main__':
    main()

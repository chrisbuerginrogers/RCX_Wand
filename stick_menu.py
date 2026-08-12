"""Shared plumbing for a two-button menu on the StickS3.

    from m5.m5_buttons import Buttons
    import stick_menu

    def main(buttons=None):
        buttons = buttons or Buttons()
        poll = stick_menu.watch(buttons)
        try:
            while True:
                poll()
                ...
        except stick_menu.Stop:
            return 'stop'
        except stick_menu.Toggle:
            return 'toggle'
        finally:
            ...cleanup...

BtnA (top) raises Stop: the caller should tear down and hand control
back to the menu's idle screen. BtnB (side) raises Toggle: tear down
and go straight into the *other* program, no idle screen in between.
A program run standalone (buttons=None, no menu involved) can just let
both propagate -- there is nothing above it to catch them either way.
"""


class Stop(Exception):
    """BtnA was pressed: stop and return to the menu's idle screen."""


class Toggle(Exception):
    """BtnB was pressed: stop and switch straight to the other program."""


def watch(buttons):
    """poll() to call every loop tick. Edge-triggered -- raises Stop or
    Toggle only on the tick a button transitions from up to down, not
    on every tick it is held down.
    """
    state = {'a': buttons.a.is_pressed(), 'b': buttons.b.is_pressed()}

    def poll():
        a = buttons.a.is_pressed()
        b = buttons.b.is_pressed()
        pressed_a = a and not state['a']
        pressed_b = b and not state['b']
        state['a'] = a
        state['b'] = b
        if pressed_a:
            raise Stop()
        if pressed_b:
            raise Toggle()

    return poll

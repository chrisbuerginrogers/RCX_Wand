"""BtnA / BtnB on the StickS3.

Pins ported from M5Stack's M5Unified (the board_M5StickS3 case in
Button_Class.cpp: raw state read as `!gpio_in(GPIO_NUM_11)` /
`!gpio_in(GPIO_NUM_12)`, i.e. active-low with the ESP32's internal
pull-up doing the pull-up work).
"""

from machine import Pin
import time

BTNA_PIN = 11
BTNB_PIN = 12

_DEBOUNCE_MS = 20


class Button:
    """A single active-low button with simple time-based debounce."""

    def __init__(self, pin_num, debounce_ms=_DEBOUNCE_MS):
        self._pin = Pin(pin_num, Pin.IN, Pin.PULL_UP)
        self._debounce_ms = debounce_ms
        self._raw = not self._pin.value()
        self._pressed = self._raw
        self._last_change = time.ticks_ms()

    def is_pressed(self):
        """Debounced, instantaneous button state."""
        raw = not self._pin.value()
        now = time.ticks_ms()
        if raw != self._raw:
            self._raw = raw
            self._last_change = now
        elif time.ticks_diff(now, self._last_change) >= self._debounce_ms:
            self._pressed = raw
        return self._pressed

    def wait_for_press(self):
        while not self.is_pressed():
            time.sleep_ms(10)

    def wait_for_release(self):
        while self.is_pressed():
            time.sleep_ms(10)


class Buttons:
    """Both StickS3 buttons: .a (BtnA) and .b (BtnB)."""

    def __init__(self):
        self.a = Button(BTNA_PIN)
        self.b = Button(BTNB_PIN)

"""Raw infrared transmit/receive on the StickS3's built-in IR LED/receiver.

Pins ported from M5Stack's uiflow-micropython
(m5stack/libs/hardware/ir.py, the M5StickS3 entry in its board pin
map): tx_pin=GPIO46, rx_pin=GPIO42. Carrier frequency (38kHz) ported
from the same firmware's RMT-based IR module
(m5stack/cmodules/rmt_ir/py_rmt_ir.c).

This is a *raw pulse-train* transmitter/receiver, not a NEC decoder -
it's meant as a building block for devices that don't speak NEC, like
the LEGO RCX (see rcx_ir.py). MicroPython's stock esp32.RMT only
supports transmit, not capture, so IRReceiver bit-bangs pulse timing
off the receiver pin instead of using RMT for that half.

IMPORTANT for IRReceiver: M5Stack's own StickS3 docs note the speaker
amp (AW8737) must be off for IR reception to work reliably - presumably
switching noise on a shared rail/ground bleeding into the IR receiver's
sensitive analog front end. If you've called m5_power.power_on_speaker()
(or used m5_audio.Speaker) in the same script, call
m5_power.power_off_speaker() before reading from IRReceiver.
"""

from machine import Pin
from esp32 import RMT
import time

TX_PIN = 46
RX_PIN = 42

CARRIER_HZ = 38000


class IRTransmitter:
    """Sends a carrier-modulated pulse train on the IR LED.

    Durations alternate on (carrier modulating)/off, starting with on.
    """

    def __init__(self, pin_num=TX_PIN, carrier_hz=CARRIER_HZ, duty_pct=33):
        self._rmt = RMT(
            0,
            pin=Pin(pin_num),
            resolution_hz=1_000_000,  # 1 tick = 1us
            idle_level=False,  # LED off at rest
            tx_carrier=(carrier_hz, duty_pct, True),
        )

    def send(self, durations_us):
        """durations_us: pulse widths in microseconds, alternating
        on/off, starting with on. Each duration must be < ~32768 (the
        RMT's per-symbol limit at 1MHz resolution)."""
        self._rmt.write_pulses(list(durations_us), True)
        self._rmt.wait_done(timeout=2000)


class IRReceiver:
    """Captures raw mark/space pulse durations (microseconds) off the
    onboard IR receiver. The receiver IC already demodulates the 38kHz
    carrier and pulls its output low while IR is present, so "mark"
    here means "receiver output active", not "carrier toggling".

    This busy-polls rather than using an interrupt, which is simple and
    accurate enough for well-separated pulses (like RCX's 2400-baud
    framing) but will accumulate more jitter than an IRQ-driven capture
    if you need tight timing on fast protocols like NEC.
    """

    def __init__(self, pin_num=RX_PIN, max_pulses=512, idle_timeout_us=20000):
        self._pin = Pin(pin_num, Pin.IN, Pin.PULL_UP)
        self._max_pulses = max_pulses
        self._idle_timeout_us = idle_timeout_us

    def _active(self):
        return self._pin.value() == 0  # receiver output is active-low

    def read(self, timeout_ms=2000):
        """Waits up to timeout_ms for a burst to start; once started,
        returns a list of pulse durations (us) - mark, space, mark, ...
        - ending when the line goes idle for idle_timeout_us. Returns
        [] if nothing arrived within timeout_ms."""
        deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
        while not self._active():
            if time.ticks_diff(deadline, time.ticks_ms()) <= 0:
                return []

        durations = []
        state = True  # currently in a mark (active)
        t_edge = time.ticks_us()
        while len(durations) < self._max_pulses:
            t0 = time.ticks_us()
            while self._active() == state:
                if time.ticks_diff(time.ticks_us(), t0) > self._idle_timeout_us:
                    durations.append(time.ticks_diff(time.ticks_us(), t_edge))
                    return durations
            now = time.ticks_us()
            durations.append(time.ticks_diff(now, t_edge))
            t_edge = now
            state = not state
        return durations

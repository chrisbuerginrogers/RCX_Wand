"""Speaker (ES8311 codec + AW8737 amp) tone playback.

Pins and the codec bring-up sequence below are ported from M5Stack's
official M5Unified/uiflow-micropython firmware for this exact board:

  - I2S pins (github.com/m5stack/uiflow-micropython,
    m5stack/boards/M5STACK_StickS3/board_init.c):
    BCLK=GPIO17, WS=GPIO15, DOUT=GPIO14, DIN=GPIO16 (mic in, unused here).
  - ES8311 register map (github.com/m5stack/uiflow-micropython,
    m5stack/libs/driver/es8311/{__init__,reg}.py).
  - The specific StickS3 "MCLK from BCLK" bring-up sequence
    (register writes 0x00/0x01/0x02/0x0D/0x12/0x13/0x37 below) is
    ported from github.com/m5stack/M5Unified,
    src/M5Unified.cpp:_speaker_enabled_cb_sticks3(). It's used here
    because MicroPython's machine.I2S can't drive a separate MCLK pin
    (unlike the ESP-IDF driver M5Stack's own firmware uses), so the
    codec has to derive its master clock from BCLK instead - this
    exact register combination is StickS3's proven-working way to do
    that, rather than something worked out from scratch here.

Confirmed live on this repo's StickS3: a 440Hz tone played and was
audible (heard directly, not just "no exception raised").

CAVEAT - the I2C connection to the codec has been flaky. Across earlier
testing in this same session, `Speaker()` reliably raised
`OSError: [Errno 19] ENODEV` from the very first I2C write inside
_codec_init() - i2c.scan() on the internal bus found only 0x68 (IMU)
and 0x6e (PMIC), never the codec at 0x18 or its alternate 0x19, across
several conditions (at startup, after power_on_speaker(), with BCLK
actively clocked, at a slower I2C speed, with the 3.3V LDO rail forced
on). Then, after several power cycles/USB reconnects for unrelated
reasons, it started responding - i2c.scan() found 0x18, and its chip ID
registers read back exactly 0x83/0x11, the documented ES8311 identity
every ES8311 driver checks for. That timeline points to a marginal
physical connection (e.g. a connector or solder joint that needed
reseating) rather than a wiring/register bug, but if Speaker() starts
raising ENODEV again, that's the known failure mode, not a regression
in this code - see tilt_tone.py for an example of catching it rather
than crashing. Sample rate is fixed at 16000Hz stereo to match the
board firmware's own default I2S config for this specific bring-up
sequence; M5Stack's general Speaker_Class docs
(docs.m5stack.com/en/arduino/m5unified/speaker_class) default to
44100Hz, but that's for their C++ stack's own MCLK-pin-based codec
setup, not this BCLK-derived-clock one, so it isn't a drop-in number
here.
"""

from machine import Pin, I2C, I2S
import time
import math
import array

from m5 import m5_power

_ES8311_ADDR = 0x18

_REG_RESET = 0x00
_REG_CLK_MGR01 = 0x01
_REG_CLK_MGR02 = 0x02
_REG_SYSTEM0D = 0x0D
_REG_SYSTEM12 = 0x12
_REG_SYSTEM13 = 0x13
_REG_DAC_VOLUME = 0x32
_REG_DAC_RAMP = 0x37

_BCLK_PIN = 17
_WS_PIN = 15
_DOUT_PIN = 14

SAMPLE_RATE = 16000


class Speaker:
    def __init__(self, volume=70):
        m5_power.power_on_speaker()
        self._i2c = m5_power.internal_i2c()
        self._codec_init()
        self.set_volume(volume)
        self._i2s = I2S(
            0,
            sck=Pin(_BCLK_PIN),
            ws=Pin(_WS_PIN),
            sd=Pin(_DOUT_PIN),
            mode=I2S.TX,
            bits=16,
            format=I2S.STEREO,
            rate=SAMPLE_RATE,
            ibuf=4000,
        )

    def _write(self, reg, val):
        self._i2c.writeto_mem(_ES8311_ADDR, reg, bytes([val]))

    def _codec_init(self):
        # Full reset first (safe regardless of whatever state the codec
        # was left in by a previous script), then the StickS3 bring-up.
        self._write(_REG_RESET, 0x1F)
        time.sleep_ms(20)
        self._write(_REG_RESET, 0x00)

        self._write(_REG_RESET, 0x80)  # CSM power on
        self._write(_REG_CLK_MGR01, 0xB5)  # MCLK sourced from BCLK pin
        self._write(_REG_CLK_MGR02, 0x18)  # MULT_PRE = 3
        self._write(_REG_SYSTEM0D, 0x01)  # power up analog circuitry
        self._write(_REG_SYSTEM12, 0x00)  # power up DAC
        self._write(_REG_SYSTEM13, 0x10)  # enable output drive
        self._write(_REG_DAC_RAMP, 0x08)  # bypass DAC equalizer

    def set_volume(self, volume):
        """volume: 0-100."""
        if volume <= 0:
            reg32 = 0
        else:
            reg32 = min(255, int(volume * 256 / 100) - 1)
        self._write(_REG_DAC_VOLUME, reg32)

    def tone(self, freq_hz, duration_ms):
        """Play a square-cycle-free sine tone for duration_ms, blocking."""
        if freq_hz <= 0:
            time.sleep_ms(duration_ms)
            return

        n = max(4, SAMPLE_RATE // int(freq_hz))
        buf = array.array("h", bytes(n * 4))  # n stereo (L+R) int16 frames
        for i in range(n):
            sample = int(8000 * math.sin(2 * math.pi * i / n))
            buf[2 * i] = sample
            buf[2 * i + 1] = sample

        end = time.ticks_add(time.ticks_ms(), duration_ms)
        while time.ticks_diff(end, time.ticks_ms()) > 0:
            self._i2s.write(buf)

    def deinit(self):
        self._i2s.deinit()
        m5_power.power_off_speaker()

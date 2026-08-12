"""Shared helper for the M5PM1 power-management IC (I2C addr 0x6E).

StickS3 gates both the LCD block and the Grove connector's 5V pin through
this chip rather than through the ESP32 itself.
"""

from machine import Pin, I2C

_ADDR = 0x6E
_i2c = None


def _get_i2c():
    global _i2c
    if _i2c is None:
        _i2c = I2C(1, scl=Pin(48), sda=Pin(47), freq=100000)
    return _i2c


def internal_i2c():
    """The shared internal I2C bus (PMIC + IMU + audio codec all live here)."""
    return _get_i2c()


def set_bit(reg, bit, value):
    i2c = _get_i2c()
    current = i2c.readfrom_mem(_ADDR, reg, 1)[0]
    updated = (current | bit) if value else (current & ~bit)
    i2c.writeto_mem(_ADDR, reg, bytes([updated & 0xFF]))


def read_reg(reg, n=1):
    return _get_i2c().readfrom_mem(_ADDR, reg, n)


def power_on_lcd():
    """Enable the GPIO2 ("L3B") rail that feeds the ST7789 LCD block."""
    gpio2_bit = 1 << 2
    set_bit(0x16, gpio2_bit, False)  # gpio2 -> plain GPIO function
    set_bit(0x10, gpio2_bit, True)  # gpio2 -> output mode
    set_bit(0x13, gpio2_bit, False)  # gpio2 -> push-pull drive
    set_bit(0x11, gpio2_bit, True)  # gpio2 -> output high


def power_on_grove_5v():
    """Enable BOOST_EN, which feeds the Grove connector's 5V pin.

    Auto-clears on every reset/download, so this needs calling every boot.
    """
    pwr_cfg_reg = 0x06
    boost_en_bit = 1 << 3
    set_bit(pwr_cfg_reg, boost_en_bit, True)


_PWR_CFG_REG = 0x06
_CHG_EN_BIT = 1 << 0
_PWR_SRC_REG = 0x04

POWER_SOURCE_USB = 1 << 0
POWER_SOURCE_5VINOUT = 1 << 1
POWER_SOURCE_BATTERY = 1 << 2


def set_charge_enable(enable):
    """Enable/disable battery charging (PWR_CFG bit 0, CHG_EN).

    Same register as power_on_grove_5v()'s BOOST_EN, just a different
    bit - and it auto-clears on every reset/download the same way, so
    this needs calling every boot if you want charging on.
    """
    set_bit(_PWR_CFG_REG, _CHG_EN_BIT, enable)


def get_power_source():
    """Returns a bitmask OR of the POWER_SOURCE_* constants above -
    which supplies currently have power present, not which one the
    PMIC is drawing from. (M5Stack's own register comment describes
    this as an exclusive 0/1/2 selector, but that doesn't match a live
    reading confirmed on this repo's hardware: on USB with the battery
    installed, it read back 5 = USB|BATTERY, both bits set at once -
    so this is a presence bitmask, not a single source ID.)

    There's no real "is it charging right now" readback on this chip -
    M5Stack's own official driver's isCharging() is an unconditional
    `return false` stub. This is the closest available signal.
    """
    return read_reg(_PWR_SRC_REG)[0] & 0x07


_GPIO3_BIT = 1 << 3


def power_on_speaker():
    """Enable the GPIO3 rail that gates the AW8737 speaker amplifier.

    Same 0x16/0x10/0x13/0x11 function/mode/drive/output sequence as
    power_on_lcd(), just on gpio3 (bit 3) instead of gpio2 (bit 2). Call
    this before using m5_audio - like power_on_lcd(), it isn't known to
    auto-clear, but every M5Stack init path re-asserts it on every boot
    anyway, so this module does the same.

    Leave this off when you're not actively playing audio: the AW8737
    idling still draws meaningfully more than the PMIC's ~14uA
    power-off figure on a 250mAh cell, and M5Stack's own docs say the
    amp must be off for the IR receiver to work reliably (see m5_ir.py).
    """
    set_bit(0x16, _GPIO3_BIT, False)  # gpio3 -> plain GPIO function
    set_bit(0x10, _GPIO3_BIT, True)  # gpio3 -> output mode
    set_bit(0x13, _GPIO3_BIT, False)  # gpio3 -> push-pull drive
    set_bit(0x11, _GPIO3_BIT, True)  # gpio3 -> output high


def power_off_speaker():
    """Disable the AW8737 amp rail enabled by power_on_speaker()."""
    set_bit(0x11, _GPIO3_BIT, False)  # gpio3 -> output low

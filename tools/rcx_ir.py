"""Talk to a LEGO Mindstorms RCX brick over IR, in direct command mode.

Byte-level framing (0x55 0xFF 0x00 header, each data byte followed by
its bitwise complement, trailing checksum + its complement) is ported
from chrisbuerginrogers/micropython's M5StickS3/rcx_ir.py, itself ported
from NQC's real RCX transport implementation
(github.com/BrickBot/nqc, rcxlib/RCX_PipeTransport.cpp). Serial
parameters (2400 baud, 8 data bits, odd parity, 1 stop bit) are LEGO's
documented values.

What ISN'T independently verified here: how the UART bit-stream gets
modulated onto the IR carrier. This uses the standard on-off-keying
convention nearly every consumer IR link uses - carrier PRESENT for an
active/"space" bit, carrier ABSENT for idle/"mark" bit. If a real RCX
doesn't respond, flip SPACE_IS_CARRIER_ON below first.

── Motor opcodes: fixed, not ported as-is ─────────────────────────────
The sibling ESP32-RCX project (github.com/.../ESP32-RCX,
library/rcx_driver.py) encodes motor_on(direction=1) as opcode 0x21
with flag 0x40 - but 0x21 ("OnOffFloat") only ever encodes float/off/on
in its top two bits; it has no direction bit at all. Direction is a
*separate* direct command, opcode 0xE1 ("SetFwdSetRwdRewDir"). Likewise
0x13 ("SetPower")'s first parameter is a motor *bitmask* (0x01/0x02/
0x04), not a plain 0/1/2 index, and it takes a power *source* byte
(2 = constant) before the power value. Confirmed against LEGO's own
P-Brick Communication Protocol command table (see
lasm-opcode-reference.md, section 3 "Motors and outputs", at the
project root) - the methods below follow that table, not the ESP32-RCX
port:

    Opcode  Name                Params                       Top two bits (6-7)
    0x21    OnOffFloat          [flags|motor_mask]            00 float, 01 (0x40) off, 10 (0x80) on
    0xE1    SetFwdSetRwdRewDir  [flags|motor_mask]             00 "backwards", 01 (0x40) "reverse", 10 (0x80) forwards
    0x13    SetPower            [motor_mask, source, value]   source 2 = constant; value 0-7

lasm-opcode-reference.md's own wording for 0xE1's middle value (0x40)
is "reverse" *and* its 0x00 value is "backwards" - two names for what
sounds like the same thing, on the actual source document, not a
transcription slip. This picks the reading that keeps the encoding
consistent with 0x21's own convention (00 = the bitwise opposite of
10, not a third middle state) and leaves the ambiguous 0x40
("toggle"?) alone - so the RCX only ever sees 0x00 or 0x80 here,
never 0x40.

**Confirmed live on hardware 2026-08-10**: the two states 0x00/0x80
do produce two genuinely different, opposite motor directions - the
IR link, framing and opcodes are all working. Which of the two is
"forward" is a property of that particular motor's mounting/gearing,
not of the protocol, so _DIR_FORWARD/_DIR_REVERSE below are labeled
to match the motor tested, not derived from the opcode table - swap
them if a differently-geared motor comes out backwards.

Motor bitmask: A = 0x01, B = 0x02, C = 0x04 (OR together to command more
than one at once). Never tested against a real RCX brick - only that
construction and the RMT transmit path run without crashing.
"""

from m5.m5_ir import IRTransmitter, IRReceiver

SPACE_IS_CARRIER_ON = True  # flip this first if a real RCX stays silent

BIT_US = 417  # 1 / 2400 baud, rounded to the microsecond

# A handful of RCX opcodes, cross-checked between NQC's rcxlib, LEGO's
# own P-Brick Communication Protocol command table, and the community
# opcode reference at mralligator.com/rcx/opcodes.html. The RCX's reply
# opcode is the request's bitwise complement; NQC also toggles bit 0x08
# on repeated identical commands so the RCX can tell a fresh command
# from an echo of the last one - not needed for one-shot use here, so
# it's always sent clear.
OP_ALIVE = 0x10
OP_GET_VERSIONS = 0x15
OP_GET_VALUE = 0x12
OP_GET_BATTERY_POWER = 0x30
OP_SET_MOTOR_POWER = 0x13
OP_SET_MOTOR_ON_OFF = 0x21
OP_SET_MOTOR_DIRECTION = 0xE1
OP_PLAY_TONE = 0x23
OP_POWER_OFF = 0x60

MOTOR_A = 0x01
MOTOR_B = 0x02
MOTOR_C = 0x04

_POWER_SOURCE_CONSTANT = 2

_STATE_ON = 0x80
_STATE_OFF = 0x40
_STATE_FLOAT = 0x00

#: Confirmed live on hardware 2026-08-10: commanding 0x80 on motor A
#: turned it the opposite way from what its physical mounting/gearing
#: makes "forward" on this robot -- so these are swapped from the
#: original bit-pattern guess (0x80 = the RCX's own "forwards" state,
#: 0x00 its "backwards" state; see the docstring above). Which value
#: is physically forward is a property of how each motor is geared and
#: wired, not of the protocol, so this pairing is what matches *this*
#: build; a different motor/robot could come out the other way.
_DIR_FORWARD = 0x00
_DIR_REVERSE = 0x80
_DIR_TOGGLE = 0x40


def _is_on(uart_bit):
    return (uart_bit == 0) == SPACE_IS_CARRIER_ON


def _bits_for_byte(byte):
    bits = [0]  # start bit
    ones = 0
    for i in range(8):
        b = (byte >> i) & 1
        bits.append(b)
        ones += b
    bits.append(0 if (ones % 2 == 1) else 1)  # odd parity: total ones must be odd
    bits.append(1)  # stop bit
    return bits


def _encode_bits(raw_bytes):
    """UART-encodes raw_bytes (2400/8/odd/1) and returns a durations
    list (microseconds) ready for IRTransmitter.send()."""
    bits = []
    for byte in raw_bytes:
        bits.extend(_bits_for_byte(byte))

    runs = []  # (is_on, run_length_in_bits)
    prev = bits[0]
    run = 1
    for b in bits[1:]:
        if b == prev:
            run += 1
        else:
            runs.append((_is_on(prev), run))
            prev = b
            run = 1
    runs.append((_is_on(prev), run))

    if not runs[0][0]:
        # Leading idle before the first real edge - doesn't matter how
        # long we wait to start, so just drop it rather than send it.
        runs = runs[1:]

    return [length * BIT_US for _, length in runs]


def _decode_bits(durations):
    """Inverse of _encode_bits: durations (as returned by
    IRReceiver.read(), where durations[0] is always the first active
    period) back to a raw UART byte stream."""
    on_bit = 0 if SPACE_IS_CARRIER_ON else 1
    bits = []
    value = on_bit
    for dur in durations:
        n = max(1, round(dur / BIT_US))
        bits.extend([value] * n)
        value = 1 - value

    out = bytearray()
    i = 0
    while i + 11 <= len(bits):
        if bits[i] != 0:
            i += 1
            continue
        frame = bits[i:i + 11]
        data_bits, parity, stop = frame[1:9], frame[9], frame[10]
        if stop != 1:
            i += 1
            continue
        byte = 0
        for pos, b in enumerate(data_bits):
            byte |= b << pos
        ones = bin(byte).count("1")
        expected_parity = 0 if (ones % 2 == 1) else 1
        if parity != expected_parity:
            i += 1
            continue
        out.append(byte)
        i += 11
    return bytes(out)


def _build_message(opcode, args=b""):
    payload = bytes([opcode & 0xFF]) + bytes(args)
    checksum = sum(payload) & 0xFF

    out = bytearray(b"\x55\xff\x00")
    for b in payload:
        out.append(b)
        out.append((~b) & 0xFF)
    out.append(checksum)
    out.append((~checksum) & 0xFF)
    return bytes(out)


def _parse_reply(raw_bytes):
    """raw_bytes: a decoded literal UART byte stream. Finds the sync
    header, verifies every byte against its complement and the trailing
    checksum, and returns just the payload (opcode + args) - or None if
    nothing valid was found."""
    idx = raw_bytes.find(b"\x55\xff\x00")
    if idx < 0:
        return None
    body = raw_bytes[idx + 3:]
    if len(body) < 4 or len(body) % 2 != 0:
        return None

    data_bytes = bytearray()
    for i in range(0, len(body), 2):
        b, comp = body[i], body[i + 1]
        if (b ^ comp) != 0xFF:
            return None
        data_bytes.append(b)

    if len(data_bytes) < 2:
        return None
    payload, checksum = bytes(data_bytes[:-1]), data_bytes[-1]
    if (sum(payload) & 0xFF) != checksum:
        return None
    return payload


class RCX:
    def __init__(self):
        self._tx = IRTransmitter()
        self._rx = IRReceiver()

    def send(self, opcode, args=b""):
        """Sends a command; doesn't wait for a reply."""
        message = _build_message(opcode, args)
        self._tx.send(_encode_bits(message))

    def send_and_receive(self, opcode, args=b"", timeout_ms=500):
        """Sends a command and waits for the RCX's reply payload
        (opcode byte + argument bytes; complement/checksum already
        verified and stripped). Returns None on timeout or a framing
        failure."""
        self.send(opcode, args)
        durations = self._rx.read(timeout_ms=timeout_ms)
        if not durations:
            return None
        return _parse_reply(_decode_bits(durations))

    def alive(self, timeout_ms=500):
        """Pings the RCX. Returns True if it replied at all."""
        return self.send_and_receive(OP_ALIVE, timeout_ms=timeout_ms) is not None

    def play_tone(self, freq_hz, duration_1_100s):
        args = bytes([freq_hz & 0xFF, (freq_hz >> 8) & 0xFF, duration_1_100s & 0xFF])
        return self.send_and_receive(OP_PLAY_TONE, args)

    def get_battery_mv(self):
        reply = self.send_and_receive(OP_GET_BATTERY_POWER)
        if reply is None or len(reply) < 3:
            return None
        return reply[1] | (reply[2] << 8)

    # ------------------------------------------------------------------
    # Direct-mode motor control
    # ------------------------------------------------------------------
    # Fire-and-forget, like every other direct command here - the RCX
    # holds each of these (power, direction, on/off) until told
    # otherwise, so a dropped packet just means the old state persists
    # one loop longer rather than the motor stopping.

    def set_power(self, motor_mask, power):
        """power: 0-7. motor_mask: MOTOR_A/B/C, OR'd together for more
        than one at once. Out-of-range values are ignored by the RCX
        rather than clamped, so this clamps first."""
        power = max(0, min(7, int(power)))
        self.send(OP_SET_MOTOR_POWER,
                  bytes([motor_mask, _POWER_SOURCE_CONSTANT, power]))

    def set_direction(self, motor_mask, forward=True):
        flag = _DIR_FORWARD if forward else _DIR_REVERSE
        self.send(OP_SET_MOTOR_DIRECTION, bytes([flag | motor_mask]))

    def motor_on(self, motor_mask):
        self.send(OP_SET_MOTOR_ON_OFF, bytes([_STATE_ON | motor_mask]))

    def motor_off(self, motor_mask):
        self.send(OP_SET_MOTOR_ON_OFF, bytes([_STATE_OFF | motor_mask]))

    def motor_float(self, motor_mask):
        """Let the motor spin freely rather than holding it off."""
        self.send(OP_SET_MOTOR_ON_OFF, bytes([_STATE_FLOAT | motor_mask]))


if __name__ == "__main__":
    rcx = RCX()
    print("Pinging RCX...")
    if rcx.alive():
        print("RCX responded!")
        mv = rcx.get_battery_mv()
        if mv is not None:
            print("Battery: {} mV".format(mv))
    else:
        print("No reply. Point the StickS3's IR window at the RCX's IR")
        print("window from a few inches away. If it still fails, try")
        print("flipping SPACE_IS_CARRIER_ON at the top of this file.")

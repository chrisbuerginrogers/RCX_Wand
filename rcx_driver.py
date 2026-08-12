"""
rcx_driver.py — Core RCX driver for ESP32 MicroPython.

This is the readable source. The identical code lives as the `code` string
inside pyscript/esp32_driver.py and is what actually gets uploaded to the ESP32
via the "Install RCX Lib" button.

Usage on an M5Stack StickS3 (default board — built-in IR LED, no extra
wiring needed):
    from rcx_driver import rcx
    rcx.beep()
    rcx.move(speed=7, duration=2.0)
    rcx.turn_left(duration=0.5)
    rcx.stop()

To target Eric's original board instead (generic ESP32 + external IR
LED on GPIO2, see docs/hardware.md), either change DEFAULT_BOARD below
(then re-run "Install RCX Lib") or build your own instance:
    from rcx_driver import RCX, BOARD_ESP32
    rcx = RCX(board=BOARD_ESP32)
"""

import time

try:
    import machine
    import esp32
    from machine import Pin
    MICROPYTHON = True
except ImportError:
    MICROPYTHON = False

BOARD_ESP32     = "esp32"     # Eric's original circuit: GPIO2 -> transistor -> external IR LED
BOARD_M5STICKS3 = "m5sticks3" # M5Stack StickS3's onboard IR LED (GPIO46)

_BOARD_TX_PIN = {
    BOARD_ESP32: 2,
    BOARD_M5STICKS3: 46,
}

# Only the StickS3 has an onboard IR receiver (GPIO42, ported from
# chrisbuerginrogers/micropython's m5_ir.py). BOARD_ESP32's circuit is
# transmit-only, so it has no entry here -- download_firmware() needs a
# receiver and will refuse to run without one.
_BOARD_RX_PIN = {
    BOARD_M5STICKS3: 42,
}

# Which board the module-level `rcx` singleton (bottom of this file) targets.
# Change this and re-run "Install RCX Lib" to switch boards without editing
# every script that does `from rcx_driver import rcx`.
DEFAULT_BOARD = BOARD_M5STICKS3


class RCX:
    """
    LEGO RCX 2.0 controller via IR (ESP32 MicroPython, direct command mode).

    Sends IR packets using the ESP32 RMT peripheral with proper 12-bit UART
    framing at 2400 baud over a 38 kHz carrier. Direct commands are
    fire-and-forget — no response reading. download_firmware() is the
    exception: it needs an IR receiver to read the RCX's block acks, so
    it only works on boards listed in _BOARD_RX_PIN (BOARD_M5STICKS3).

    Pin defaults, by board:
        BOARD_M5STICKS3 (default) ir_pin = 46  onboard IR LED, no external circuit needed
                                   ir_rx_pin = 42  onboard IR receiver
        BOARD_ESP32               ir_pin = 2   RMT output -> IR LED driver transistor
                                   ir_rx_pin = none (transmit-only circuit)

    Pass ir_pin/ir_rx_pin explicitly to override a board's defaults.
    """

    SOUND_BLIP  = 1
    SOUND_BEEP  = 2
    SOUND_SWEEP = 3
    SOUND_PLING = 4
    SOUND_BUZZ  = 5

    MOTOR_A = 0
    MOTOR_B = 1
    MOTOR_C = 2

    def __init__(self, board=DEFAULT_BOARD, ir_pin=None, ir_rx_pin=None):
        self._last_tx_opcode = None
        self.rmt = None
        self._rx_pin = None
        self.board = board
        pin_num = ir_pin if ir_pin is not None else _BOARD_TX_PIN.get(board, 2)
        rx_pin_num = ir_rx_pin if ir_rx_pin is not None else _BOARD_RX_PIN.get(board)

        if MICROPYTHON:
            try:
                if board == BOARD_M5STICKS3:
                    # StickS3 firmware wants the newer RMT constructor
                    # (resolution_hz/idle_level) rather than clock_div. Pin
                    # and carrier settings ported from chrisbuerginrogers/
                    # micropython's m5_ir.py:
                    # https://github.com/chrisbuerginrogers/micropython/tree/main/M5StickS3
                    self.rmt = esp32.RMT(0, pin=Pin(pin_num), resolution_hz=1_000_000,
                                         idle_level=False, tx_carrier=(38000, 33, True))
                else:
                    self.rmt = esp32.RMT(0, pin=Pin(pin_num), clock_div=80,
                                         tx_carrier=(38000, 33, 1))
            except Exception as e:
                print("RCX init warning:", e)

            if rx_pin_num is not None:
                try:
                    self._rx_pin = Pin(rx_pin_num, Pin.IN, Pin.PULL_UP)
                except Exception as e:
                    print("RCX RX init warning:", e)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _send_ir_bytes(self, byte_data):
        """
        Encode bytes as 12-bit raw UART frames over IR and transmit via RMT.

        Each byte is framed as:
          START (light ON) | 8 data bits LSB-first | odd parity | STOP | GAP

        Consecutive bits in the same state are merged into one longer RMT pulse.
        Parity: if even number of 1-bits -> parity=1 (light OFF); odd -> parity=0 (ON).
        """
        BIT_US = round(1000000 / 2400)  # ~417 us per bit at 2400 baud

        pulses = []
        current_on = True  # first bit is always START (light ON)
        current_dur = 0

        def add_bit(is_on):
            nonlocal current_on, current_dur
            if is_on == current_on:
                current_dur += BIT_US
            else:
                pulses.append(current_dur)
                current_dur = BIT_US
                current_on = is_on

        for b in byte_data:
            add_bit(True)  # START bit: light ON
            ones = 0
            for i in range(8):
                bit = (b >> i) & 1
                if bit:
                    ones += 1
                add_bit(bit == 0)  # 0 -> light ON, 1 -> light OFF
            add_bit(ones % 2 != 0)  # odd parity
            add_bit(False)  # STOP bit
            add_bit(False)  # inter-byte gap

        if current_dur > 0:
            pulses.append(current_dur)

        self.rmt.write_pulses(pulses, 1)

    def _recv_ir_bytes(self, timeout_ms=1000, idle_timeout_us=20000, max_pulses=1600):
        """
        Capture a raw mark/space pulse train off the IR receiver and decode it
        back into a UART byte stream. Mirrors _send_ir_bytes' polarity: a mark
        (receiver active) is a START bit or a 0 data/parity bit.

        Bit-banged (MicroPython's esp32.RMT can't capture), ported from
        chrisbuerginrogers/micropython's m5_ir.py IRReceiver. Returns b"" if
        nothing arrives within timeout_ms or no receiver is configured.
        """
        if not self._rx_pin:
            return b""

        def active():
            return self._rx_pin.value() == 0  # receiver output is active-low

        deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
        while not active():
            if time.ticks_diff(deadline, time.ticks_ms()) <= 0:
                return b""

        durations = []
        state = True  # currently in a mark (light ON)
        t_edge = time.ticks_us()
        while len(durations) < max_pulses:
            t0 = time.ticks_us()
            while active() == state:
                if time.ticks_diff(time.ticks_us(), t0) > idle_timeout_us:
                    durations.append(time.ticks_diff(time.ticks_us(), t_edge))
                    return self._decode_ir_bytes(durations)
            now = time.ticks_us()
            durations.append(time.ticks_diff(now, t_edge))
            t_edge = now
            state = not state

        return self._decode_ir_bytes(durations)

    def _decode_ir_bytes(self, durations):
        """
        Inverse of _send_ir_bytes: alternating mark/space durations (first is
        always a mark) -> the UART byte stream they encode. Frames without a
        valid start(0)/stop(1) or parity are skipped rather than aborting, so
        one corrupted frame doesn't take down the whole reply.
        """
        BIT_US = round(1000000 / 2400)
        bits = []
        value = 0  # mark == bit value 0 (matches _send_ir_bytes' START convention)
        for dur in durations:
            n = dur // BIT_US
            if n < 1:
                n = 1
            bits.extend([value] * n)
            value = 1 - value

        out = bytearray()
        i = 0
        nbits = len(bits)
        while i + 11 <= nbits:
            if bits[i] != 0:
                i += 1
                continue
            data_bits = bits[i + 1:i + 9]
            parity = bits[i + 9]
            stop = bits[i + 10]
            if stop != 1:
                i += 1
                continue
            byte = 0
            ones = 0
            for pos in range(8):
                b = data_bits[pos]
                byte |= b << pos
                ones += b
            expected_parity = 0 if (ones % 2 != 0) else 1
            if parity != expected_parity:
                i += 1
                continue
            out.append(byte)
            i += 11
        return bytes(out)

    def _parse_reply(self, raw_bytes):
        """
        raw_bytes: a decoded UART byte stream from _recv_ir_bytes(). Finds the
        sync header, verifies every byte against its complement and the
        trailing checksum, and returns just the payload (opcode + args) -- or
        None if nothing valid was found.
        """
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

    def _build(self, opcode, params=None):
        """
        Build one RCX direct-command packet.

        Format: [55 FF 00] [op] [op^FF] [p1] [p1^FF] ... [ck] [ck^FF]

        Toggle bit (0x08) flips only when this opcode is identical to the
        last one actually transmitted -- ported from NQC's real transport
        (rcxlib/RCX_PipeTransport.cpp: "if (byte==fTxLastCommand) byte ^= 8").
        Flipping it unconditionally (the previous behavior here) is wrong:
        the RCX uses this bit to tell a fresh repeated command from an echo
        of the last one, so it should stay put across different commands.
        """
        if params is None:
            params = []
        tx_op = opcode
        if self._last_tx_opcode is not None and tx_op == self._last_tx_opcode:
            tx_op ^= 0x08
        self._last_tx_opcode = tx_op
        pkt = bytearray([0x55, 0xFF, 0x00, tx_op, tx_op ^ 0xFF])
        ck = tx_op
        for p in params:
            pkt.append(p)
            pkt.append(p ^ 0xFF)
            ck = (ck + p) & 0xFF
        pkt.append(ck)
        pkt.append(ck ^ 0xFF)
        return bytes(pkt)

    def _send(self, opcode, params=None):
        """Build a packet and transmit it. 100 ms gap after each send."""
        if not self.rmt:
            print("RCX: not available")
            return
        pkt = self._build(opcode, params)
        self._send_ir_bytes(pkt)
        time.sleep_ms(100)

    def _send_and_recv(self, opcode, params=None, timeout_ms=1000):
        """Build, transmit, and wait for + validate the RCX's reply.
        Returns the reply payload (opcode-complement byte + args) or
        None once timeout_ms has genuinely elapsed with nothing valid.

        Retries the listen within the remaining time budget if a
        capture doesn't parse as a valid reply, rather than treating
        that as a timeout. Confirmed live: _recv_ir_bytes() commits to
        "this is the reply" the moment the RX pin first goes active,
        and a brief noise blip right after our own transmission (most
        likely self-hearing off the transmit LED -- captured durations
        in that case were nowhere near clean multiples of the 417us
        bit period a real, even error-ridden, RCX reply would show)
        was enough to trigger that and end the attempt on garbage
        before the genuine reply had a chance to arrive. This is also
        why direct commands here were always fire-and-forget in
        practice -- ping/get_battery_mv() never decoded a reply even
        though the RCX was clearly receiving and acting on commands.
        """
        if not self.rmt:
            print("RCX: not available")
            return None
        pkt = self._build(opcode, params)
        self._send_ir_bytes(pkt)
        deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
        while True:
            remaining = time.ticks_diff(deadline, time.ticks_ms())
            if remaining <= 0:
                return None
            raw = self._recv_ir_bytes(timeout_ms=remaining)
            if not raw:
                return None  # genuine timeout: nothing arrived at all
            reply = self._parse_reply(raw)
            if reply is not None:
                return reply
            # Got something, but it didn't parse as a valid reply --
            # likely noise. Keep listening with whatever time is left
            # instead of giving up on it.

    # ------------------------------------------------------------------
    # Firmware download
    # ------------------------------------------------------------------

    def download_firmware(self, data, start=0x8000, chunk_size=200,
                           timeout_ms=1500, on_progress=None):
        """
        Download RAM firmware to the RCX over IR, replaying NQC's real
        transfer sequence (rcxlib/RCX_Link.cpp: RCX_Link::TransferFirmware):
        ping, delete any existing firmware, begin a load at `start` with a
        16-bit checksum, send the image in acked chunks, then send the
        unlock command that boots it.

        `data`: raw firmware bytes, e.g. flattened from a LEGO/ROBOLAB
        FIRMWARE.TXT S-record file -- see tools/srec_to_micropython.py.
        `start`: load address; the ROBOLAB "fast" firmware uses 0x8000.

        Needs a board with an IR receiver configured (BOARD_M5STICKS3's
        onboard one, or ir_rx_pin passed to __init__) -- unlike the other
        commands here, firmware download is two-way: each chunk must be
        acked before the next is sent, or the RCX silently drops out of
        download mode.

        Returns True if every step was acked, False on the first
        unacknowledged one (data already sent that far is simply discarded
        by the RCX -- its ROM bootloader implements delete/begin/download
        independent of whatever firmware is or isn't currently loaded, so a
        failed attempt just means trying again, not a bricked brick).
        """
        if not self._rx_pin:
            print("RCX: no IR receiver configured for board", self.board,
                  "-- firmware download needs a two-way link")
            return False

        if self._send_and_recv(0x10, timeout_ms=timeout_ms) is None:
            print("RCX: no reply to ping")
            return False

        # LEGO's own P-Brick Communication Protocol table (this project's
        # lasm-opcode-reference.md, section 15) names 0x65 with these exact
        # params "GoIntoBootMode" (LASM `reset 1,3,5,7,11`), not "delete
        # firmware" as this comment (and NQC's naming) has it -- but the
        # bytes sent match the official table exactly, and "awaits a new
        # firmware download" is what this step needs to do either way, so
        # this is a naming mismatch, not a behavior one. Left as ESP32-RCX
        # wrote it rather than renamed, to keep this a faithful copy.
        if self._send_and_recv(0x65, [1, 3, 5, 7, 0x0b], timeout_ms=timeout_ms) is None:
            print("RCX: no reply to delete-firmware")
            return False

        checksum = sum(data[:min(len(data), 0x4c00)]) & 0xFFFF
        begin_args = [start & 0xFF, (start >> 8) & 0xFF,
                      checksum & 0xFF, (checksum >> 8) & 0xFF, 0x00]
        if self._send_and_recv(0x75, begin_args, timeout_ms=timeout_ms) is None:
            print("RCX: no reply to begin-firmware")
            return False

        total = len(data)
        sent = 0
        seq = 1
        while sent < total:
            remaining = total - sent
            n = remaining if remaining <= chunk_size else chunk_size
            chunk = data[sent:sent + n]
            block_seq = 0 if remaining <= chunk_size else seq  # 0 marks the final block
            block_sum = sum(chunk) & 0xFF
            args = [block_seq & 0xFF, (block_seq >> 8) & 0xFF,
                    n & 0xFF, (n >> 8) & 0xFF] + list(chunk) + [block_sum]
            if self._send_and_recv(0x45, args, timeout_ms=timeout_ms) is None:
                print("RCX: no reply to firmware block, seq", block_seq,
                      "offset", sent, "of", total)
                return False
            sent += n
            seq += 1
            if on_progress:
                on_progress(sent, total)

        # No retry and a long timeout on this one: it's what boots the new
        # firmware, and the RCX takes a moment before it can reply.
        self._send_and_recv(0xa5, [ord('L'), ord('E'), ord('G'), ord('O'), 174],
                             timeout_ms=max(timeout_ms, 3000))
        print("RCX: firmware download complete,", total, "bytes")
        return True

    # ------------------------------------------------------------------
    # Direct commands
    # ------------------------------------------------------------------

    def ping(self):
        self._send(0x10)

    def beep(self, sound=2):
        self._send(0x51, [sound])

    def motor_on(self, motor_id, direction=0):
        if 0 <= motor_id <= 2:
            port = 1 << motor_id
            flag = 0x80 if direction == 0 else 0x40
            self._send(0x21, [flag | port])

    def motor_off(self, motor_id):
        if 0 <= motor_id <= 2:
            port = 1 << motor_id
            self._send(0x21, [0x40 | port])

    def motor_brake(self, motor_id):
        if 0 <= motor_id <= 2:
            port = 1 << motor_id
            self._send(0x21, [0xC0 | port])

    def set_power(self, motor_id, power):
        if 0 <= motor_id <= 2 and 0 <= power <= 7:
            self._send(0x13, [motor_id, power])

    # ------------------------------------------------------------------
    # High-level robot commands
    # ------------------------------------------------------------------

    def move(self, speed=7, duration=None):
        self.set_power(0, speed)
        self.set_power(1, speed)
        self.motor_on(0, direction=0)
        self.motor_on(1, direction=0)
        if duration is not None:
            self.wait(duration)
            self.stop()

    def backward(self, speed=7, duration=None):
        self.set_power(0, speed)
        self.set_power(1, speed)
        self.motor_on(0, direction=1)
        self.motor_on(1, direction=1)
        if duration is not None:
            self.wait(duration)
            self.stop()

    def turn_left(self, speed=7, duration=None):
        self.set_power(0, speed)
        self.set_power(1, speed)
        self.motor_on(0, direction=0)
        self.motor_on(1, direction=1)
        if duration is not None:
            self.wait(duration)
            self.stop()

    def turn_right(self, speed=7, duration=None):
        self.set_power(0, speed)
        self.set_power(1, speed)
        self.motor_on(0, direction=1)
        self.motor_on(1, direction=0)
        if duration is not None:
            self.wait(duration)
            self.stop()

    def spin_left(self, speed=7, duration=None):
        self.turn_left(speed=speed, duration=duration)

    def spin_right(self, speed=7, duration=None):
        self.turn_right(speed=speed, duration=duration)

    def stop(self):
        self.motor_off(0)
        self.motor_off(1)
        self.motor_off(2)

    def brake(self):
        self.motor_brake(0)
        self.motor_brake(1)
        self.motor_brake(2)

    def wait(self, seconds):
        time.sleep(seconds)

    def set_all_power(self, power):
        self.set_power(0, power)
        self.set_power(1, power)
        self.set_power(2, power)

    def reverse_turn_left(self, speed=7, duration=None):
        self.set_power(0, speed)
        self.set_power(1, speed)
        self.motor_on(0, direction=1)
        self.motor_on(1, direction=0)
        if duration is not None:
            self.wait(duration)
            self.stop()

    def reverse_turn_right(self, speed=7, duration=None):
        self.set_power(0, speed)
        self.set_power(1, speed)
        self.motor_on(0, direction=0)
        self.motor_on(1, direction=1)
        if duration is not None:
            self.wait(duration)
            self.stop()


# Ready-to-use instance — targets DEFAULT_BOARD above
rcx = RCX()

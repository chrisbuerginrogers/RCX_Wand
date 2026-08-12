"""
rcx_driver.py — Core RCX driver for ESP32 MicroPython.

  >>> Runs ON the M5StickS3, but lives in tools/ with the rest of the
      Mac-side machinery. The device's filesystem is flat, so this is
      copied to its *root*; tools/stick_link.py's DEVICE_RUNTIME is
      where that mapping is defined. run_on_rcx.py and
      load_firmware_on_rcx.py push it automatically. <<<

Originally ported from ESP32-RCX/library/rcx_driver.py, and kept
alongside rcx_ir.py (which RCXWand.py uses for direct motor control)
because only this one implements the acked block-transfer protocol that
firmware and program downloads need.

Usage on an M5Stack StickS3 (default board — built-in IR LED, no extra
wiring needed):
    from rcx_driver import rcx
    rcx.beep()
    rcx.move(speed=7, duration=2.0)
    rcx.turn_left(duration=0.5)
    rcx.stop()

For a generic ESP32 with an external IR LED on GPIO2, change
DEFAULT_BOARD below or build your own instance:
    from rcx_driver import RCX, BOARD_ESP32
    rcx = RCX(board=BOARD_ESP32)

Before changing anything about the IR link, read CLAUDE.md. Five real
bugs were fixed here on 2026-08-12 — an unpowered receiver rail, a
missing rmt.wait_done(), an unmasked toggle bit in replies, a parser
anchored on a sync that never survives, and SetSourceValue's fifth
param — and all five were invisible without live measurement.
"""

import time

try:
    import machine
    import esp32
    from machine import Pin, UART
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

# UART used for receive on the StickS3. Transmit still has to go through
# the RMT (it needs the 38 kHz carrier), but the receiver IC demodulates
# the carrier for us and its output *is* a plain 2400 8-O-1 UART line --
# idle high, mark (IR present) = logic 0 = start bit -- exactly the
# framing _send_ir_bytes() builds. Letting the UART peripheral do the
# sampling replaces a bit-banged capture that never once decoded a real
# reply. `tx` has to be given a pin even though nothing transmits on it;
# GPIO1 is EXT_GPIO1 on the HAT header and is otherwise unused here.
_RX_UART_ID = 1
_RX_UART_TX_PIN = 1

# Firmware-download pacing, matching what LEGO's own tool does: leave
# 100 ms between packets, and on an unanswered one retry up to 5 times
# before giving up and asking the operator whether to keep going.
SEND_GAP_MS = 100
RETRY_GAP_MS = 100
MAX_RETRIES = 5
GIVE_UP_PAUSE_MS = 500

# Block-transfer opcodes -- BeginOfTask, BeginOfSub, ContinueDL. These
# are the only compiled-LASM commands whose reply is worth waiting for;
# everything else compiled from a top-level line is a one-shot direct
# command.
_ACKED_OPCODES = (0x25, 0x35, 0x45)

# Sources and system parameters used by post_boot_init(). Mirrors the
# authoritative enums in lasm_compiler.py (source.ctl / system
# parameters.ctl); duplicated rather than imported because
# lasm_compiler.py needs dataclasses, pathlib and a 100KB OpCodes.json
# and is deliberately a Mac-side-only module.
SRC_CONSTANT = 2
SRC_SYSTEM = 24
SYSPARAM_TRANSMITTER_RANGE = 13
SYSPARAM_FLOAT_DURING_INACTIVE_MOTOR_PWM = 14
SYSPARAM_PREAMBLE_SIZE = 17
SYSPARAM_POWER_DOWN_DELAY = 20
SYSPARAM_WATCH_FORMAT = 21

#: Minutes of idle before the RCX powers itself down, set at the end of
#: every firmware download. 0 would mean "never".
DEFAULT_POWER_DOWN_MINUTES = 15


def _power_up_ir():
    """Enable the rails the StickS3's IR daughterboard needs.

    Both halves of the IR block -- the IR928 transmit LED and the
    VSOP38338 receiver -- hang off the GROVE_5V boost rail (see the
    "Power Network" sheet of StickS3_schematic.pdf, which draws
    GROVE_5V feeding both IR_TX and IR_RX), and that rail auto-clears
    on every reset/download.

    With it off, GPIO42 is not merely dead but actively misleading:
    the receiver's output is high-Z, so the R6/R5 10K/20K divider on
    the daughterboard pulls the pin to 0V, which reads as "receiver
    active" forever. Measured live on 2026-08-12: 2000/2000 samples
    low before power_on_grove_5v(), a clean idle-high line with zero
    transitions over a full second after it. That single missing call
    is why no reply was ever decoded, and why pointing a TV remote at
    the Stick during earlier debugging produced nothing either.

    The speaker amp goes off for the documented reason (M5Stack's own
    docs say the AW8737 breaks IR reception -- see m5/m5_ir.py). Note
    that stick_ui.UI() constructs a Speaker and therefore turns that
    amp back *on*, so anything driving IR from the UI has to power it
    down again afterwards.
    """
    try:
        from m5 import m5_power
        m5_power.power_off_speaker()
        m5_power.power_on_grove_5v()
        time.sleep_ms(200)
    except Exception as e:
        print("RCX: could not set up IR power rails:", e)


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
        self._uart = None
        self.board = board
        pin_num = ir_pin if ir_pin is not None else _BOARD_TX_PIN.get(board, 2)
        rx_pin_num = ir_rx_pin if ir_rx_pin is not None else _BOARD_RX_PIN.get(board)

        if MICROPYTHON:
            if board == BOARD_M5STICKS3:
                _power_up_ir()

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
                    # No PULL_UP: the daughterboard already defines this
                    # node with a 10K/20K divider off the receiver's 5V
                    # output, and the internal pull-up fights it -- with
                    # the receiver unpowered it drags the pin to ~1.0V,
                    # square in the ESP32's undefined band, which is
                    # what turned coupling off our own transmit LED into
                    # the phantom "blips" seen during earlier debugging.
                    self._rx_pin = Pin(rx_pin_num, Pin.IN)
                except Exception as e:
                    print("RCX RX init warning:", e)

                try:
                    self._uart = UART(_RX_UART_ID, baudrate=2400, bits=8,
                                      parity=1, stop=1, tx=_RX_UART_TX_PIN,
                                      rx=rx_pin_num, rxbuf=4096, timeout=20)
                except Exception as e:
                    print("RCX UART RX init warning:", e)

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
        # write_pulses() is NON-blocking -- measured live on 2026-08-12,
        # it returned in 0 ms on a packet that took 35 ms on air. Without
        # this wait, everything after a send races the send itself: a
        # 200-byte firmware chunk is ~1.9 s of transmission but _send()
        # only slept 100 ms, so consecutive chunks overlapped on the wire.
        # That is the most likely explanation for the one download that
        # visibly counted on the RCX's screen and then stalled partway.
        # m5/m5_ir.py's IRTransmitter always paired the two calls; this
        # module had dropped the wait_done() half.
        self.rmt.wait_done(timeout=sum(pulses) // 1000 + 2000)

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

    def _find_reply(self, raw_bytes, want):
        """The RCX's reply payload (opcode + args) in raw_bytes, or None.

        Deliberately does NOT look for the 55 FF 00 sync. The RCX sends a
        preamble ahead of every transmission and our receiver's AGC is
        still settling through it, so the header reliably arrives as
        garbage while the packet body behind it decodes perfectly.
        Measured live on 2026-08-12: 40 consecutive pings each came back
        looking like

            <our echo> <preamble garbage> e7 18 e7 18

        -- a flawless reply (opcode E7, complement 18, checksum E7) that a
        sync-anchored parser discarded every single time. Ping decoding
        went from 0/10 to 8/10 on this change alone.

        Instead: find maximal runs of byte/complement pairs, and inside
        each run look for a stretch starting with the opcode we expect and
        ending at its own checksum. Complement pairs plus a checksum is
        structure enough to make a false positive unlikely, and anchoring
        on `want` also steps over our own echo without special-casing it.
        """
        n = len(raw_bytes)
        i = 0
        best = None
        while i < n - 1:
            if (raw_bytes[i] ^ raw_bytes[i + 1]) != 0xFF:
                i += 1
                continue
            data = bytearray()
            j = i
            while j + 1 < n and (raw_bytes[j] ^ raw_bytes[j + 1]) == 0xFF:
                data.append(raw_bytes[j])
                j += 2
            k = len(data)
            for a in range(k):
                if (data[a] & 0xF7) != want:  # see _reply_opcode on the mask
                    continue
                s = data[a]
                b = a + 1
                while b < k:
                    if (s & 0xFF) == data[b]:
                        cand = bytes(data[a:b])
                        if best is None or len(cand) > len(best):
                            best = cand
                        break
                    s += data[b]
                    b += 1
            i = j
        return best

    @staticmethod
    def _reply_opcode(opcode):
        """The RCX answers with the complement of the command byte, toggle
        bit cleared -- ping 0x10 -> 0xE7, ContinueDL 0x45 -> 0xB2. See
        lasm-opcode-reference.md, "The reply-byte rule".

        Compare against `reply[0] & 0xF7`, never against reply[0] itself:
        the RCX mirrors our toggle bit back in its replies, so a real ping
        answer is 0xE7 or 0xEF depending on how _build() toggled the
        command. Matching the exact byte silently drops every other
        reply -- which is precisely what happened on the first successful
        firmware download: 60 acks seen across 120 blocks, exactly half,
        because the alternating 0x45/0x4D chunks came back 0xB2/0xBA.
        """
        return (~opcode) & 0xF7

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

    def _send_and_recv_once(self, opcode, params=None, timeout_ms=400):
        """One transmit + one listen window, via the hardware UART.

        Returns the reply payload (reply opcode + args) or None. The UART
        is flushed immediately before transmitting so stale bytes from a
        previous exchange can't be mistaken for this one's answer; our own
        echo still arrives first and is filtered out by opcode.
        """
        if not self.rmt:
            print("RCX: not available")
            return None
        if not self._uart:
            return self._send_and_recv_bitbang(opcode, params, timeout_ms)

        pkt = self._build(opcode, params)
        self._uart.read()  # discard anything left over
        self._send_ir_bytes(pkt)  # returns only once the packet is fully on air

        want = self._reply_opcode(opcode)
        deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
        buf = bytearray()
        while time.ticks_diff(deadline, time.ticks_ms()) > 0:
            n = self._uart.any()
            if n:
                buf += self._uart.read(n)
                payload = self._find_reply(bytes(buf), want)
                if payload:
                    return payload
            time.sleep_ms(2)
        return None

    def _send_and_recv(self, opcode, params=None, timeout_ms=400, retries=5):
        """Transmit and wait for the RCX's reply, retrying up to `retries`
        times. Returns the reply payload, or None if every try went
        unanswered."""
        for _ in range(retries):
            reply = self._send_and_recv_once(opcode, params, timeout_ms)
            if reply is not None:
                return reply
            time.sleep_ms(RETRY_GAP_MS)
        return None

    def _send_and_recv_bitbang(self, opcode, params=None, timeout_ms=1000):
        """Fallback for boards with an RX pin but no usable UART.

        Kept only as a last resort. The bit-banged capture this rests on
        never decoded a single real reply in practice, and the reason was
        never the timing code: GPIO42 was being held at 0V by the
        receiver's output divider because the receiver itself had no
        power (see _power_up_ir()), so _recv_ir_bytes() saw "active"
        immediately, every time, and captured coupling noise. Prefer
        _send_and_recv_once()'s UART path, which is what the RCX's acks
        actually come back through.
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
            payload = self._find_reply(raw, self._reply_opcode(opcode))
            if payload:
                return payload
            # Got something, but it didn't parse as a valid reply --
            # likely noise. Keep listening with whatever time is left
            # instead of giving up on it.

    # ------------------------------------------------------------------
    # Firmware download
    # ------------------------------------------------------------------

    def download_firmware(self, data, start=0x8000, chunk_size=200,
                           timeout_ms=400, on_progress=None, on_stall=None,
                           send_reset=True,
                           power_down_minutes=DEFAULT_POWER_DOWN_MINUTES,
                           acked=True):
        """
        Download RAM firmware to the RCX over IR: put the brick into boot
        mode, begin a load at `start` with a 16-bit checksum, send the
        image in acked 200-byte blocks 100 ms apart, then send the unlock
        command that boots it.

        Once the firmware is up this also runs post_boot_init(), setting
        the power-down delay to `power_down_minutes` -- a fresh firmware
        boot leaves the watch display free-running otherwise.

        Loading the default programs is a separate step, done by the Mac
        (load_firmware_on_rcx.py compiles tools/default_codes.lasm and
        feeds the result to send_program()), because compiling LASM
        needs dataclasses, pathlib and a 100KB OpCodes.json -- none of
        which belong on the Stick.

        `acked=False` is blind mode, for a Stick whose IR receiver does
        not work: every packet still goes out on the same 100 ms pacing,
        but nothing waits for or checks a reply. Delivery is usually
        fine -- the RCX receives us reliably -- but you lose the block
        status bytes entirely, so a corrupted block is silently accepted
        and the firmware simply will not boot. Watch the RCX's own
        screen: it counts blocks as they land.

        `data`: raw firmware bytes, e.g. flattened from a LEGO/ROBOLAB
        FIRMWARE.TXT S-record file -- see tools/srec_to_micropython.py.
        `start`: load address; the ROBOLAB "fast" firmware uses 0x8000.
        `send_reset`: skip this if the RCX is already sitting in boot
        mode (blank screen), since the ROM does not answer 0x65 again.
        `on_stall(step, tries)`: called after MAX_RETRIES unanswered
        attempts at one packet, following a GIVE_UP_PAUSE_MS pause.
        Return True to keep retrying that same packet, False to abort.
        With no callback, a stall aborts.

        Every step is acked: the RCX replies with the complement of the
        command byte plus a status byte, and for blocks that status is
        meaningful (0 OK, 3 block checksum error, 4 firmware checksum
        error, 6 download not active -- see lasm-opcode-reference.md).
        A non-zero status is reported and aborts, since continuing past
        one just desyncs the rest of the transfer.

        Needs a board with an IR receiver configured (BOARD_M5STICKS3's
        onboard one, or ir_rx_pin passed to __init__).

        Returns True if the whole image was accepted, False otherwise.
        A failed attempt is not a bricked brick -- the ROM bootloader
        implements begin/download independent of whatever firmware is or
        isn't loaded, so it just means trying again.
        """
        if acked and not self._uart and not self._rx_pin:
            print("RCX: no IR receiver configured for board", self.board,
                  "-- firmware download needs a two-way link, or acked=False")
            return False
        if acked and not self.receiver_looks_alive():
            print("RCX: WARNING -- the IR receiver looks dead or noisy.")
            print("     Every block will go unacked and this will fail at block 1.")
            print("     Re-run with acked=False (--blind) to send anyway.")

        def step(label, opcode, params=None, timeout=None):
            """One packet. Acked with the retry-then-ask policy, or
            fire-and-forget in blind mode."""
            if not acked:
                self._send(opcode, params)
                return b"\x00\x00"  # stand-in "ok" so callers can carry on
            while True:
                reply = self._send_and_recv(opcode, params,
                                            timeout_ms=timeout or timeout_ms,
                                            retries=MAX_RETRIES)
                if reply is not None:
                    return reply
                print("RCX: no reply to", label, "after", MAX_RETRIES, "tries")
                time.sleep_ms(GIVE_UP_PAUSE_MS)
                if not on_stall or not on_stall(label, MAX_RETRIES):
                    return None

        if send_reset:
            # lasm-opcode-reference.md section 15 names 0x65 with these
            # params "GoIntoBootMode" (LASM `reset 1,3,5,7,11`), not
            # "delete firmware" as NQC's naming has it -- the bytes match
            # the official table either way.
            if step("boot-mode", 0x65, [1, 3, 5, 7, 0x0b], timeout=1000) is None:
                return False
            time.sleep_ms(SEND_GAP_MS)

        checksum = sum(data[:min(len(data), 0x4c00)]) & 0xFFFF
        begin_args = [start & 0xFF, (start >> 8) & 0xFF,
                      checksum & 0xFF, (checksum >> 8) & 0xFF, 0x00]
        if step("begin-firmware", 0x75, begin_args, timeout=1000) is None:
            return False
        time.sleep_ms(SEND_GAP_MS)

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
            reply = step("block {}".format(block_seq), 0x45, args)
            if reply is None:
                print("RCX: giving up at offset", sent, "of", total)
                return False
            if acked and len(reply) > 1 and reply[1] != 0:
                print("RCX: block", block_seq, "rejected, status", reply[1],
                      "(3=block checksum, 4=firmware checksum, 6=download not active)")
                return False
            sent += n
            seq += 1
            if on_progress:
                on_progress(sent, total)
            time.sleep_ms(SEND_GAP_MS)

        # No retry and a long timeout on this one: it's what boots the new
        # firmware, and the RCX takes a moment before it can reply. A
        # successful unlock answers with LEGO's easter-egg string -- the
        # ROM only sends it if valid firmware was actually downloaded, so
        # it is the one genuinely conclusive "it worked" signal there is.
        if acked:
            boot = self._send_and_recv(0xa5, [ord('L'), ord('E'), ord('G'), ord('O'), 174],
                                        timeout_ms=max(timeout_ms, 3000), retries=2)
        else:
            self._send(0xa5, [ord('L'), ord('E'), ord('G'), ord('O'), 174])
            boot = None
        if boot and len(boot) > 1:
            try:
                print("RCX: unlocked --", bytes(boot[1:]).decode())
            except Exception:
                print("RCX: unlocked, reply", bytes(boot).hex())
        print("RCX: firmware download complete,", total, "bytes")

        time.sleep_ms(1000)  # let the new firmware finish coming up
        self.post_boot_init(power_down_minutes=power_down_minutes)
        return True

    # ------------------------------------------------------------------
    # Post-boot setup
    # ------------------------------------------------------------------

    def set_system_param(self, param, value, acked=False):
        """`set 24,<param>,2,<value>` -- SetSourceValue writing a system
        parameter: dest = (SRC_SYSTEM, param), origin = (SRC_CONSTANT,
        value).

        FIVE params, not four: the origin value is 16-bit LO/HI.
        Confirmed against a real capture of `set 2,2,2,2`:

            55 FF 00  0D F2  02 FD  02 FD  02 FD  02 FD  00 FF  15 EA

        i.e. opcode 0x05 (toggled to 0x0D), params [2,2,2,2,0], checksum
        0x0D+8 = 0x15. Sent with only four params the RCX does not reply
        at all.

        Defaults to fire-and-forget because it has to: transmit to the
        RCX is reliable, but these acks are frequently undecodable at
        this link's current quality (see CLAUDE.md, "What is still
        marginal"). They do land -- sending the watch-format parameter
        visibly reset the RCX's clock while its ack never decoded once.
        """
        args = [SRC_SYSTEM, param, SRC_CONSTANT, value & 0xFF, (value >> 8) & 0xFF]
        if acked:
            return self._send_and_recv(0x05, args, retries=MAX_RETRIES)
        self._send(0x05, args)
        return None

    def post_boot_init(self, power_down_minutes=15):
        """The init sequence LEGO's own LabVIEW tool issues right after
        UnlockFirmware, with the power-down delay as a parameter.

        The four parameters and their values come from a real LabVIEW
        block diagram (see CLAUDE.md), not from this project's guesswork.
        nWatchFormat is the one that stops the display free-running after
        a fresh firmware boot.
        """
        for param, value, name in (
                (SYSPARAM_POWER_DOWN_DELAY, power_down_minutes, "nPowerDownDelay"),
                (SYSPARAM_PREAMBLE_SIZE, 3, "nPreambleSize"),
                (SYSPARAM_WATCH_FORMAT, 1, "nWatchFormat"),
                (SYSPARAM_FLOAT_DURING_INACTIVE_MOTOR_PWM, 1,
                 "bFloatDuringInactiveMotorPWM"),
        ):
            print("RCX: set 24,{},2,{}  ({})".format(param, value, name))
            self.set_system_param(param, value)
            time.sleep_ms(SEND_GAP_MS)

    def send_program(self, commands, on_progress=None, acked=True):
        """Send compiled LASM commands -- [(opcode, params_bytes), ...].

        Block-transfer commands (BeginOfTask, BeginOfSub, ContinueDL)
        are acked before the next goes out, the same mechanism firmware
        download uses. Everything else is a direct command and goes out
        fire-and-forget.

        Send order is load-bearing and must not be reordered: each
        `prgm N` selects the slot the BeginOfTask after it downloads
        into.

        `acked=False` is blind mode: transmit everything on the same
        100 ms pacing but never wait for a reply. For a Stick whose IR
        receiver does not work, this is the only way to download a
        program at all -- and it is far less hopeless than it sounds,
        because the *RCX* receives us fine (every one of the 120 blocks
        of a real firmware download acked cleanly when the receiver was
        still alive). What you lose is verification, not delivery:
        nothing here can tell you a block was accepted, so the program
        running on the brick is the only confirmation you get.
        Returns True in blind mode simply to mean "all bytes sent".
        """
        for i, (opcode, params) in enumerate(commands):
            if acked and opcode in _ACKED_OPCODES:
                reply = self._send_and_recv(opcode, list(params),
                                            retries=MAX_RETRIES)
                if reply is None:
                    print("RCX: no reply to opcode {:#04x}, step {}/{}".format(
                        opcode, i + 1, len(commands)))
                    return False
                if len(reply) > 1 and reply[1] != 0:
                    print("RCX: opcode {:#04x} rejected, status {}".format(
                        opcode, reply[1]))
                    return False
            else:
                self._send(opcode, list(params))
            if on_progress:
                on_progress(i + 1, len(commands))
            time.sleep_ms(SEND_GAP_MS)
        return True

    def receiver_looks_alive(self, settle_ms=800):
        """Rough check of whether the IR receiver is usable at all.

        A healthy VSOP38338 with power and no IR in view idles HIGH and
        perfectly quiet. Two failure modes seen on real hardware, both
        of which make every acked operation pointless:

          * stuck low with no transitions -- either unpowered (the rail
            auto-clears every reset; see _power_up_ir) or the receiver's
            output never reaches the pin
          * low most of the time with hundreds of transitions/second --
            a desensitised front end that cannot decode even its own
            transmit LED at point-blank range

        Cheap enough to call before a long transfer so the caller can
        suggest blind mode instead of failing on block 1 of 120.
        """
        if not self._rx_pin:
            return False
        time.sleep_ms(settle_ms)
        lows = 0
        for _ in range(1000):
            if self._rx_pin.value() == 0:
                lows += 1
        last = self._rx_pin.value()
        trans = 0
        t0 = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), t0) < 300:
            v = self._rx_pin.value()
            if v != last:
                trans += 1
                last = v
        return lows < 100 and trans < 20

    def start_task(self, number=0):
        """StartTask (0x71) -- runs a task that has been downloaded."""
        self._send(0x71, [number])

    def stop_all(self):
        """StopAllTasks (0x50)."""
        self._send(0x50)

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

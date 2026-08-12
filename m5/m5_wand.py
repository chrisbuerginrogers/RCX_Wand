"""
m5_wand — tap a LEGO card, and the Stick wires that card's bricks together
==========================================================================

  >>> Runs ON the M5StickS3. Needs the rest of m5/ and the RFID2 Unit. <<<

Tap any connection card on the reader. The Stick listens for the bricks
carrying that card, connects to them, and wires them together:

    Controller   + Double Motor    one joystick per wheel
    Controller   + Single Motor    the left stick drives it
    Color Sensor + Double Motor    reflected light drives both wheels
    Color Sensor + Single Motor    reflected light drives it

The card's *color* is only its address -- it does not pick a behavior.
Purple, green, orange or any other card all work the same way: whatever
answers to that card is what runs.

The bricks have to be switched on and carrying the card. The Stick waits
for them rather than giving up, naming what is still missing on screen;
BtnA stops, and tapping a different card switches straight over.

**A brick that something else is already connected to is not
advertising**, so it cannot be found. If a brick is blinking away and
never turns up here, disconnect it from the LEGO app first -- each brick
has exactly one connection slot.

This is the library half. The program that drives it is
`M5StickS3/Wand_driving.py` -- run that.

── What is in here ───────────────────────────────────────────────────────
Three things: the connection card decode, a BLE central for the bricks'
GATT protocol, and the screen, separated by the banner comments below.
Plus the pieces a driving loop needs -- `find_pair()`, `connect_brick()`,
`Watcher`, `sender_speeds()`, `apply_speeds()` -- which is what makes
`Wand_driving.py` short enough to read in one go and change freely.

The BLE section is the part worth lifting out for other projects: the
`Controller` / `ColorSensor` / `SingleMotor` / `DoubleMotor` classes are
usable on their own, with no screen and no card reader:

    from m5.m5_wand import Controller, DoubleMotor, PURPLE

    ctrl = Controller();  ctrl.connect(PURPLE, 6055)
    drive = DoubleMotor(); drive.connect(PURPLE, 6055)
    while True:
        ctrl.update()
        drive.tank(ctrl.left_percent, ctrl.right_percent)

── How a brick is found ──────────────────────────────────────────────────
A brick tapped with a connection card advertises that card in LEGO
manufacturer data (company ID 0x0397):

    [product_group, product_device, card_color, serial_lo, serial_hi]

so `connect(color, serial)` is a scan filter, not a search by name. Match
on **both**: serials are allocated per color, so RED #1126 and
PURPLE #1126 are different cards and a filter on serial alone silently
collides.

The color on the wire and on the card is the *firmware* code, and
everything above `firmware_to_app()` here speaks App codes (PURPLE = 6).
Firmware 2 is purple and App 2 is yellow, so skipping the conversion
yields plausible-looking wrong answers rather than an error.

── Not the broadcast protocol ────────────────────────────────────────────
These bricks also talk to each other connectionlessly, by broadcasting
FD02 *service data* that a motor obeys with nobody connected. That is a
different protocol under the same UUID and this file deliberately does
not implement it: it carries seven speed steps per stick and neither
joystick angle nor reflected light. Those broadcasts are non-connectable
advertisements, which is how the scan filter below tells them apart from
a brick that can actually be connected to.

── One connection slot per brick ─────────────────────────────────────────
A brick accepts one connection at a time. While this runs the Stick holds
it, so the LEGO app cannot see the brick -- and if a real controller is
also driving the motor over the broadcast protocol, two things are giving
it orders and the last packet heard wins. Switch the other controller off.
"""

import bluetooth
import struct
import time

from m5.m5_audio import Speaker
from m5.m5_display import Display, WHITE, BLACK
from m5.m5_rfid import ReadError


# ═════════════════════════════════════════════════════════════════════════
#   Card colors
# ═════════════════════════════════════════════════════════════════════════
# App color codes, matching the legoeducation package, lelib and picolib,
# so a number means the same thing across all of them.

NOCOLOR = 0
RED = 1
YELLOW = 2
BLUE = 3
TEAL = 4
GREEN = 5
PURPLE = 6
WHITE_CARD = 7
MAGENTA = 8
ORANGE = 9
AZURE = 10

COLOR_NAMES = {
    NOCOLOR: 'NOCOLOR', RED: 'RED', YELLOW: 'YELLOW', BLUE: 'BLUE',
    TEAL: 'TEAL', GREEN: 'GREEN', PURPLE: 'PURPLE', WHITE_CARD: 'WHITE',
    MAGENTA: 'MAGENTA', ORANGE: 'ORANGE', AZURE: 'AZURE',
}

#: Firmware color code (what is on the card and on the wire) -> App code.
_FIRMWARE_TO_APP = {9: 1, 7: 2, 3: 3, 5: 4, 6: 5, 2: 6, 10: 7,
                    1: 8, 8: 9, 4: 10}


def firmware_to_app(firmware_color):
    """Firmware color code -> App code, or None if it is not a card color."""
    return _FIRMWARE_TO_APP.get(firmware_color)


def color_name(app_color):
    return COLOR_NAMES.get(app_color, str(app_color))


# ═════════════════════════════════════════════════════════════════════════
#   LEGO connection cards
# ═════════════════════════════════════════════════════════════════════════
# The cards are NTAG/Ultralight (SAK 0x00, 7-byte UID) and carry the color
# and serial in the clear from page 4:
#
#     page 4   4C 33 47 30                    ASCII "L3G0", a magic marker
#     page 5   00 <color> <serial hi> <lo>    firmware color, serial is
#                                             big-endian
#     page 6   00 00 00 00
#     page 7   FF EE DD CC                    fixed filler
#
# Read from a purple #6055 card:
#
#     4C334730 000217A7
#              ^^ ^^^^-- 0x17A7 = 6055
#              +-------- 0x02 = firmware PURPLE
#
# The serial is big-endian here and little-endian in the advertisement.
# decode_pages() hands back an integer so callers never see the
# difference, but do not copy raw bytes from one to the other.

CARD_MAGIC = b'L3G0'
FIRST_DATA_PAGE = 4


class NotALegoCard(Exception):
    """A tag is there and answered, and it is not one of these.

    Distinct from m5_rfid.ReadError, which means the read did not
    complete and is worth retrying -- the driver has already retried it
    three times, re-selecting the tag each go, before it raises. Getting
    these backwards is what makes a good card look like a bad one.
    """


def decode_pages(data):
    """(app_color, serial) from the 16 bytes read at page 4.

    Pure: no reader involved, so it can be checked off-hardware. Raises
    NotALegoCard if the magic marker is missing, which is what tells a
    LEGO card apart from any other NTAG that happens to be nearby.
    """
    if data is None or len(data) < 8:
        raise ValueError('need at least 8 bytes from page {}, got {}'.format(
            FIRST_DATA_PAGE, 0 if data is None else len(data)))
    if bytes(data[0:4]) != CARD_MAGIC:
        raise NotALegoCard('no {} marker (got {})'.format(
            CARD_MAGIC, ''.join('%02X' % b for b in data[0:4])))

    firmware_color = data[5]
    serial = (data[6] << 8) | data[7]
    app = _FIRMWARE_TO_APP.get(firmware_color)
    if app is None:
        raise NotALegoCard('unknown color code {:#04x}'.format(firmware_color))
    return app, serial


def read_card(rfid):
    """(uid, app_color, serial) for the card on the reader, or None.

    None means no tag in the field. Raises NotALegoCard for a tag that is
    there but is not one, and lets m5_rfid.ReadError through for a read
    that did not complete.
    """
    uid = rfid.read_uid()
    if uid is None:
        return None
    if rfid.sak != 0x00:
        raise NotALegoCard('SAK {:#04x}; LEGO cards are Ultralight/NTAG (0x00)'
                           .format(rfid.sak))
    data = rfid.read_pages(FIRST_DATA_PAGE)
    if data is None:
        # Still in the field but will not answer an unauthenticated read at
        # all, which means a MIFARE Classic.
        raise NotALegoCard('tag will not answer an unauthenticated read; '
                           'LEGO cards are Ultralight/NTAG')
    color, serial = decode_pages(data)
    return bytes(uid), color, serial


# ═════════════════════════════════════════════════════════════════════════
#   BLE — the FD02 GATT protocol
# ═════════════════════════════════════════════════════════════════════════

# The service is 0000FD02-0000-1000-8000-00805F9B34FB, which is the SIG
# base UUID with FD02 in it -- so a brick may declare it either as a
# 16-bit UUID or in full, and MicroPython reports back whichever it was
# told. The two do not compare equal, so both are kept and both accepted.
# The characteristics have 0001/0002 in the second group, off the base
# pattern, so they are genuinely 128-bit and have one spelling each.
_SERVICE_UUID16 = bluetooth.UUID(0xFD02)
_SERVICE_UUID128 = bluetooth.UUID('0000fd02-0000-1000-8000-00805f9b34fb')
_WRITE_UUID = bluetooth.UUID('0000fd02-0001-1000-8000-00805f9b34fb')
_NOTIFY_UUID = bluetooth.UUID('0000fd02-0002-1000-8000-00805f9b34fb')
_CCCD_UUID = bluetooth.UUID(0x2902)

_LEGO_COMPANY_ID = 0x0397
_AD_MANUFACTURER = 0xFF
_MFR_LEN = 5                    # [group, device, color, serial_lo, serial_hi]

#: Advertisement types. 3 is ADV_NONCONN_IND -- the connectionless FD02
#: beacon. Skipping it is what stops a scan for "the purple controller"
#: latching onto a packet it can never connect to.
_ADV_NONCONN_IND = 3

_IRQ_SCAN_RESULT = 5
_IRQ_SCAN_DONE = 6
_IRQ_PERIPHERAL_CONNECT = 7
_IRQ_PERIPHERAL_DISCONNECT = 8
_IRQ_GATTC_SERVICE_RESULT = 9
_IRQ_GATTC_SERVICE_DONE = 10
_IRQ_GATTC_CHARACTERISTIC_RESULT = 11
_IRQ_GATTC_CHARACTERISTIC_DONE = 12
_IRQ_GATTC_DESCRIPTOR_RESULT = 13
_IRQ_GATTC_DESCRIPTOR_DONE = 14
_IRQ_GATTC_WRITE_DONE = 17
_IRQ_GATTC_NOTIFY = 18
_IRQ_MTU_EXCHANGED = 21

# ── product IDs ──────────────────────────────────────────────────────────
SINGLE_MOTOR = 512
DOUBLE_MOTOR = 513
COLOR_SENSOR = 514
CONTROLLER = 515

PRODUCT_NAMES = {
    SINGLE_MOTOR: 'Single Motor',
    DOUBLE_MOTOR: 'Double Motor',
    COLOR_SENSOR: 'Color Sensor',
    CONTROLLER: 'Controller',
}

# ── RPC opcodes ──────────────────────────────────────────────────────────
_INFO_REQUEST = 0
_INFO_RESPONSE = 1
_DEVICE_NOTIFICATION_REQUEST = 40
_DEVICE_NOTIFICATION_RESPONSE = 41
_DEVICE_NOTIFICATION = 60
_MOTOR_RUN_COMMAND = 122
_MOTOR_STOP_COMMAND = 138
_MOTOR_SET_SPEED_COMMAND = 140
_MOVEMENT_MOVE_TANK_COMMAND = 156
_MOVEMENT_STOP_COMMAND = 168

CLOCKWISE = 0
COUNTERCLOCKWISE = 1

#: Motor bit masks. A Single Motor is always LEFT (bit 0); a Double Motor
#: uses one, the other, or both.
MOTOR_LEFT = 1
MOTOR_RIGHT = 2
MOTOR_BOTH = 3

# ── device notification sub-messages ─────────────────────────────────────
# Inside one DeviceNotification the payload is a chain of [type][fixed
# body] with no length prefix per item, so walking it needs every size
# known: a wrong size desynchronises everything after it.
_N_INFO = 0
_N_IMU = 1
_N_CARD = 3
_N_BUTTON = 4
_N_MOTOR = 10
_N_COLOR = 12
_N_CONTROLLER = 15
_N_GESTURE = 16

_N_FORMAT = {
    _N_INFO: '<BB',
    _N_IMU: '<BBhhhhhhhhh',
    _N_CARD: '<bH',
    _N_BUTTON: '<B',
    _N_MOTOR: '<BBHhblb',
    _N_COLOR: '<bBHHHHBB',
    _N_CONTROLLER: '<bbhh',
    _N_GESTURE: '<b',
}
_N_SIZE = {t: struct.calcsize(f) for t, f in _N_FORMAT.items()}

_INFO_RESPONSE_FORMAT = '<BBHBBHBBHHH'

#: Preferred ATT MTU. The default 23 leaves 20 bytes of notification
#: payload, and a motor reporting two motors plus battery, button and card
#: overflows that -- the tail is dropped by the radio, not by this code,
#: so it looks like a brick that stopped reporting rather than a truncated
#: packet. Raising it is not optional.
_MTU = 128

_RX_SLOTS = 4
_RX_SLOT_LEN = 96

_POLL_MS = 20
_SCAN_INTERVAL_US = 30000
_CONNECT_TIMEOUT_MS = 8000
_STEP_TIMEOUT_MS = 5000
_DEFAULT_UPDATE_MS = 100


#: Set True (or `m5_wand.VERBOSE = True` from the REPL) to print GATT
#: handles, unknown packets and dropped writes. The one switch worth
#: reaching for when a brick connects but then does nothing.
VERBOSE = False


class LegoError(Exception):
    """Something in the connect sequence did not finish."""


def _lego_mfr_offset(adv):
    """Offset of the LEGO manufacturer payload in adv, or -1.

    Returns a bare integer and allocates nothing -- not a tuple, not a
    slice. It runs inside the BLE scan callback, where touching the heap
    can fail outright, so the length check happens here rather than being
    handed back to the caller.

    An advertisement is a chain of length-prefixed AD structures. A
    malformed one ends the walk instead of raising: anything in the room
    can be broadcasting and none of it is under our control.
    """
    i = 0
    end = len(adv)
    while i + 1 < end:
        length = adv[i]
        if length == 0 or i + 1 + length > end:
            break
        if (adv[i + 1] == _AD_MANUFACTURER and length >= 3 + _MFR_LEN
                and adv[i + 2] | (adv[i + 3] << 8) == _LEGO_COMPANY_ID):
            return i + 4
        i += 1 + length
    return -1


class _Central(object):
    """The one BLE radio, shared by every device object.

    MicroPython allows a single bluetooth.BLE() and a single irq handler,
    so connections cannot each own one. This routes every event to the
    right device by connection handle, and owns scanning -- which is
    exclusive, so only one connect() can be in progress at a time.
    """

    def __init__(self):
        self._ble = bluetooth.BLE()
        self._ble.active(True)
        try:
            self._ble.config(mtu=_MTU)
        except (AttributeError, OSError):
            # Firmware without a configurable MTU. Motor notifications will
            # be truncated; nothing else here breaks.
            print('m5_wand: MTU not configurable, expect short packets')
        self._ble.irq(self._irq)

        self._by_handle = {}
        self._connecting = None         # device awaiting PERIPHERAL_CONNECT
        self._connect_failed = False

        # Scan filter and result. Every field is allocated here, once,
        # because the scan callback may not allocate.
        self._want_product = -1
        self._want_color = -1
        self._want_serial = -1
        self._found = False
        self._found_addr = bytearray(6)
        self._found_addr_type = 0
        self._found_product = 0
        self._found_color = 0
        self._found_serial = 0
        self._scanning = False
        self._rescan = False
        self.seen = 0                   # every advertisement, LEGO or not

        # discover() mode: instead of stopping at the first match, note
        # which product types are on the air under one card. A bit mask of
        # small ints, so the scan callback can update it without
        # allocating.
        self._collect = False
        self._products = 0

    # ── IRQ ──────────────────────────────────────────────────────────────
    def _irq(self, event, data):
        if event == _IRQ_SCAN_RESULT:
            addr_type, addr, adv_type, rssi, adv = data
            self.seen = (self.seen + 1) & 0xFFFF
            if adv_type == _ADV_NONCONN_IND or self._found:
                return
            off = _lego_mfr_offset(adv)
            if off < 0:
                return
            product = (adv[off] << 8) | adv[off + 1]
            color = _FIRMWARE_TO_APP.get(adv[off + 2], -1)
            serial = adv[off + 3] | (adv[off + 4] << 8)
            if self._want_color >= 0 and color != self._want_color:
                return
            if self._want_serial >= 0 and serial != self._want_serial:
                return
            if self._collect:
                bit = product - SINGLE_MOTOR
                if 0 <= bit <= 3:
                    self._products |= 1 << bit
                return
            if self._want_product >= 0 and product != self._want_product:
                return
            # addr is only valid inside this callback, so copy it out now,
            # a byte at a time -- a slice would allocate.
            for k in range(6):
                self._found_addr[k] = addr[k]
            self._found_addr_type = addr_type
            self._found_product = product
            self._found_color = color
            self._found_serial = serial
            self._found = True

        elif event == _IRQ_SCAN_DONE:
            # gap_scan() is not safe to call from here; the main loop
            # restarts it.
            self._scanning = False
            self._rescan = True

        elif event == _IRQ_PERIPHERAL_CONNECT:
            conn_handle, addr_type, addr = data
            device = self._connecting
            self._connecting = None
            if device is None:
                self._ble.gap_disconnect(conn_handle)
                return
            device._on_connect(conn_handle)
            self._by_handle[conn_handle] = device

        elif event == _IRQ_PERIPHERAL_DISCONNECT:
            conn_handle = data[0]
            if self._connecting is not None:
                # Refused or dropped before it was ever up.
                self._connecting = None
                self._connect_failed = True
            device = self._by_handle.pop(conn_handle, None)
            if device is not None:
                device._on_disconnect()

        elif event == _IRQ_MTU_EXCHANGED:
            conn_handle, mtu = data
            device = self._by_handle.get(conn_handle)
            if device is not None:
                device._mtu = mtu

        elif event == _IRQ_GATTC_SERVICE_RESULT:
            conn_handle, start_handle, end_handle, uuid = data
            device = self._by_handle.get(conn_handle)
            if device is not None and (uuid == _SERVICE_UUID16 or
                                       uuid == _SERVICE_UUID128):
                device._svc = (start_handle, end_handle)

        elif event == _IRQ_GATTC_SERVICE_DONE:
            device = self._by_handle.get(data[0])
            if device is not None:
                device._step_done = True

        elif event == _IRQ_GATTC_CHARACTERISTIC_RESULT:
            # The tuple grew an end_handle in newer firmware; the value
            # handle is at index 2 and the UUID last either way.
            device = self._by_handle.get(data[0])
            if device is None:
                return
            value_handle = data[2]
            uuid = data[-1]
            if uuid == _WRITE_UUID:
                device._tx = value_handle
            elif uuid == _NOTIFY_UUID:
                device._rx = value_handle

        elif event == _IRQ_GATTC_CHARACTERISTIC_DONE:
            device = self._by_handle.get(data[0])
            if device is not None:
                device._step_done = True

        elif event == _IRQ_GATTC_DESCRIPTOR_RESULT:
            # Indexed from both ends rather than unpacked: an unpack that
            # does not match the firmware's tuple raises *inside* the
            # callback, where it surfaces as "Unhandled exception in IRQ
            # callback handler" and nothing else -- the discovery then
            # completes having found nothing, which reads as a device
            # with no CCCD.
            device = self._by_handle.get(data[0])
            if device is None or data[-1] != _CCCD_UUID:
                return
            dsc_handle = data[-2]
            # The lowest CCCD above the notify value is the one that
            # belongs to it. Searching the whole service can turn up
            # another characteristic's.
            if dsc_handle > device._rx and (device._cccd is None or
                                            dsc_handle < device._cccd):
                device._cccd = dsc_handle

        elif event == _IRQ_GATTC_DESCRIPTOR_DONE:
            device = self._by_handle.get(data[0])
            if device is not None:
                device._step_done = True

        elif event == _IRQ_GATTC_WRITE_DONE:
            # Only the CCCD write is checked -- commands go out
            # write-without-response and never raise this.
            device = self._by_handle.get(data[0])
            if device is not None:
                device._write_status = data[-1]

        elif event == _IRQ_GATTC_NOTIFY:
            conn_handle, value_handle, notify_data = data
            device = self._by_handle.get(conn_handle)
            if device is not None:
                device._stash(notify_data)

    # ── scanning ─────────────────────────────────────────────────────────
    def scan_for(self, product, color=-1, serial=-1, timeout_ms=None,
                 on_wait=None):
        """(addr_type, addr, product, color, serial) for the first match.

        Returns None if timeout_ms passes with nothing matching.
        timeout_ms=None waits indefinitely, which is what "wait for the
        brick to be switched on" means. on_wait() is called about every
        _POLL_MS so the caller can animate, or bail out by raising.
        """
        self._want_product = product
        self._want_color = color if color is not None else -1
        self._want_serial = serial if serial is not None else -1
        self._found = False
        self._rescan = False
        self._start_scan()

        deadline = None
        if timeout_ms is not None:
            deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
        try:
            while not self._found:
                if self._rescan:
                    self._rescan = False
                    self._start_scan()
                if deadline is not None and \
                        time.ticks_diff(deadline, time.ticks_ms()) <= 0:
                    return None
                if on_wait is not None:
                    on_wait()
                time.sleep_ms(_POLL_MS)
            return (self._found_addr_type, bytes(self._found_addr),
                    self._found_product, self._found_color, self._found_serial)
        finally:
            self.stop_scan()

    def discover(self, color, serial, ready=None, on_wait=None,
                 timeout_ms=None):
        """Which product types are advertising under this card.

        Returns a bit mask, one bit per type -- `product_bit()` reads it.
        This is what makes a tap self-explaining: rather than assuming
        from the card's color which bricks exist, ask the air.

        Keeps listening until ready(mask) says it has enough, so a brick
        switched on after the tap still turns up. on_wait(mask) runs
        every poll so the caller can show what has been heard so far, or
        bail out by raising.

        A brick that something else is already connected to -- the LEGO
        app, say -- is **not** advertising and will not appear here. That
        is the usual reason a brick you can see blinking does not show up.
        """
        if ready is None and timeout_ms is None:
            raise ValueError('discover() needs a ready() test or a timeout, '
                             'or it can never return')
        self._want_product = -1
        self._want_color = color if color is not None else -1
        self._want_serial = serial if serial is not None else -1
        self._products = 0
        self._found = False
        self._rescan = False
        self._collect = True
        self._start_scan()

        deadline = None
        if timeout_ms is not None:
            deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
        try:
            while True:
                mask = self._products
                if ready is not None and ready(mask):
                    return mask
                if self._rescan:
                    self._rescan = False
                    self._start_scan()
                if deadline is not None and \
                        time.ticks_diff(deadline, time.ticks_ms()) <= 0:
                    return mask
                if on_wait is not None:
                    on_wait(mask)
                time.sleep_ms(_POLL_MS)
        finally:
            self._collect = False
            self.stop_scan()

    def _start_scan(self):
        # duration 0 = until stopped; window == interval = listen
        # constantly. Active, because the manufacturer data may arrive in
        # the scan response rather than the advertisement itself.
        self._ble.gap_scan(0, _SCAN_INTERVAL_US, _SCAN_INTERVAL_US, True)
        self._scanning = True

    def stop_scan(self):
        if self._scanning:
            self._scanning = False
            try:
                # gap_scan(None) is how MicroPython spells "stop now";
                # the stub types duration_ms as int and flags it.
                self._ble.gap_scan(None)  # type: ignore[arg-type]
            except OSError:
                pass

    # ── connecting ───────────────────────────────────────────────────────
    def gap_connect(self, device, addr_type, addr):
        self._connecting = device
        self._connect_failed = False
        self._ble.gap_connect(addr_type, addr)

    def connect_failed(self):
        return self._connect_failed

    # ── passthrough ──────────────────────────────────────────────────────
    def discover_services(self, conn):
        # Deliberately unfiltered. Passing a UUID here would filter on the
        # exact spelling, and the one the brick declares is not known in
        # advance; the result handler accepts either.
        self._ble.gattc_discover_services(conn)

    def discover_characteristics(self, conn, start, end):
        self._ble.gattc_discover_characteristics(conn, start, end)

    def discover_descriptors(self, conn, start, end):
        self._ble.gattc_discover_descriptors(conn, start, end)

    def exchange_mtu(self, conn):
        try:
            self._ble.gattc_exchange_mtu(conn)
        except (AttributeError, OSError):
            pass

    def write(self, conn, handle, payload, mode=0):
        self._ble.gattc_write(conn, handle, payload, mode)

    def disconnect(self, conn):
        try:
            self._ble.gap_disconnect(conn)
        except OSError:
            pass

    def close(self):
        self.stop_scan()
        for conn in list(self._by_handle):
            self.disconnect(conn)
        self._ble.active(False)


_central = None


def central():
    """The shared radio, started on first use."""
    global _central
    if _central is None:
        _central = _Central()
    return _central


def close_radio():
    """Drop every connection and power the BLE radio down."""
    global _central
    if _central is not None:
        _central.close()
        _central = None


class LegoDevice(object):
    """One connected brick. Subclasses add the commands it understands.

    State arrives as notifications and is unpacked by update(), on the
    main loop -- the radio callback only copies bytes into a ring,
    because allocating there is what turns a busy room into an
    "Unhandled exception in IRQ callback handler".

    Call update() every time round your loop. Nothing else refreshes the
    readings.
    """

    PRODUCT = -1

    def __init__(self, verbose=None):
        self._c = central()
        self.verbose = VERBOSE if verbose is None else verbose

        self.conn = None
        self.card_color = None
        self.card_serial = None
        self.product = None
        self.firmware = None
        self.max_packet = None

        self.battery = None             # percent
        self.usb = None                 # 0 unplugged, 1 plugged in
        self.button = 0                 # 1 while held

        self._svc = None                # type: tuple | None
        self._tx = None
        self._rx = None
        self._cccd = None
        self._mtu = 23
        self._step_done = False
        self._write_status = None

        self._slots = [bytearray(_RX_SLOT_LEN) for _ in range(_RX_SLOTS)]
        self._lens = [0] * _RX_SLOTS
        self._write_at = 0
        self._read_at = 0
        self._info = None

    # ── connecting ───────────────────────────────────────────────────────
    def connect(self, color, serial=None, timeout_ms=None, update_ms=None,
                on_wait=None):
        """Find this device type carrying the given card, and connect.

        color/serial are the App color code and the serial printed on the
        card -- exactly what read_card() gives you. Both are matched,
        because serials are allocated per color and repeat across them.

        Waits indefinitely by default: the brick is expected to be
        switched on and advertising, and "not yet" is not an error.
        """
        # -1 is scan_for()'s "any serial"; None means the same thing here.
        found = self._c.scan_for(self.PRODUCT, color,
                                 -1 if serial is None else serial,
                                 timeout_ms=timeout_ms, on_wait=on_wait)
        if found is None:
            return False
        addr_type, addr, product, card_color, card_serial = found
        self.product = product
        self.card_color = card_color
        self.card_serial = card_serial

        self._c.gap_connect(self, addr_type, addr)
        if not self._wait(lambda: self.conn is not None or
                          self._c.connect_failed(), _CONNECT_TIMEOUT_MS):
            raise LegoError('{}: no answer to connect'.format(self._label()))
        if self.conn is None:
            raise LegoError('{}: refused the connection'.format(self._label()))

        self._c.exchange_mtu(self.conn)

        self._discover()
        self._subscribe()
        self._read_info()
        self.set_update_rate(_DEFAULT_UPDATE_MS if update_ms is None
                            else update_ms)
        if self.verbose:
            print('{} connected, mtu {}'.format(self._label(), self._mtu))
        return True

    def _discover(self):
        self._step_done = False
        self._c.discover_services(self.conn)
        if not self._wait(lambda: self._step_done, _STEP_TIMEOUT_MS) or \
                self._svc is None:
            raise LegoError('{}: no FD02 service'.format(self._label()))

        start, end = self._svc
        self._step_done = False
        self._c.discover_characteristics(self.conn, start, end)
        if not self._wait(lambda: self._step_done, _STEP_TIMEOUT_MS):
            raise LegoError('{}: characteristic discovery stalled'
                            .format(self._label()))
        if self._tx is None or self._rx is None:
            raise LegoError('{}: FD02 service is missing its read or write '
                            'characteristic'.format(self._label()))

        if self.verbose:
            print('{}: service {}..{} tx {} rx {}'.format(
                self._label(), start, end, self._tx, self._rx))

        # The CCCD lives just after the notify characteristic's value, and
        # asking beats assuming -- a firmware that put a user-description
        # descriptor first would make _rx + 1 silently write to the wrong
        # handle.
        #
        # Searched across the whole service rather than from _rx + 1: the
        # end handle a brick reports for its service has been seen to stop
        # at the notify value, which leaves the CCCD outside any range
        # derived from it and finds nothing at all.
        self._step_done = False
        self._c.discover_descriptors(self.conn, start, end)
        self._wait(lambda: self._step_done, _STEP_TIMEOUT_MS)

        if self._cccd is None:
            # Every notify characteristic must have one, so not finding it
            # means the search could not see it, not that it is absent.
            # The conventional position is immediately after the value,
            # and subscribing there is worth trying -- if it is wrong the
            # info request below times out and says so.
            self._cccd = self._rx + 1
            print('{}: no CCCD found by discovery, assuming handle {}'
                  .format(self._label(), self._cccd))

    def _subscribe(self):
        # Written with a response, and the response is checked. A rejected
        # CCCD write is silent otherwise, and shows up two steps later as
        # a brick that never answers -- which sends you looking at the
        # wrong thing.
        self._write_status = None
        self._c.write(self.conn, self._cccd, b'\x01\x00', 1)
        if not self._wait(lambda: self._write_status is not None,
                          _STEP_TIMEOUT_MS):
            raise LegoError('{}: no answer subscribing at handle {}'
                            .format(self._label(), self._cccd))
        if self._write_status != 0:
            raise LegoError('{}: handle {} refused the subscribe (ATT error '
                            '{}) -- that is probably not its CCCD'
                            .format(self._label(), self._cccd,
                                    self._write_status))

    def _read_info(self):
        """Ask for version and product info, and wait for the answer.

        Doubles as the handshake that proves the link works end to end
        before any command is sent, and confirms the brick really is the
        product the advertisement claimed.
        """
        self._info = None
        self._send(struct.pack('<B', _INFO_REQUEST))

        def answered():
            # The reply arrives as a notification like everything else, so
            # it only lands once update() has drained the ring.
            self.update()
            return self._info is not None

        if not self._wait(answered, _STEP_TIMEOUT_MS):
            raise LegoError('{}: no reply to the info request'
                            .format(self._label()))
        if self.product is not None and self._info != self.product:
            raise LegoError('{}: connected to product {} instead'
                            .format(self._label(), self._info))

    def set_update_rate(self, delay_ms=_DEFAULT_UPDATE_MS):
        """How often the brick reports its state. 0 = off, else 15..1000."""
        if delay_ms != 0 and not (15 <= delay_ms <= 1000):
            raise ValueError('update rate must be 0 or 15..1000 ms')
        self._send(struct.pack('<BH', _DEVICE_NOTIFICATION_REQUEST, delay_ms))

    @property
    def connected(self):
        return self.conn is not None

    def disconnect(self):
        if self.conn is not None:
            self._c.disconnect(self.conn)
            # The disconnect event clears self.conn; do not wait for it,
            # since a brick that has already gone away never sends one.
            self.conn = None

    # ── plumbing ─────────────────────────────────────────────────────────
    def _label(self):
        name = PRODUCT_NAMES.get(self.PRODUCT, 'device')
        if self.card_color is None:
            return name
        return '{} {} #{}'.format(color_name(self.card_color), name,
                                  self.card_serial)

    def _wait(self, ready, timeout_ms):
        deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
        while not ready():
            if time.ticks_diff(deadline, time.ticks_ms()) <= 0:
                return False
            time.sleep_ms(_POLL_MS)
        return True

    def _on_connect(self, conn_handle):
        self.conn = conn_handle

    def _on_disconnect(self):
        self.conn = None
        self._svc = None
        self._tx = None
        self._rx = None
        self._cccd = None

    def _stash(self, notify_data):
        """Copy one notification into the ring. Runs in the BLE callback."""
        n = len(notify_data)
        if n > _RX_SLOT_LEN:
            n = _RX_SLOT_LEN
        nxt = self._write_at + 1
        if nxt == _RX_SLOTS:
            nxt = 0
        if nxt == self._read_at:
            return                      # full; drop it, another is coming
        slot = self._slots[self._write_at]
        for k in range(n):
            slot[k] = notify_data[k]
        self._lens[self._write_at] = n
        self._write_at = nxt

    def _send(self, payload):
        if self.conn is None or self._tx is None:
            raise LegoError('{}: not connected'.format(self._label()))
        try:
            self._c.write(self.conn, self._tx, payload, 0)
        except OSError:
            # Write-without-response has no flow control, so a burst can
            # fill the controller's buffer. One retry after a breath is
            # enough in practice; dropping the command beats raising into
            # a drive loop.
            time.sleep_ms(20)
            try:
                self._c.write(self.conn, self._tx, payload, 0)
            except OSError as exc:
                if self.verbose:
                    print('{}: write dropped ({})'.format(self._label(), exc))

    # ── incoming ─────────────────────────────────────────────────────────
    def update(self):
        """Unpack everything the brick has reported since the last call.

        Returns True if anything was processed. Cheap enough to call every
        time round a drive loop, which is where it belongs -- readings do
        not refresh on their own.
        """
        got = False
        while self._read_at != self._write_at:
            slot = self._slots[self._read_at]
            length = self._lens[self._read_at]
            self._read_at = (self._read_at + 1) % _RX_SLOTS
            try:
                self._handle(memoryview(slot)[:length])
            except (ValueError, IndexError) as exc:
                if self.verbose:
                    print('{}: bad packet ({})'.format(self._label(), exc))
            got = True
        return got

    def _handle(self, packet):
        if len(packet) < 1:
            return
        kind = packet[0]
        if kind == _DEVICE_NOTIFICATION:
            if len(packet) < 3:
                return
            length = packet[1] | (packet[2] << 8)
            body = packet[3:]
            if length > len(body):
                # Truncated -- almost always the MTU. Take what arrived;
                # the walk below stops when it runs out.
                length = len(body)
            self._walk(body, length)
        elif kind == _INFO_RESPONSE:
            values = struct.unpack_from(_INFO_RESPONSE_FORMAT, packet, 1)
            self.firmware = (values[3], values[4], values[5])
            self.max_packet = values[9]
            self._info = values[10]
        elif kind == _DEVICE_NOTIFICATION_RESPONSE:
            pass                        # plain ack, nothing to record
        elif self.verbose:
            print('{}: unhandled message {}'.format(self._label(), kind))

    def _walk(self, body, length):
        """Split one DeviceNotification into its sub-notifications.

        They are packed back to back as [type][fixed body] with no length
        of their own, so an unrecognised type ends the walk: carrying on
        past it would read the next item at the wrong offset and produce
        confident nonsense rather than an error.
        """
        at = 0
        while at < length:
            kind = body[at]
            size = _N_SIZE.get(kind)
            if size is None or at + 1 + size > length:
                if self.verbose and size is None:
                    print('{}: unknown notification {}'
                          .format(self._label(), kind))
                return
            self._notified(kind, body, at + 1)
            at += 1 + size

    def _notified(self, kind, body, at):
        """Record one sub-notification. Subclasses extend this."""
        if kind == _N_INFO:
            self.battery, self.usb = struct.unpack_from(_N_FORMAT[kind],
                                                        body, at)
        elif kind == _N_BUTTON:
            self.button = body[at]
        elif kind == _N_CARD:
            color, serial = struct.unpack_from(_N_FORMAT[kind], body, at)
            app = firmware_to_app(color)
            if app is not None:
                self.card_color = app
                self.card_serial = serial


class Controller(LegoDevice):
    """Two joysticks.

    left_percent / right_percent are -100..100 with the stick's own
    deadzone already applied, which makes them drop straight into a motor
    speed. left_angle / right_angle are the raw stick deflection in
    degrees, if you want a different curve.
    """

    PRODUCT = CONTROLLER

    def __init__(self, verbose=None):
        LegoDevice.__init__(self, verbose)
        self.left_percent = 0
        self.right_percent = 0
        self.left_angle = 0
        self.right_angle = 0

    def _notified(self, kind, body, at):
        if kind == _N_CONTROLLER:
            (self.left_percent, self.right_percent,
             self.left_angle, self.right_angle) = struct.unpack_from(
                _N_FORMAT[kind], body, at)
        else:
            LegoDevice._notified(self, kind, body, at)


class ColorSensor(LegoDevice):
    """Color, reflected light and the raw channels behind them.

    color is an App color code (0 = nothing in view). reflection is
    0..255 -- the continuous brightness reading, and the only one of
    these a connectionless broadcast cannot give you.
    """

    PRODUCT = COLOR_SENSOR

    def __init__(self, verbose=None):
        LegoDevice.__init__(self, verbose)
        self.color = 0
        self.reflection = 0
        self.red = 0
        self.green = 0
        self.blue = 0
        self.hue = 0
        self.saturation = 0
        self.value = 0

    def _notified(self, kind, body, at):
        if kind == _N_COLOR:
            (color, self.reflection, self.red, self.green, self.blue,
             self.hue, self.saturation, self.value) = struct.unpack_from(
                _N_FORMAT[kind], body, at)
            # The sensor reports firmware codes here too, and -1 for
            # "nothing in view".
            self.color = firmware_to_app(color) or NOCOLOR
        else:
            LegoDevice._notified(self, kind, body, at)


class _MotorBase(LegoDevice):
    """Shared motor commands, addressed by bit mask."""

    def __init__(self, verbose=None):
        LegoDevice.__init__(self, verbose)
        self.speed = 0                  # as last reported by the brick
        self.position = 0
        self.power = 0
        self._running = 0               # bit mask of motors told to run

    def _notified(self, kind, body, at):
        if kind == _N_MOTOR:
            (mask, state, absolute, power, speed, position,
             gesture) = struct.unpack_from(_N_FORMAT[kind], body, at)
            self.speed = speed
            self.position = position
            self.power = power
        else:
            LegoDevice._notified(self, kind, body, at)

    def _set_speed(self, mask, speed):
        speed = int(speed)
        if speed < -100:
            speed = -100
        elif speed > 100:
            speed = 100
        self._send(struct.pack('<BBb', _MOTOR_SET_SPEED_COMMAND, mask, speed))
        return speed

    def _run(self, mask, direction=CLOCKWISE):
        self._send(struct.pack('<BBB', _MOTOR_RUN_COMMAND, mask, direction))
        self._running |= mask

    def _stop(self, mask):
        self._send(struct.pack('<BB', _MOTOR_STOP_COMMAND, mask))
        self._running &= ~mask


class SingleMotor(_MotorBase):
    """One motor.

    set_speed() is the whole interface for a "keep turning at this speed"
    loop: setting a speed on a stopped motor does nothing visible, so the
    first call also sends the run command, and a speed of 0 stops it
    rather than leaving it running at zero.
    """

    PRODUCT = SINGLE_MOTOR

    def set_speed(self, speed, direction=CLOCKWISE):
        speed = self._set_speed(MOTOR_LEFT, speed)
        if speed == 0:
            if self._running:
                self._stop(MOTOR_LEFT)
        elif not self._running:
            self._run(MOTOR_LEFT, direction)
        return speed

    def run(self, direction=CLOCKWISE):
        self._run(MOTOR_LEFT, direction)

    def stop(self):
        self._stop(MOTOR_LEFT)


class DoubleMotor(_MotorBase):
    """Left and right motors in one brick.

    tank() is the one to drive with: it sets both wheels in a single
    command, so they change together instead of one lurching a packet
    ahead of the other.
    """

    PRODUCT = DOUBLE_MOTOR

    def tank(self, left, right):
        """Drive left and right wheels at -100..100 each."""
        left = int(left)
        right = int(right)
        if left < -100:
            left = -100
        elif left > 100:
            left = 100
        if right < -100:
            right = -100
        elif right > 100:
            right = 100
        self._send(struct.pack('<Bbb', _MOVEMENT_MOVE_TANK_COMMAND,
                               left, right))
        return left, right

    def set_speed(self, left, right=None):
        """Set each motor's speed for the individual run commands."""
        if right is None:
            right = left
        return (self._set_speed(MOTOR_LEFT, left),
                self._set_speed(MOTOR_RIGHT, right))

    def run(self, direction=CLOCKWISE):
        self._run(MOTOR_BOTH, direction)

    def stop(self):
        self._send(struct.pack('<B', _MOVEMENT_STOP_COMMAND))
        self._stop(MOTOR_BOTH)


# ═════════════════════════════════════════════════════════════════════════
#   The screen and the speaker
# ═════════════════════════════════════════════════════════════════════════
# 135x240 portrait. The card's own color fills the screen, so which card
# is in use is readable across a room; the lines below it change without
# repainting the background.

GRAY = 0x8410
SCREEN_RED = 0xF800

#: Each card color as RGB565. Values follow LEGO's own color map where it
#: has one.
CARD_RGB565 = {
    RED: 0xD8C4,
    YELLOW: 0xFEA0,
    BLUE: 0x0377,
    TEAL: 0x05B6,
    GREEN: 0x6546,
    PURPLE: 0x4972,
    WHITE_CARD: 0xFFFF,
    MAGENTA: 0xE2D3,
    ORANGE: 0xF3E4,
    AZURE: 0x7DFD,
}

#: Yellow and white backgrounds need dark text to stay readable.
_DARK_TEXT_ON = (YELLOW, WHITE_CARD)

# Row positions, top to bottom.
_Y_TITLE = 6
_Y_COLOR = 22
_Y_SERIAL = 52
_Y_BRICK1 = 88
_Y_BRICK2 = 108
_Y_BIG1 = 138
_Y_BIG2 = 172
_Y_HINT = 216

BEEP_OK = (1760, 120)       # short and high: a card was read
BEEP_BAD = (220, 250)       # low buzz: not a card we can use
BEEP_GO = ((1568, 90), (2093, 140))     # two rising notes: bricks connected


class UI(object):
    """The Stick's screen and speaker, in one place.

    The speaker is optional on purpose. Constructing a Speaker has been
    seen to raise ENODEV on this board's codec, and losing the beeps is a
    far better outcome than a program that will not start.
    """

    def __init__(self, volume=70):
        self.display = Display()
        self.background = BLACK
        self.text = WHITE
        try:
            self.speaker = Speaker(volume=volume)
        except OSError as exc:
            print('m5_wand: no audio ({})'.format(exc))
            self.speaker = None

    # ── drawing ──────────────────────────────────────────────────────────
    def line(self, y, text, color=None, scale=1, spacing=2):
        """Draw one line centered, blanking the rest of that line first.

        Truncated to what fits rather than left to the library's
        clipping, which trims evenly off both ends -- for a label you
        would rather read the start of it than the middle.
        """
        if color is None:
            color = self.text
        wide = self.display.max_chars(scale, spacing)
        text = str(text).upper()[:wide]
        self.display.draw_text(0, y, ' ' * wide, color, self.background,
                               scale=scale, spacing=spacing)
        self.display.draw_text_centered(y, text, color, self.background,
                                        scale=scale, spacing=spacing)

    def looking(self, message='TAP A CARD', hint='purple or green'):
        self.background = BLACK
        self.text = WHITE
        self.display.fill(BLACK)
        self.line(_Y_COLOR, message, WHITE, scale=2, spacing=0)
        self.line(_Y_SERIAL + 10, hint, GRAY)

    def card(self, color, serial, title='', beep=True):
        """Fill the screen with the card's own color and name it."""
        self.background = CARD_RGB565.get(color, BLACK)
        self.text = BLACK if color in _DARK_TEXT_ON else WHITE
        self.display.fill(self.background)
        self.line(_Y_TITLE, title, GRAY)
        self.line(_Y_COLOR, color_name(color), scale=2, spacing=0)
        self.line(_Y_SERIAL, '#{}'.format(serial), scale=2, spacing=0)
        if beep:
            self.beep()

    def brick(self, slot, message):
        """One of the two brick-status lines, 0 on top."""
        self.line(_Y_BRICK1 if slot == 0 else _Y_BRICK2, message)

    def big(self, slot, message):
        """One of the two large live-readout lines, 0 on top."""
        self.line(_Y_BIG1 if slot == 0 else _Y_BIG2, message,
                  scale=2, spacing=0)

    def hint(self, message):
        self.line(_Y_HINT, message, GRAY)

    def problem(self, headline, detail='', beep=True):
        """Something is wrong, on black so it cannot be missed."""
        self.background = BLACK
        self.text = WHITE
        self.display.fill(BLACK)
        self.line(_Y_COLOR, headline, SCREEN_RED, scale=2, spacing=0)
        if detail:
            self.line(_Y_SERIAL + 10, detail, GRAY)
        if beep:
            self.buzz()

    # ── sound ────────────────────────────────────────────────────────────
    def beep(self):
        if self.speaker is not None:
            self.speaker.tone(*BEEP_OK)

    def buzz(self):
        if self.speaker is not None:
            self.speaker.tone(*BEEP_BAD)

    def go(self):
        """Two rising notes: both bricks are connected and it is live."""
        if self.speaker is not None:
            for freq, ms in BEEP_GO:
                self.speaker.tone(freq, ms)

    def close(self):
        if self.speaker is not None:
            self.speaker.deinit()


# ═════════════════════════════════════════════════════════════════════════
#   The program
# ═════════════════════════════════════════════════════════════════════════
# What a tap does is decided by *what answers to the card*, not by the
# card's color. Tap any card, and whichever sender and motor are
# advertising under it get wired together:
#
#     Controller   + Double Motor   one stick per wheel
#     Controller   + Single Motor   the left stick drives it
#     Color Sensor + Double Motor   reflected light drives both wheels
#     Color Sensor + Single Motor   reflected light drives it
#
# An earlier version mapped color -> behavior in a table here, with
# purple driving and green following the light. Tapping any other color
# printed "no bricks are wired to ORANGE" -- which was about the table,
# not about the room, and read as a claim that the bricks were not there
# when they were sitting right in front of you blinking. Asking the air
# cannot be wrong in that way, and it means a card of any color works
# the moment its bricks are switched on.

#: Preference order when a card has more than one of a kind. First match
#: wins, so a card carrying both a controller and a color sensor is
#: driven by the controller.
SENDERS = ((CONTROLLER, Controller), (COLOR_SENSOR, ColorSensor))
ACTUATORS = ((DOUBLE_MOTOR, DoubleMotor), (SINGLE_MOTOR, SingleMotor))

#: What each brick is called on its status line. Kept short because the
#: line holds thirteen characters and the state marker goes on the end.
SHORT_NAMES = {
    CONTROLLER: 'CONTROLLER',
    DOUBLE_MOTOR: 'MOTORS',
    COLOR_SENSOR: 'SENSOR',
    SINGLE_MOTOR: 'MOTOR',
}

LOOP_MS = 50                # how often a running pair reads and acts
CARD_POLL_MS = 600          # how often it checks for a new card tap

#: Resend even an unchanged command this often. The bricks hold the last
#: command indefinitely, so this is not a keepalive they need -- it is
#: cover for the one packet in a while that a write-without-response
#: quietly loses.
REFRESH_MS = 500

#: Below this the wheels are not worth waking up. The stick has its own
#: deadzone and rests at exactly 0, so this only catches drift.
STICK_DEADZONE = 5

#: Reflection is 0..255. Anything under the floor counts as dark and
#: stops the motor, so a hand over the sensor is a brake rather than a
#: crawl.
REFLECTION_FLOOR = 20
MAX_LIGHT_SPEED = 100


class Cancelled(Exception):
    """BtnA was pressed, or another card was tapped, while waiting."""


def product_bit(product):
    """This product's bit in the mask discover() returns."""
    return 1 << (product - SINGLE_MOTOR)


def pick(mask, table):
    """The first class in `table` whose product is in `mask`, or None."""
    for product, cls in table:
        if mask & product_bit(product):
            return cls
    return None


def light_to_speed(reflection):
    """Reflection (0..255) -> motor speed (0..MAX_LIGHT_SPEED).

    The floor is subtracted rather than clamped, so the speed ramps up
    from a standstill at the threshold instead of jumping to a fifth of
    full power the moment the sensor sees anything.
    """
    if reflection <= REFLECTION_FLOOR:
        return 0
    span = 255 - REFLECTION_FLOOR
    return (reflection - REFLECTION_FLOOR) * MAX_LIGHT_SPEED // span


def sender_speeds(sender):
    """(left, right) in -100..100, from whichever kind of sender it is.

    A controller gives a speed per side; a color sensor gives one
    reading, which both sides share so it drives a Double Motor straight
    ahead.
    """
    if isinstance(sender, Controller):
        left = sender.left_percent
        right = sender.right_percent
        if -STICK_DEADZONE < left < STICK_DEADZONE:
            left = 0
        if -STICK_DEADZONE < right < STICK_DEADZONE:
            right = 0
        return left, right
    speed = light_to_speed(sender.reflection)
    return speed, speed


def apply_speeds(actuator, left, right):
    """Send the speeds in whichever command this motor brick takes."""
    if isinstance(actuator, DoubleMotor):
        actuator.tank(left, right)
    else:
        # One motor, so the left-hand value drives it -- the left stick
        # for a controller, and the single light reading either way.
        actuator.set_speed(left)


def readout(sender, actuator, left, right):
    """The two big on-screen lines for this pair.

    Both halves matter. A single motor has no right-hand wheel, so
    showing an R line for it would be reporting a number that goes
    nowhere.
    """
    if isinstance(sender, Controller):
        if isinstance(actuator, DoubleMotor):
            return 'L {:>4}'.format(left), 'R {:>4}'.format(right)
        return 'STK{:>4}'.format(left), 'SPD{:>4}'.format(left)
    return 'LIT{:>4}'.format(sender.reflection), 'SPD{:>4}'.format(left)


def poll_card(rfid):
    """(color, serial) for the card on the reader, or None.

    Swallows both failure modes on purpose. A read that did not complete
    says nothing about the card -- the driver already retried it three
    times, and the next pass round the loop tries again -- and a tag that
    is not a LEGO card is a thing people will hold up, not an error worth
    stopping for.

    Always halts the tag, so taking a card off and putting it back reads
    as two taps rather than one long one.
    """
    try:
        found = read_card(rfid)
    except (NotALegoCard, ReadError):
        rfid.halt()
        return None
    if found is None:
        return None
    rfid.halt()
    _uid, color, serial = found
    return color, serial


class Watcher(object):
    """Watches for a reason to leave the pair that is running.

    Two of them: BtnA, and a different card. Both are checked on the same
    slow tick, because polling the reader is not free and doing it every
    50 ms would visibly stutter the drive loop.
    """

    def __init__(self, buttons, rfid, current):
        self.buttons = buttons
        self.rfid = rfid
        self.current = current          # the (color, serial) in use
        self.next_card = None           # a different card, once tapped
        self._was_down = buttons.a.is_pressed()
        self._checked_at = time.ticks_ms()

    def should_stop(self):
        down = self.buttons.a.is_pressed()
        pressed = down and not self._was_down
        self._was_down = down
        if pressed:
            return True

        if time.ticks_diff(time.ticks_ms(), self._checked_at) < CARD_POLL_MS:
            return False
        self._checked_at = time.ticks_ms()

        card = poll_card(self.rfid)
        if card is None or card == self.current:
            # The same card left sitting on the reader is not a new tap.
            return False
        self.next_card = card
        return True

    def raise_if_stopping(self):
        """should_stop(), for the places that can only bail out by raising."""
        if self.should_stop():
            raise Cancelled()


def find_pair(ui, color, serial, watcher):
    """(sender_cls, actuator_cls) once both are on the air under this card.

    Waits indefinitely, showing what it has heard so far, so switching a
    brick on after the tap is all it takes. The only ways out are BtnA
    and another card, both of which arrive as Cancelled.
    """
    shown = [None]

    def ready(mask):
        return bool(pick(mask, SENDERS)) and bool(pick(mask, ACTUATORS))

    def waiting(mask):
        watcher.raise_if_stopping()
        if mask != shown[0]:
            # Name what is missing rather than what is present: the
            # useful half of "nothing is happening" is which brick to go
            # and switch on.
            sender = pick(mask, SENDERS)
            actuator = pick(mask, ACTUATORS)
            ui.brick(0, SHORT_NAMES[sender.PRODUCT] if sender else 'NO SENDER')
            ui.brick(1, SHORT_NAMES[actuator.PRODUCT] if actuator
                     else 'NO MOTOR')
            shown[0] = mask

    ui.brick(0, 'LISTENING...')
    ui.brick(1, '')
    print('listening for bricks on {} #{}...'.format(color_name(color), serial))
    mask = central().discover(color, serial, ready=ready, on_wait=waiting)
    sender = pick(mask, SENDERS)
    actuator = pick(mask, ACTUATORS)
    if sender is None or actuator is None:
        # Unreachable: discover() was given no timeout, so it only
        # returns once ready() has seen both halves. Checked anyway so
        # the attribute reads below cannot be the thing that explains a
        # crash that started somewhere else.
        raise LegoError('discover() returned without a full pair')
    print('  found {} + {}'.format(PRODUCT_NAMES[sender.PRODUCT],
                                   PRODUCT_NAMES[actuator.PRODUCT]))
    return sender, actuator


def connect_brick(ui, cls, slot, color, serial, watcher):
    """Connect one brick, saying so on screen until it turns up."""
    device = cls()
    name = PRODUCT_NAMES[cls.PRODUCT]
    # Thirteen characters fit on this line, and "CONTROLLER OK" is
    # exactly thirteen -- hence the one-character marker rather than "...".
    short = SHORT_NAMES[cls.PRODUCT]
    print('connecting to the {}...'.format(name))
    ui.brick(slot, '{} ?'.format(short))

    def waiting():
        watcher.raise_if_stopping()

    device.connect(color, serial, on_wait=waiting)
    ui.brick(slot, '{} OK'.format(short))
    print('  connected to the {}'.format(name))
    return device


def shut_down(devices):
    """Stop and disconnect everything, whatever state it got to."""
    for device in devices:
        if device is None:
            continue
        try:
            if device.connected and hasattr(device, 'stop'):
                device.stop()
        except Exception as exc:
            print('  stop failed: {}'.format(exc))
        try:
            device.disconnect()
        except Exception as exc:
            print('  disconnect failed: {}'.format(exc))


def wait_for_card(ui, rfid):
    """Block until a card is tapped."""
    ui.looking()
    print('tap a card')
    while True:
        card = poll_card(rfid)
        if card is not None:
            return card
        time.sleep_ms(150)

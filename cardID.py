'''
Tap cards, hear what they are, and log every one with its FD02 broadcast.

  >>> Runs ON the M5StickS3, not the Mac. <<<
  Needs on the Stick: m5/ (2026-08 or later), lego_card.py, stick_ui.py,
  stick_menu.py

This is the card-harvesting tool. Every new card you tap goes into
card_taps.csv on the Stick as one row: the card's RFID UID, its color and
serial, and all twelve bytes of the FD02 service data a device carrying it
broadcasts. That is the pairing needed to attack the open question in
../CLAUDE.md -- what bytes 2 and 7 are a function of.

── How to tap ────────────────────────────────────────────────────────────
Two taps per card, in this order:

    1. tap the card on a CONTROLLER or COLOR SENSOR, switched on and nearby
    2. tap the same card on the Stick's RFID reader

Step 1 first, and it is not optional. b2/b7 are not stored on the card, so
the RFID read alone gets you color and serial and nothing else -- the
tokens only exist in what a sender broadcasts. Tapping the sender first
means it is already broadcasting that card by the time the Stick sees it,
so the answer comes back the instant you tap.

Every tap answers immediately. The Stick never waits for a beacon to turn
up, so you can go straight down a stack of cards at whatever pace you like.

The screen fills with the card's own color and names it, and:

    one high beep      the card was read
    two rising notes   the row went into the log
    a low buzz         not a LEGO card, or no beacon on the air for it

If you get the buzz with NO BEACON, step 1 did not happen for that card (or
the sender is out of range or asleep). Tap it on the sender and tap it here
again -- nothing was written, so there is nothing to undo.

── The file ──────────────────────────────────────────────────────────────
card_taps.csv on the Stick, appended to and closed after every row so
pulling the power never costs more than the card in your hand. Fetch it
with:

    python3 mac_fetch_cards.py

Cards already in the file are recognized on the next boot and are not
logged twice -- the counter on screen picks up where it left off. Only
complete rows are written: a card with no beacon is not recorded at all,
so a UID in the file always comes with its twelve bytes.

── Running from the menu ───────────────────────────────────────────────
main(buttons=None) works standalone (builds its own Buttons(), BtnA
just ends the run) or launched from main.py's menu (buttons passed in,
BtnA/BtnB return 'stop'/'toggle' to the caller instead of exiting the
process). This file used to *be* main.py; it moved here so main.py
could become the menu between this and rcx.py -- nothing about the
logic below changed, only the stop mechanism (was Ctrl-C only; is now
BtnA, checked once per poll of the RFID reader) and the entry point
(was a bare call at the bottom; is now behind main()/__main__).
'''

import time

import bluetooth

import lego_card
import stick_ui
import stick_menu
from m5.m5_buttons import Buttons
from m5.m5_rfid import RFID, ReadError

#: Shown pinned to the bottom of the screen so it's clear which of the
#: menu's two programs is running -- see stick_ui.UI.tag().
TAG = 'ID'

LOG_PATH = 'card_taps.csv'

COLUMNS = ('n,uid,color,color_app,serial,'
           'b0,b1,b2,b3,b4,b5,b6,b7,b8,b9,b10,b11,'
           'payload,rssi,uptime_ms')

FD02 = 0xFD02
SERVICE_DATA_16 = 0x16          # AD type: Service Data - 16-bit UUID
PAYLOAD_LEN = 12

#: A beacon older than this is not trusted to still describe the card in
#: your hand -- a sender that has since been re-tapped would be stale.
STALE_MS = 30000

#: How many beacons can pile up between passes of the main loop. The slots
#: are allocated once, up front -- see the note on the callback below.
QUEUE_SLOTS = 32

POLL_MS = 120


# ── BLE listening ─────────────────────────────────────────────────────────

_IRQ_SCAN_RESULT = 5
_IRQ_SCAN_DONE = 6


def find_fd02(adv):
    '''Offset of a full FD02 service data payload in adv, or -1.

    Returns a bare integer and allocates nothing -- not a tuple, not a slice.
    It runs inside the BLE callback, and anything that touches the heap there
    fails with "Unhandled exception in IRQ callback handler" (seen live).
    That is also why the payload length is checked here rather than returned.

    An advertisement is a chain of length-prefixed AD structures. A malformed
    one ends the walk rather than raising: anything in the room can be
    broadcasting, and none of it is under our control.
    '''
    i = 0
    end = len(adv)
    while i + 1 < end:
        length = adv[i]
        if length == 0 or i + 1 + length > end:
            break
        if (adv[i + 1] == SERVICE_DATA_16 and length >= 3 + PAYLOAD_LEN
                and adv[i + 2] | (adv[i + 3] << 8) == FD02):
            return i + 4
        i += 1 + length
    return -1


class Listener(object):
    '''Every FD02 beacon in range, kept by the card it is broadcasting.

    Passive: nothing is connected and nothing is transmitted, so this does
    not disturb the devices it listens to or use up a connection slot.

    The radio hands packets over on a callback that cannot allocate, so they
    land in a ring of buffers made once in __init__ and are unpacked later by
    drain(), on the main loop, where allocating is fine. If the ring fills the
    callback drops packets -- a sender broadcasts many times a second, so the
    next one is along in a moment.
    '''

    def __init__(self):
        self.heard = {}                 # (app_color, serial) -> (payload, rssi, t)
        self.seen = 0                   # every advertisement, LEGO or not
        self._slots = [bytearray(PAYLOAD_LEN) for _ in range(QUEUE_SLOTS)]
        self._rssi = [0] * QUEUE_SLOTS
        self._write = 0
        self._read = 0
        self._restart = False
        self._ble = bluetooth.BLE()
        self._ble.active(True)
        self._ble.irq(self._on_event)
        self._start_scan()

    def _start_scan(self):
        # duration 0 = until stopped; window == interval = listen constantly,
        # which matters because a beacon we miss is a card the user has to
        # tap again.
        self._ble.gap_scan(0, 30000, 30000, True)

    def _on_event(self, event, data):
        if event == _IRQ_SCAN_DONE:
            self._restart = True        # gap_scan() is not safe from here
            return
        if event != _IRQ_SCAN_RESULT:
            return
        # Counted for every advertisement in the room, so a card that will not
        # log can be told apart from a radio that is not hearing anything.
        # Masked to stay a small integer, which costs no allocation.
        self.seen = (self.seen + 1) & 0xFFFF
        offset = find_fd02(data[4])
        if offset < 0:
            return
        nxt = self._write + 1
        if nxt == QUEUE_SLOTS:
            nxt = 0
        if nxt == self._read:
            return                      # ring full; drop this one
        # data[4] is only valid inside this callback, so copy it out now,
        # a byte at a time -- a slice would allocate.
        adv = data[4]
        slot = self._slots[self._write]
        for k in range(PAYLOAD_LEN):
            slot[k] = adv[offset + k]
        self._rssi[self._write] = data[3]
        self._write = nxt

    def drain(self):
        '''Move everything the radio heard into the by-card table.'''
        if self._restart:
            self._restart = False
            self._start_scan()
        now = time.ticks_ms()
        while self._read != self._write:
            slot = self._slots[self._read]
            rssi = self._rssi[self._read]
            color = lego_card.firmware_to_app(slot[1])
            if color is not None:       # otherwise not a card color, not ours
                serial = slot[3] | (slot[4] << 8)
                self.heard[(color, serial)] = (bytes(slot), rssi, now)
            self._read = (self._read + 1) % QUEUE_SLOTS

    def beacon_for(self, color, serial):
        '''(payload, rssi) most recently heard for this card, or None.'''
        entry = self.heard.get((color, serial))
        if entry is None:
            return None
        payload, rssi, when = entry
        if time.ticks_diff(time.ticks_ms(), when) > STALE_MS:
            return None
        return payload, rssi

    def close(self):
        try:
            self._ble.gap_scan(None)
        finally:
            self._ble.active(False)


# ── the log file ──────────────────────────────────────────────────────────

def as_hex(data):
    return ''.join('{:02X}'.format(b) for b in data)


def load_log(path):
    '''(uids already logged, number of rows) -- so a reboot does not duplicate.

    A file we cannot parse is treated as empty rather than being overwritten:
    append-only means the worst case is a few repeated rows, and losing a
    morning of tapping to a parse bug would be far worse.
    '''
    uids = set()
    rows = 0
    try:
        handle = open(path, 'r')
    except OSError:
        return uids, rows
    try:
        for line in handle:
            fields = line.strip().split(',')
            if len(fields) < 2 or fields[0] == 'n':
                continue
            uids.add(fields[1])
            rows += 1
    finally:
        handle.close()
    return uids, rows


def append_row(path, row):
    '''Add one row, closing the file again so a power cut cannot lose it.'''
    new = not row_file_exists(path)
    handle = open(path, 'a')
    try:
        if new:
            handle.write(COLUMNS + '\n')
        handle.write(row + '\n')
    finally:
        handle.close()


def row_file_exists(path):
    try:
        open(path, 'r').close()
        return True
    except OSError:
        return False


def build_row(n, uid, color, serial, payload, rssi):
    fields = [str(n), as_hex(uid), stick_ui.color_name(color), str(color),
              str(serial)]
    fields += [str(b) for b in payload]
    fields += [as_hex(payload), str(rssi), str(time.ticks_ms())]
    return ','.join(fields)


# ── one tap ───────────────────────────────────────────────────────────────

def log_card(uid, color, serial, listener, ui, state):
    '''Show the card, find its beacon, and add a row if both are in hand.

    Answers immediately either way. There is deliberately no waiting for a
    beacon to turn up: the beacon has to already be on the air, because you
    tap the sender first. Waiting here blocked the whole main loop, so the
    next card you tapped was ignored until the wait ran out -- which looked
    like the reader being slow to identify a card rather than the previous
    card still timing out.
    '''
    key = as_hex(uid)
    print()
    print('{} #{}  uid {}'.format(stick_ui.color_name(color), serial, key))
    ui.card(color, serial)
    ui.tag(TAG)

    if key in state['uids']:
        print('  already in {}'.format(LOG_PATH))
        ui.status('ALREADY HAVE')
        ui.note('{} LOGGED'.format(state['rows']))
        return

    listener.drain()
    found = listener.beacon_for(color, serial)
    if found is None:
        # Nothing is written on purpose: a UID with no tokens is not the
        # pairing this file exists to collect, and a half-row would read
        # like data later.
        print('  no FD02 beacon -- tap this card on a controller or color '
              'sensor, then tap it here again')
        # Which of the two things went wrong. Cards heard but not this one
        # means the sender is not carrying it; nothing heard at all with
        # advertisements arriving means no LEGO sender is switched on; no
        # advertisements at all means the radio, not the cards.
        print('  radio: {} advertisement(s) seen, cards on the air: {}'.format(
            listener.seen,
            ', '.join('{} #{}'.format(stick_ui.color_name(c), s)
                      for c, s in listener.heard) or 'none'))
        ui.status('NO BEACON')
        ui.note('TAP ON SENDER')
        ui.buzz()
        return

    payload, rssi = found
    state['rows'] += 1
    row = build_row(state['rows'], uid, color, serial, payload, rssi)
    append_row(LOG_PATH, row)
    state['uids'].add(key)
    print('  {}'.format(row))
    ui.status('SAVED')
    ui.note('{} LOGGED'.format(state['rows']))
    ui.saved()


# ── main ──────────────────────────────────────────────────────────────────

def main(buttons=None):
    '''Tap cards until BtnA/BtnB ends the run.

    Returns 'stop' or 'toggle', same convention as RCXWand.main() -- see
    stick_menu.py. buttons=None builds its own Buttons(), for
    standalone use.
    '''
    buttons = buttons or Buttons()
    poll = stick_menu.watch(buttons)

    ui = stick_ui.UI()
    # 'TAP CARDS' (9 chars) and 'logging to csv' (14) both overran this
    # screen's character budget (8 / 13) and got cut off mid-word.
    ui.looking('TAP CARD', 'SAVES TO CSV')
    ui.tag(TAG)

    uids, rows = load_log(LOG_PATH)
    state = {'uids': uids, 'rows': rows}
    print('{}: {} card(s) already logged'.format(LOG_PATH, rows))
    if rows:
        ui.note('{} LOGGED'.format(rows))

    listener = Listener()
    rfid = RFID()
    last_uid = None
    reason = 'stop'

    try:
        while True:
            poll()
            listener.drain()

            uid = rfid.read_uid()
            if uid is None:
                # Field empty, so bringing the same card back counts as a
                # fresh tap. The screen keeps showing the last card read.
                last_uid = None
                time.sleep_ms(POLL_MS)
                continue

            if uid == last_uid:
                rfid.halt()
                time.sleep_ms(POLL_MS)
                continue
            last_uid = uid

            try:
                color, serial = lego_card.read_card_data(rfid)
            except ReadError as e:
                # Says nothing about the card, and the driver already retried
                # it three times -- forget the UID so the next pass reads it
                # again instead of skipping it as a repeat.
                print('read failed ({}), will retry'.format(e))
                last_uid = None
                rfid.halt()
                time.sleep_ms(POLL_MS)
                continue
            except lego_card.NotALegoCard as e:
                print('not a LEGO card: {}'.format(e))
                # 'NOT A CARD' (10 chars) overran the 8-char headline
                # budget; the [:20] detail slice was already looser
                # than the real 13-char line budget it draws into.
                ui.problem('BAD CARD', str(e)[:13])
                ui.tag(TAG)
                rfid.halt()
                time.sleep_ms(POLL_MS)
                continue

            rfid.halt()
            log_card(uid, color, serial, listener, ui, state)
    except stick_menu.Stop:
        print('stopped')
        reason = 'stop'
    except stick_menu.Toggle:
        print('toggling')
        reason = 'toggle'
    finally:
        listener.close()
        ui.close()
    return reason


if __name__ == '__main__':
    main()

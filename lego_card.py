'''
lego_card — read a LEGO Education connection card with an RFID reader.

  >>> read_card() runs ON the M5StickS3 (needs m5/ and the RFID2 Unit).
      decode_pages() is pure and can be checked anywhere. <<<

The cards are NTAG/Ultralight tags (SAK 0x00, 7-byte UID). The color and
serial are stored in the clear from page 4:

    page 4   4C 33 47 30                    ASCII "L3G0", a magic marker
    page 5   00 <color> <serial hi> <lo>    color is the FIRMWARE code,
                                            serial is big-endian
    page 6   00 00 00 00
    page 7   FF EE DD CC                    fixed filler

Read from a purple #6055 card:

    page 4  4C334730 000217A7 00000000 FFEEDDCC
                     ^^ ^^^^-- 0x17A7 = 6055
                     +-------- 0x02 = firmware PURPLE

Note the serial is big-endian here, while the FD02 broadcast carries it
little-endian. Do not copy one into the other without swapping.

── The b2/b7 tokens a motor validates ────────────────────────────────────
Not stored on the card -- confirmed by dumping every page, not just 4-7:
pages 8 through 19 read as zeros and nothing anywhere looks like them. But
they are *computable* from the card, because they are a CRC-16 of its UID:
card_hash() below. So a tap now yields everything needed to drive a motor,
and no card needs registering by hand any more.

They used to have to be read off the air with ../watch_service_data.py, or
harvested alongside the card by ../examples/stick_log_cards.py. Neither is
necessary for driving -- only for gathering evidence about new cards.

── What this needs from m5 ───────────────────────────────────────────────
The m5 library from 2026-08 or later, where read_pages() tells "this tag
type cannot serve that request" (returns None) apart from "the read did not
complete" (raises ReadError), and retries the second case itself. Both used
to come back as None, so this file carried its own retry loop and a
CardReadFailed exception to tell them apart. Neither is needed now.

Opening the reader is likewise plain RFID(): the Grove 5V boost
settle-and-retry that open_reader() used to do lives in the driver.
'''

# Firmware color code -> App color code, matching picolib's constants and
# lelib's read_sensor(). The wire and the App number the colors differently.
_FIRMWARE_TO_APP = {9: 1, 7: 2, 3: 3, 5: 4, 6: 5, 2: 6, 10: 7,
                    1: 8, 8: 9, 4: 10}

CARD_MAGIC = b'L3G0'
FIRST_DATA_PAGE = 4


def firmware_to_app(firmware_color):
    '''Firmware color code -> App color code, or None if it is not a color.

    The card's page 5 and the FD02 broadcast's byte 1 both carry the firmware
    code, while everything user-facing (lelib, picolib, stick_ui) speaks App
    codes. Convert before naming a color -- firmware 2 is purple, App 2 is
    yellow, so skipping this yields plausible-looking wrong answers.
    '''
    return _FIRMWARE_TO_APP.get(firmware_color)


def _reflect(value, width):
    '''Reverse the low `width` bits of value -- 0b1101 -> 0b1011 at width 4.'''
    out = 0
    for _ in range(width):
        out = (out << 1) | (value & 1)
        value >>= 1
    return out


def card_hash(uid):
    '''(b2, b7) -- the two beacon bytes a motor validates -- from a card UID.

    A CRC-16 of the 7 UID bytes: polynomial 0x0001, reflected in and out,
    init 0, read big-endian so b2 is the high byte and b7 the low one. That
    polynomial is x^16 + 1, which makes this an XOR fold of the UID rather
    than anything cryptographic. Checked against all 39 cards in
    ../card_taps.csv; ../card_hash.py is the same thing with a CLI.

    Pass the UID read_card() returned. The motor cannot do this itself -- the
    UID is not in the broadcast, so it compares b2/b7 against what it stored
    when the card was tapped, which is exactly why computing them is enough
    to drive it.
    '''
    crc = 0
    for byte in uid:
        crc ^= _reflect(byte, 8) << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x0001) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    crc = _reflect(crc, 16)
    return (crc >> 8) & 0xFF, crc & 0xFF


class NotALegoCard(Exception):
    '''The tag is there and answered, and what it said was not a LEGO card.

    Distinct from m5_rfid.ReadError, which means the read did not complete
    and is worth trying again. That distinction used to live here as a
    CardReadFailed exception, because read_pages() returned None for both
    cases; the driver now tells them apart itself and has already retried
    three times before it raises. Getting this backwards is what makes a
    good card look like a bad one to the user.
    '''


def decode_pages(data):
    '''(app_color, serial) from the 16 bytes read at page 4.

    Raises NotALegoCard if the magic marker is not there, which is what tells
    a LEGO card apart from any other NTAG that happens to be nearby.

    A short buffer raises ValueError rather than anything card-shaped: a
    successful read_pages() always returns 16 bytes, so getting fewer means
    the caller passed something wrong, not that the card is unusual.
    '''
    if data is None or len(data) < 8:
        raise ValueError('need at least 8 bytes from page {}, got {}'.format(
            FIRST_DATA_PAGE, 0 if data is None else len(data)))
    if bytes(data[0:4]) != CARD_MAGIC:
        raise NotALegoCard('no {} marker (got {})'.format(
            CARD_MAGIC, ''.join('%02X' % b for b in data[0:4])))

    firmware_color = data[5]
    serial = (data[6] << 8) | data[7]
    if firmware_color not in _FIRMWARE_TO_APP:
        raise NotALegoCard('unknown color code {:#04x}'.format(firmware_color))
    return _FIRMWARE_TO_APP[firmware_color], serial


def read_card_data(rfid):
    '''(app_color, serial) for the selected card.

    Raises NotALegoCard for a tag that is there but is not one, and lets
    m5_rfid.ReadError through for a read that did not complete -- the driver
    has already retried that three times, re-selecting the tag each time,
    before it raises.
    '''
    data = rfid.read_pages(FIRST_DATA_PAGE)
    if data is None:
        # Still in the field but will not answer an unauthenticated 0x30 at
        # all, which means a MIFARE Classic. LEGO cards are Ultralight/NTAG
        # and always answer it.
        raise NotALegoCard('tag will not answer an unauthenticated read; '
                           'LEGO cards are Ultralight/NTAG')
    return decode_pages(data)


def read_card(rfid):
    '''(uid_bytes, (app_color, serial)) for the card on the reader, or None.

    Returns None when no tag is present. Raises NotALegoCard for a tag that is
    there but is not a LEGO card, and m5_rfid.ReadError if the read kept
    failing part way through.
    '''
    uid = rfid.read_uid()
    if uid is None:
        return None
    if rfid.sak != 0x00:
        raise NotALegoCard('SAK {:#04x}; LEGO cards are Ultralight/NTAG (0x00)'
                           .format(rfid.sak))
    return bytes(uid), read_card_data(rfid)

'''
stick_ui — the screen and speaker, shared by every Stick example.

  >>> Runs ON the M5StickS3. Needs m5/ on the board. <<<

One look for all the examples: the screen fills with the card's own color and
names it, a short high beep says a card was read, a low buzz says something is
wrong. Written once here so the examples stay short and behave the same.

    ui = stick_ui.UI()
    ui.looking('TAP A CARD')
    ui.card(color, serial)             # fills the screen, beeps
    ui.status('DRIVING')               # a line under the card, color kept
    ui.note('42 LOGGED')               # a second line under that
    ui.saved()                         # two rising notes: written to a file
    ui.problem('NOT A CARD', 'no L3G0 marker')

The screen deliberately keeps showing the last card after it is lifted off the
reader. That card's color and serial are the ones in use, so blanking them
would throw away the answer.

Needs the m5 library from 2026-08 or later -- see the check below the imports.
'''

from m5.m5_audio import Speaker
from m5.m5_display import Display, WHITE, BLACK

# The font used to be a 31-glyph subset with 18 of the 26 capitals missing,
# and draw_char() drew anything missing as a blank -- so "ORANGE" came out
# empty and looked like a dead panel rather than a font gap. This file used
# to carry those 18 glyphs and patch them in. m5 ships the full printable
# ASCII range now, so they are gone from here.
#
# Checked out loud rather than left to fail silently, because the old failure
# was invisible: blank letters on a screen that is otherwise working.
if not hasattr(Display, 'draw_text_centered'):
    raise ImportError(
        'stick_ui needs the m5 library from 2026-08 or later (complete font, '
        'text clipping, draw_text_centered). The m5/ on this board is older, '
        'so text would silently lose most of its capitals -- update it from '
        'chrisbuerginrogers/micropython.')

GRAY = 0x8410
RED = 0xF800

# Each card color as RGB565, so the screen can show the color itself rather
# than only name it. Values follow LEGO's own color map where it has one.
CARD_COLORS = {
    1: 0xD8C4,   # red
    2: 0xFEA0,   # yellow
    3: 0x0377,   # blue
    4: 0x05B6,   # teal
    5: 0x6546,   # green
    6: 0x4972,   # purple
    7: 0xFFFF,   # white
    8: 0xE2D3,   # magenta
    9: 0xF3E4,   # orange
    10: 0x7DFD,  # azure
}

COLOR_NAMES = {1: 'RED', 2: 'YELLOW', 3: 'BLUE', 4: 'TEAL', 5: 'GREEN',
               6: 'PURPLE', 7: 'WHITE', 8: 'MAGENTA', 9: 'ORANGE', 10: 'AZURE'}

# Yellow and white backgrounds need dark text to stay readable.
_DARK_TEXT_ON = (2, 7)

BEEP_OK = (1760, 120)      # short and high: card read
BEEP_BAD = (220, 250)      # low buzz: not a card we can use
BEEP_SAVED = ((1568, 90), (2093, 140))   # two rising notes: written to the log


def color_name(color):
    return COLOR_NAMES.get(color, str(color))


class UI(object):
    def __init__(self, volume=70):
        self.display = Display()
        self.speaker = Speaker(volume=volume)
        self.background = BLACK
        self.text = WHITE

    # ── drawing ───────────────────────────────────────────────────────
    def _line(self, y, text, color, scale=1, spacing=2):
        '''Draw one line centered, blanking the rest of that line first.

        Uppercase is a look, not a limitation -- the font has the full
        printable ASCII range now, lowercase included.

        Truncated to what fits rather than left to the library's clipping,
        which trims evenly off both ends. For a label you would rather read
        the start of it than the middle.
        '''
        wide = self.display.max_chars(scale, spacing)
        text = str(text).upper()[:wide]
        self.display.draw_text(0, y, ' ' * wide, color, self.background,
                               scale=scale, spacing=spacing)
        self.display.draw_text_centered(y, text, color, self.background,
                                        scale=scale, spacing=spacing)

    def looking(self, message='LOOKING', hint='tap a card'):
        '''Waiting for a card. Only shown before the first one arrives.'''
        self.background = BLACK
        self.text = WHITE
        self.display.fill(BLACK)
        self._line(40, message, WHITE, scale=2, spacing=0)
        self._line(70, hint, GRAY)

    def card(self, color, serial, beep=True):
        '''Fill the screen with the card's own color and name it.'''
        self.background = CARD_COLORS.get(color, BLACK)
        self.text = BLACK if color in _DARK_TEXT_ON else WHITE
        self.display.fill(self.background)
        self._line(20, color_name(color), self.text, scale=2, spacing=0)
        self._line(50, '#{}'.format(serial), self.text, scale=2, spacing=0)
        if beep:
            self.beep()

    def status(self, message):
        '''A line under the card, keeping the card's color on screen.'''
        self._line(90, message, self.text)

    def note(self, message):
        '''A second, quieter line under status(). Same color background.

        Thirteen characters fit at this scale; longer text is cut off.
        '''
        self._line(115, message, self.text)

    def tag(self, message):
        '''A short label pinned near the bottom of the screen -- e.g.
        which program is running, when this UI is shared across a menu
        of them. Call again after every looking()/card()/problem(),
        which repaint the whole screen and would otherwise blank it.
        '''
        self._line(205, message, GRAY)

    def problem(self, headline, detail='', beep=True):
        '''Something is wrong, on a black screen so it cannot be missed.'''
        self.background = BLACK
        self.text = WHITE
        self.display.fill(BLACK)
        self._line(34, headline, RED, scale=2, spacing=0)
        if detail:
            self._line(70, detail, GRAY)
        if beep:
            self.buzz()

    # ── sound ─────────────────────────────────────────────────────────
    def beep(self):
        '''Short high note: a card was read.'''
        self.speaker.tone(*BEEP_OK)

    def buzz(self):
        '''Low note: not a card we can use.'''
        self.speaker.tone(*BEEP_BAD)

    def saved(self):
        '''Two rising notes: something was written to a file.

        Deliberately different from beep(), which only means "a card was
        read". When you are tapping a stack of cards, the two sounds are what
        tell you a card actually landed in the log without watching the screen.
        '''
        for freq, ms in BEEP_SAVED:
            self.speaker.tone(freq, ms)

    def close(self):
        self.speaker.deinit()

# lasm_compiler.py — what's been verified, what hasn't

`lasm_compiler.py` compiles LASM (LEGO Assembler) source into RCX
bytecode: `Command(opcode, params)` objects ready for
`rcx_driver.RCX._send()`/`._send_and_recv()`. It's a from-scratch
reimplementation of LEGO's own LabVIEW compiler — `ConvertCodes.vi` and
its `LASM=>Bits` subVI (Copyright 2000 Tufts University) — not a port
of any existing open-source tool. Read `lasm_compiler.py`'s own module
docstring first; this file is the *narrative* of how the encoding rules
were established, for whoever picks this up next.

**Layout note (2026-08-12):** the compiler and everything firmware-
related now live in `tools/` — `tools/lasm_compiler.py`,
`tools/OpCodes.json`, `tools/lasm-opcode-reference.md`,
`tools/rcx_firmware_data.py`, `tools/default_codes.lasm`,
`tools/flash_rcx.py`, `tools/stick_link.py`, and both RCX drivers
(`tools/rcx_ir.py`, `tools/rcx_driver.py`). The only top-level entry
points are `setup_wand.py` (deploy everything to a fresh Stick),
`run_on_rcx.py` (compile a LASM program and run it) and
`load_firmware_on_rcx.py` (flash firmware, then load the defaults),
all three Mac-side. The wand app that stays on the device is `main.py`,
`RCXWand.py` (was `rcx.py`), `cardID.py`, `stick_*.py`, `lego_card.py`
and `m5/`. Paths below that predate the move should be read with a
`tools/` prefix.

Note the two drivers live in `tools/` but are *device* runtime: they
get copied to the root of the Stick's flat filesystem, so repo layout
and device layout are unrelated. `tools/stick_link.py`'s
`DEVICE_RUNTIME` / `WAND_FILES` are where that mapping is defined --
change a path there, not in the README's copy command.

## Getting MicroPython onto a fresh StickS3 (download mode)

A board fresh from M5Stack ships with UIFlow, not MicroPython, and
getting it into download mode is the whole difficulty. **Hold the power
button down while plugging in the USB cable.** That is what actually
works -- confirmed on hardware 2026-08-12, and it is the user's own
finding, not something from a datasheet.

M5Stack's own docs (docs.m5stack.com/en/uiflow2/sticks3/program) say to
hold the side button ~2 seconds and release when an internal green LED
blinks. **That did not work here** and cost several attempts; don't
trust it over the line above.

Why it matters so much on this board: the USB serial port is provided
by the *running firmware* over the ESP32-S3's native USB, not by a
separate USB-UART bridge. The instant esptool asserts its normal reset,
the chip resets, the CDC device detaches, and the port vanishes
mid-handshake. Symptoms, all of which mean "not in download mode",
none of which mean "esptool is broken":

- `Could not configure port: (6, 'Device not configured')`
- `termios.error: (6, 'Device not configured')` from `flush_input`
- `Failed to connect to ESP32-S3: No serial data received.`
- the port disappearing from `/dev/cu.usbmodem*` entirely, and not
  coming back until the board is replugged and powered on

Two things that do NOT work as escapes, both tried: `--before
usb_reset` (the port is already gone by then), and driving the REPL to
call `machine.bootloader()` (UIFlow presents no interactive REPL on
that port -- writing to it gets no response at all, only the boot
banner is ever emitted).

Once the power-button-while-connecting trick has worked, confirm with a
**read-only** probe before erasing anything. It should name the chip
rather than time out:

```
esptool.py --chip esp32s3 --port /dev/cu.usbmodemXXXX \
    --before no_reset --after no_reset flash_id
# Chip is ESP32-S3-PICO-1 (LGA56) (revision v0.2)
# Detected flash size: 8MB
```

Then flash, keeping `no_reset` on *both* ends so esptool does not knock
the board back out of the bootloader between erase and write:

```
esptool.py --chip esp32s3 --port /dev/cu.usbmodemXXXX \
    --before no_reset --after no_reset \
    write_flash --erase-all -z 0 ESP32_GENERIC_S3-SPIRAM_OCT-<ver>.bin
```

Use the **Octal-SPIRAM** build from
micropython.org/download/ESP32_GENERIC_S3/ -- not the plain or
quad-SPIRAM one; this board is an ESP32-S3-PICO-1-N8R8 with octal
PSRAM. Matching the version already running on a known-good unit
(v1.28.0 / 2026-04-06 here) removes one variable when comparing two
boards.

It ends with `Hash of data verified.` and `Staying in bootloader.` --
that is success, but the chip is still in the bootloader, so unplug,
replug (no button this time) and press the power button to boot
MicroPython. Then `python3 setup_wand.py` provisions it in one command.

## Where the source material lives

- `~/Desktop/compiler/` — LabVIEW block diagrams and front panels for
  `ConvertCodes.vi` and its dependencies, exported to PNG/HTML. This is
  the primary source. Read the panel/diagram images directly (they're
  small enough) rather than guessing from filenames.
- `tools/OpCodes.json` / `tools/lasm-opcode-reference.md` — a
  separate, independently-sourced opcode table (LEGO's P-Brick
  Communication Protocol doc, flattened to JSON then grouped into
  Markdown). Used for opcode numbers, LASM mnemonics, and `Structure`/
  `LASM format` text — but that text is often ambiguous prose about bit
  packing, so treat it as a *starting hypothesis*, not ground truth.
  Ground truth is real captured bytes (see below).

## The two things that actually pinned the encoding down

Everything genuinely verified in this module traces back to two real
captured examples, both checksum-valid, both reproduced exactly by
`_selftest()` in `lasm_compiler.py` (run `python3 tools/lasm_compiler.py` to
re-check):

1. **`LASM=>BitCode.vi`'s own front panel** — a 12-line LASM program,
   its per-line compiled bytes, and the 3 real wire packets those
   produced (`BeginOfTask` + 2× `ContinueDL`). This is where the packet
   envelope, chunking, and most opcode byte layouts (`set`, `setv`,
   `disp`, `mone`, `jmp`) came from. **Caveat**: this was read out of a
   LabVIEW screenshot by eye (not OCR), at a resolution where `5` and
   `D` are genuinely hard to tell apart, and two of the ContinueDL
   packets turned out to be visually truncated by the indicator's fixed
   display width. One transcription error was caught and fixed this way
   (`BeginOfTask`'s per-line stub row was misread as 4 params instead
   of 5) — if something here doesn't match real hardware, re-check
   against this image before assuming the hardware's wrong.
2. **An independently supplied hex/LASM listing** (a 4-line task body:
   `tmrz 3` / `set 24,9,2,1` / `plays 5` / `wait 2,100`) with 2 full,
   *cleanly* captured wire packets (no truncation, every checksum
   verified in code, not by eye). This is higher-confidence than #1
   where the two disagree — see the `tmrz` story below.

## The `tmrz` story, and why target-awareness exists

Example #1 encoded `tmrz 0` as `A1 00` — `ClearTimer`'s own dedicated
opcode. Example #2 encoded `tmrz 3` as `05 01 03 02 00 00` — a generic
`SetSourceValue` assignment (`dest = (Timer, 3)`, `orig = (Constant,
0)`) instead. These looked like a contradiction until the user supplied
a screenshot of the actual `LASM=>Bits` case-structure dispatcher: it
substitutes a generic assignment for any opcode the **NXT** firmware
doesn't implement, with an explicit comment on each case ("Opcode is
not supported on NXT brick. Replace with assignment to a source
instead."). So it's not a contradiction — it's two different compile
targets. `lasm_compiler.py` models this as `nxt_compatible=True/False`
threaded through `assemble_line()`/`compile_lasm()` (default `True`,
since that's what the more recently supplied example used), with a
`_TARGET_AWARE` dispatch table for every opcode with a confirmed
substitution.

**If you find more of these substitution cases** (more LabVIEW
screenshots, or another real captured example that disagrees with what
`nxt_compatible=False` currently emits for some opcode), that's the
pattern to extend: add a `_dedicated_X`/`_nxt_X` pair and register it
in `_TARGET_AWARE`. Don't assume an opcode is NXT-safe or NXT-unsafe
without evidence either way — several similar-looking "clear X" opcodes
(`cnti`, `cntd`, `senz`) are deliberately left dedicated-only because
nobody's shown a substitution case for them specifically, even though
`cntz` (their sibling) turned out to have one.

## The two enums that unblocked the substitutions

Both supplied directly by the user as LabVIEW enum edit-items lists
(not inferred, not from NQC or any other RCX tool):

- **`source.ctl`** (0–63, what `lasm_compiler.py` calls `SRC_*`) —
  **diverges from NQC's `RCX_ValueType` enum starting at index 5**
  (`5` is `MotorPowerSigned` here, `TachCounterType` in NQC's
  `RCX_Constants.h`). If you're ever tempted to fill in a source code
  by cross-referencing NQC or some other RCX tool's source, don't —
  this project's `source.ctl` is a different, later enum (it supports
  Scout/Spybot/NXT-specific sources NQC's classic-RCX-only enum
  doesn't), and it's the one this compiler's real output actually uses.
- **`system parameters.ctl`** (0–90) — the sub-index selecting *which*
  system parameter within `SRC_SYSTEM` (24) a substitution reads or
  writes (`bTransmitterRange`=13, `bPlaySounds`=32, `nDatalogSize`=42,
  `nViewState`=53, `nProgramNumber`=61, `nImmediateBatteryLevel`=0).
  The complete enum (all 91 entries) is `lasm_compiler.py`'s
  `SYSTEM_PARAMETERS` dict — supplied directly by the user (not
  inferred), in RCX_Wand's own `set 24,<param>,2,<value>` firmware
  post-boot init sequence (`nPowerDownDelay`=20, `nPreambleSize`=17,
  `nWatchFormat`=21, `bFloatDuringInactiveMotorPWM`=14 — see that
  project's README/session notes for where these came from: a real
  LabVIEW block diagram, not this compiler's own test data). Only
  entries actually used somewhere got a dedicated `SYSPARAM_*` name too
  — the same convention `SRC_*` follows — but the full table is no
  longer chat-history-only.

A third enum (`opcodes.ctl`, opcode byte → internal LASM-Defs name) was
also supplied, mainly useful as a cross-check — it's what confirmed
`getBattery`'s substitution target is `getValue` = opcode `0x12`
(`poll`), and validated the rest of the opcode table against known
internal names (`setXmitPower`=`0x31`, `clearCounter`=`0xB7`, etc.).

## What's confirmed vs. reasoned vs. genuinely unverified

Grep `lasm_compiler.py` for these tags on individual encoders:

- **`[VERIFIED]`** — byte-for-byte checked against one of the two real
  examples above.
- **`[DOCUMENTED]`** — not in either example, but `OpCodes.json`'s
  `Structure` field is unambiguous (plain positional bytes or a
  LO/HI-split 16-bit value, no bit-packing to misread).
- **`[BEST-EFFORT]`** — involves bit-packing or field ordering that had
  to be interpreted from `Structure`'s free-text prose. Most likely
  candidates for a subtle bug if something doesn't work on real
  hardware: `senm` (slope/mode packing), `chk`/`chkl` (comparison
  condition + source packing, and whether the final jump distance is
  really direction-less/forward-only), `monal`/`monel` (long-jump
  field ordering).
- **`[REASONED, not pixel-confirmed]`** — currently just `view`'s NXT
  substitution: the block diagram screenshot for `"setDisplay"` only
  clearly showed 3 of an expected 4 combiner inputs at the resolution
  captured. The implementation assumes the same shape as every other
  confirmed substitution, which fits `SetSourceValue`'s structure
  exactly if true, but nobody's seen a real captured `view`/`setDisplay`
  packet to confirm it byte-for-byte.

**Open/unverified, called out in the module docstring:**

- Jump-distance-from-label anchoring (`compile_lasm`'s label
  resolution) uses NQC's documented convention — distance relative to
  the byte immediately *after* the jump instruction — because that's
  the only convention with any real source behind it. `ConvertCodes.vi`
  own arithmetic for this was never traced from the diagrams. If labels
  resolve to off-by-N jumps on real hardware, this is the first place
  to check. (Pass an explicit integer distance instead of a label name
  to sidestep it entirely.)
- 110 of 184 unique LASM mnemonics in `OpCodes.json` aren't implemented
  at all — mostly Scout-only, NXT/Swan-only 32-bit float/long math,
  debug breakpoints, and the 82 entries with an empty `Structure` field
  (no byte layout documented anywhere available). Calling
  `assemble_line()` on one of these raises `NotImplementedError` naming
  the opcode and showing whatever `Structure` text exists, rather than
  guessing.

## Toggle bit: deliberately not this module's job

The captured examples show packets with the toggle bit (`0x08`)
alternating on *every* packet, regardless of whether the opcode
repeats — different from NQC's real transport, which only toggles on
an exact repeat of the last transmitted opcode byte (see
`ESP32-RCX/library/rcx_driver.py`'s `_build()` docstring, which
implements and verifies that NQC rule). `lasm_compiler.py` never sets
the toggle bit — `Command.opcode` is always clean — on the theory that
toggling is a transport-time concern, not a compile-time one. Feed
`Command.opcode`/`.params` to `rcx_driver.RCX._send()` or
`._send_and_recv()`, which already handles it.

## Re-verifying after a change

`python3 tools/lasm_compiler.py` runs `_selftest()`, which re-derives both
worked examples from scratch and diffs every byte. If you add or change
an encoder, add its evidence there too rather than just trusting a
one-off manual check — that's what caught the `BeginOfTask` param-count
transcription error during development.

---

# Firmware download over IR — investigation notes (2026-08-11)

Separate topic from everything above: this is about **getting bytes to
and from a real RCX over the StickS3's IR link**, using
`rcx_driver.py`'s `download_firmware()` and, later, a hand-rolled
"blind send" variant. Read this before touching `flash_rcx.py` or
`rcx_driver.py`'s receive path again — a lot of ground was covered live
against real hardware and most of it didn't work, which is exactly the
kind of thing worth not re-discovering from scratch.

## Where things stand

**RESOLVED 2026-08-12 — a full firmware download completed, acked end
to end, and the RCX booted it.** Read "## 2026-08-12: what was actually
wrong" at the bottom of this file first; the 2026-08-11 notes below it
are kept because their *observations* are all still accurate and were
what made the diagnosis possible, but several of their *conclusions*
turned out to be wrong. In particular "replies cannot be decoded" was
never a protocol or timing problem — the IR receiver simply had no
power.

Original 2026-08-11 status, left for context: the RCX's screen showed
real, live counting during *exactly one* attempt out of roughly a
dozen; that attempt stalled before the end and was never reproduced.

## The two separate problems

### 1. Replies from the RCX cannot currently be decoded — confirmed broken

`download_firmware()` (the ack'd, correct-per-protocol implementation)
needs a successfully decoded reply after *every* step — ping,
boot-mode, begin, every chunk. It never gets one, so it fails on the
very first step (`ping`) almost instantly. This is why an early attempt
"went too fast" — it wasn't skipping steps, it was failing before
reaching them.

What was actually tested, live:

- Listening with **no transmission first**: clean silence, 0 pulses,
  for the full timeout window (1.5s). Ambient IR noise alone is not the
  problem.
- Listening **right after our own transmission**: reliably picks up
  *something* — but the pulse durations are nowhere near clean
  multiples of the 417µs bit period (2400 baud) that even a
  garbled-but-real RCX reply should show. Compare our own transmitted
  packets, which *are* clean multiples (that's how `_send_ir_bytes`
  builds them) — these captures look like electrical/optical noise, not
  data.
- **The noise scales with how much we just transmitted.** A short ping
  (~1ms TX) produced one small blip late in a 100ms window. A 200-byte
  chunk-sized send (~74ms TX) produced a huge burst — 100+ pulses in
  the first 100ms after TX ended, decaying smoothly over ~400-500ms
  down to isolated single blips. A genuine RCX reply should be roughly
  constant regardless of what *we* sent it; this pattern points to
  self-hearing (electrical ringing / optical leakage off our own
  transmit LED) or AGC settling, not the RCX replying.
- Forcing the speaker amp off first (`m5_power.power_off_speaker()`) —
  a documented gotcha ("speaker must be off for the IR receiver to work
  reliably") — made no difference.
- Pointing an external remote control at the receiver and holding a
  button: across two separate 6-8 second windows, only isolated
  single-pulse blips (never a real multi-pulse burst a remote's
  continuous transmission would produce). Inconclusive but leans toward
  the receiver not reliably detecting external IR at all, independent
  of the RCX.
- **Real bug found and fixed** (deployed, in `rcx_driver.py`'s
  `_send_and_recv`): `_recv_ir_bytes` committed to "this is the reply"
  on the very first RX-pin activity, even if it was a noise blip too
  short to be real — so a self-hearing glitch right after TX could end
  the whole listen attempt on garbage before the genuine reply (if any)
  had a chance to arrive. Fixed by looping: if a capture doesn't parse,
  keep listening with whatever time budget remains, instead of treating
  it as a timeout. **This fix is real but did not produce a single
  valid decoded reply in any test that followed it** — the underlying
  no-real-signal-ever-arrives problem persists. Don't assume replies
  work now just because that bug is fixed.

Net: treat the RX/ack path as **not functional**. Any code path that
depends on decoding a real reply (the stock `download_firmware()`,
`alive()`, `get_battery_mv()`, `rcx_ir.RCX.alive()`) should be assumed
broken until proven otherwise, live, on hardware.

### 2. Blind (unacked) sending — worked once, not since

Since acks don't work, tried mirroring what already works reliably for
motor control: fire-and-forget `_send()`, no reply checking, with a
fixed delay between packets. Sequence used (all opcodes verified
against `lasm-opcode-reference.md` / `OpCodes.json`, not guessed):

```
0x65  reset/boot-mode   args [1,3,5,7,0x0b]                  ("GoIntoBootMode" — see rcx_driver.py's
                                                                comment on this opcode's real name vs
                                                                ESP32-RCX's "delete-firmware" label)
0x75  begin-firmware    args [start_lo,start_hi,cksum_lo,cksum_hi,0x00]
                         start=0x8000, checksum=sum(FIRMWARE[:0x4c00]) & 0xFFFF (=0x5991 for
                         rcx_firmware_data.py's image)
0x45  chunk (x120)      args [seq_lo,seq_hi,len_lo,len_hi] + 200 data bytes + [sum(data) & 0xFF]
                         seq counts 1,2,3,...; last chunk uses seq=0
0xA5  unlock/boot       args [76,69,71,79,174]  ("LEGO" + 0xAE)
```

Plus, after unlock, a post-boot init sequence the user supplied from a
**real, working LabVIEW block diagram** (not from this project's own
guesswork) — four `set` (opcode `0x05`, `set 24,<param>,2,<value>`,
i.e. `SRC_SYSTEM`=24, source=`SRC_CONSTANT`=2) calls:

```
set 24,20,2,3   Powerdown time (nPowerDownDelay)
set 24,17,2,3   nPreambleSize
set 24,21,2,1   nWatchFormat
set 24,14,2,1   bFloatDuringInactiveMotorPWM
```

(Indices are from the full `system parameters.ctl` enum the user
supplied directly — now `lasm_compiler.py`'s `SYSTEM_PARAMETERS` dict,
all 91 entries, not just the 6 named ones from before.)

**What happened, in order:**

1. An isolated test sent `reset` (opcode `0x65`) via the old
   (pre-retry-fix) `_send_and_recv`. No reply decoded (expected, see
   above), but the RCX visibly went into its blank "waiting for
   firmware" boot-mode screen. Confirmed: **one-way command delivery
   works** — the RCX reliably receives and acts on what we send it,
   independent of whether we can decode anything back.
2. Several minutes later (lots of unrelated diagnostics happened
   in between), ran begin+120 chunks+unlock, fire-and-forget, 900ms
   between packets, *without* resending reset (RCX was already in boot
   mode from step 1). **The RCX's screen showed real, live counting.**
   It did not reach the end, and the final unlock had no visible
   effect — user's assessment: sent too fast, no way to know a block
   landed before sending the next one, likely desynced partway through.
   This is the only time this has been observed.
3. Every attempt since — same script, same opcodes, same 900ms-1000ms
   spacing, with and without the post-unlock param-set commands, across
   multiple full power-cycles of both the Stick and the RCX, including
   at least one run where the RCX was freshly confirmed in a clean
   boot-mode state (blank screen, just-reset) immediately before
   sending begin+chunks — **produced no visible reaction on the RCX at
   all**, for the entire transfer, every time. Mac-side logging
   consistently shows the script completing normally with real
   byte-progress counted (so the RMT transmit path itself is running
   every time) — the RCX just never visibly reacts to any of it after
   that first time.

**What's been ruled out or is inconclusive:**

- Not a Mac-side/script hang — progress prints happen every attempt.
- Not (solely) about resending `reset` vs. not — tried both.
- Not (solely) about the RCX's boot-mode state going stale — tried
  with a just-confirmed-fresh boot-mode state too.
- Power-cycling either device alone, or both, hasn't reproduced the
  working state again.
- The exact original 900ms-timing, no-extra-params script was re-run
  verbatim (not just "close to" the original) — still no reaction.

**Not yet identified**: what was actually different about the one
attempt that worked. Candidates nobody has confirmed or ruled out:

- RCX battery voltage sag under sustained testing (many back-to-back
  attempts, motors run earlier in the same session) affecting whether
  boot-mode's internal checks pass.
- Accumulated state on the Stick side that a `machine.reset()` doesn't
  fully clear (RMT channel 0 has been claimed by `rcx_driver.RCX()`
  *and separately* by `rcx_ir.RCX()` dozens of times across many
  interrupted `mpremote exec` sessions over this debugging session;
  neither class explicitly releases the RMT channel).
- Physical alignment/distance sensitivity not obviously visible from
  software.
- The one success may not mean what it was assumed to mean — nobody
  has independently confirmed what the RCX's LCD is actually supposed
  to show during a real firmware download (what format, what the
  expected final value is), so "it was counting" is real but its
  significance is not fully pinned down.

## What's still missing regardless of the above

- **On-Stick-screen progress during a real attempt.** `flash_rcx.py`
  already has this wired up in principle (`ui.status('{}%'...)` per
  acked chunk in `_flash()`'s `on_progress` callback) — but since
  ack-checking doesn't work, `flash_rcx.py` itself has never gotten far
  enough to show anything. Every test in this investigation was a raw
  diagnostic script run directly via `mpremote exec`, which never
  touches the UI at all — so "nothing shown on the Stick's screen"
  during all of this debugging is expected, not a separate bug. If
  blind sending is the path forward (given ack-checking's current
  state), the fix is to add screen updates to the *blind* send loop
  directly, not rely on `flash_rcx.py`'s existing (ack-gated) progress
  hook.
- Independent confirmation of the RCX's expected on-screen behavior
  during a real download (ideally from LEGO/LabVIEW documentation or
  the user's own prior experience with the working tool), to have a
  ground truth to check a blind send against instead of guessing from
  screen behavior alone.

## Where to pick this back up

Superseded — see the next section.

---

# 2026-08-12: what was actually wrong

A full 23904-byte download ran start to finish and `UnlockFirmware` came
back with LEGO's easter-egg string — which the ROM only sends *if valid
firmware has been downloaded*:

```
4a 75 73 74 20 61 20 62 69 74 20 6f 66 66 20 74 68 65 20 62 6c 6f 63 6b 21
J  u  s  t     a     b  i  t     o  f  f     t  h  e     b  l  o  c  k  !
```

— "Just a bit off the block!", every byte intact through `_find_reply()`.
The RCX then answered `ping` and reported 8.456 V of battery.
Five separate bugs were in the way, and *none* of them was the packet
sequence — that had been correct all along, exactly as the 2026-08-11
notes suspected.

**Read the next section before drawing conclusions from that success:
the run was blind, and did not depend on acks at all.**

## 0. Why the download worked but `poll` never did

Worth getting straight, because it is easy to misread the win above as
"the ack path works now". It does not follow.

**The successful download was blind.** It was `dl.py`, which fired every
packet fire-and-forget and merely *logged* any ack it happened to
decode. Its own summary line reads `all 23904 bytes sent in 294s, 60
acks seen` — 60 across 121 packets. It completed because **transmit
works**, not because replies came back. That is exactly why `--blind`
is a viable workaround today.

**The "half" was the toggle bug, not marginal signal.** Consecutive
`0x45` chunks alternate `0x45`/`0x4D`, so replies alternated
`0xB2`/`0xBA`, and matching the exact byte instead of masking `0x08`
discarded every other one. 60/120 is exactly that.

**Reply length really does decide survival odds.** The sync never
survives the preamble (see §4), so what must arrive *completely intact*
is the byte/complement run from opcode to checksum. One bad byte
anywhere fails the complement check or the checksum and kills the whole
packet:

| Reply | Payload | Bytes that must survive |
|---|---|---|
| `ping` -> `E7` | 1 | 4 |
| `ContinueDL` -> `B2 00` | 2 | 6 |
| `poll` -> `E5 lo hi` | 3 | **8** |
| `pollb` -> `C7 lo hi` | 3 | **8** |

At the best rate ever measured here (ping 8/10, so ~94.6% per byte)
that predicts ~71% for block acks but only ~63% for `poll`. Short
replies survive a marginal link markedly better than long ones.

**But length is the secondary reason.** That maths predicts `poll` at
~60%; it was observed at **0%**. The dominant factor is that the
receiver degraded *between* the two experiments — with identical code,
ping went 8/10 -> 2/10 -> 0/10 across the session while the idle line
went from 0 transitions/second to 300-1700. The download happened while
the receiver was still healthy; every `poll` attempt happened after it
started failing.

So the ordering is the answer: **downloads early and blind, reads late
and degraded.** Delivery was never the problem in either case — the
`set` commands never acked either, yet visibly took effect (the RCX's
clock reset when `nWatchFormat` landed). Only the return path failed.

## 1. The IR receiver had no power (this was the whole ballgame)

Both halves of the StickS3's IR block — the IR928 transmit LED and the
VSOP38338 receiver — hang off the **GROVE_5V boost rail**, not off a
always-on rail. The "Power Network" sheet of
`~/GitHub/micropython/M5StickS3/StickS3_schematic.pdf` (page 1) draws
`GROVE_5V` feeding both `IR_TX` and `IR_RX`; page 4 ("BTB小板 IR_TX/RX
SPK") is the detail. **That rail auto-clears on every reset**, and
`m5_power.power_on_grove_5v()` is the only thing that turns it back on.

With it off, GPIO42 is not merely dead but *actively misleading*: the
receiver's output is high-Z, so the daughterboard's R6/R5 10K/20K
divider pulls the pin to 0V — which reads as "receiver active" forever.
Measured live:

```
BEFORE power_on_grove_5v():  no-pull 2000/2000 samples low, pull-up 2000/2000 low
AFTER  power_on_grove_5v():  idle high, 0 transitions across a full second
```

That single missing call explains every symptom in the 2026-08-11
notes: `_recv_ir_bytes()`'s `while not active()` loop exited instantly
every time and captured coupling noise off our own transmit LED; the
"noise that scales with how much we just transmitted" was exactly that;
and pointing a TV remote at the Stick produced nothing because the
receiver was unpowered then too. **The 2026-08-11 conclusion that the
receiver hardware might be broken was wrong.**

Related: `Pin(42, Pin.IN, Pin.PULL_UP)` was also wrong. The divider
already defines the node; the internal pull-up fights it and, with the
receiver unpowered, parks the pin at ~1.0V — inside the ESP32's
undefined band, which is what turned coupling into phantom "blips". Use
a bare `Pin.IN`.

Also note `stick_ui.UI()` constructs a `Speaker`, which powers the
AW8737 amp back **on** — the very thing M5Stack's docs say breaks IR
reception. Anything driving IR from the UI has to power it down again.

## 2. `write_pulses()` is non-blocking, and nothing waited for it

Measured live: `write_pulses()` returned in **0 ms** on a packet that
took **35 ms** on air. `_send_ir_bytes()` never called `wait_done()`,
while `m5/m5_ir.py`'s `IRTransmitter` always paired the two. A 200-byte
firmware chunk is ~1.9 s of transmission but `_send()` slept only
100 ms, so consecutive chunks overlapped on the wire. This is almost
certainly the desync behind the one 2026-08-11 attempt that visibly
counted on the RCX's screen and then stalled.

## 3. The RCX mirrors the toggle bit back in its replies

`reply_opcode = (~cmd) & 0xF7` is right, but the *received* opcode has
to be masked too: compare `reply[0] & 0xF7`, never `reply[0]`. A real
ping answer is `0xE7` **or** `0xEF` depending on how `_build()` toggled
the command. Matching the exact byte silently drops every other reply —
which is precisely what happened on the first successful download: **60
acks across 120 blocks, exactly half**, because the alternating
`0x45`/`0x4D` chunks came back `0xB2`/`0xBA`.

## 4. The `55 FF 00` sync does not survive, and must not be required

This one is counterintuitive and cost the most time. The RCX sends a
preamble ahead of every transmission, and the receiver's AGC is still
settling through it, so **the header reliably arrives as garbage while
the packet body behind it decodes perfectly**. Forty consecutive pings
each came back looking like:

```
55ff00 18e7 18e7 | 5fdfdf7dd777dfd7dfdfdf75dfd757df | e718e718
  our own echo   | preamble, AGC settling           | FLAWLESS REPLY
```

`e7 18 e7 18` is a textbook ping reply (opcode E7, complement 18,
checksum E7) — and a sync-anchored parser threw away all forty. Ping
decoding went **0/10 → 8/10** on this change alone.

`rcx_driver._find_reply()` now ignores the sync entirely: it finds
maximal runs of byte/complement pairs and, inside each run, looks for a
stretch that starts with the expected reply opcode and ends at its own
checksum. Complement pairs plus a checksum is structure enough that
false positives are unlikely, and anchoring on the expected opcode also
steps over our own echo without special-casing it.

## 5. `SetSourceValue` takes five params, not four

`set 24,<param>,2,<value>` is **`[24, param, 2, value_lo, value_hi]`** —
the origin value is 16-bit LO/HI. Confirmed against a real capture of
`set 2,2,2,2`:

```
55 FF 00  0D F2  02 FD  02 FD  02 FD  02 FD  00 FF  15 EA
          opcode 0x05 toggled to 0x0D, params [2,2,2,2,0], cksum 0x0D+8=0x15
```

Sent with only four params it gets no reply at all. The post-boot init
from the LabVIEW diagram does land, though — after sending it, the
user confirmed **the RCX's clock was reset**, so `nWatchFormat` took
effect even while its acks were undecodable.

## What is still marginal

Downstream (RCX → Stick) decoding **works but is variable**, and this
is now a link-quality problem, not a protocol one:

- **Short range beats long range, clearly.** `bTransmitterRange`
  (system param 13) = 0 scored ping 8/10; = 1 scored 0/10. Long range
  appears to overdrive a receiver sitting well inside its working
  range. Leave it at 0. *Watch out:* a sweep that ends on `range=1`
  leaves the RCX there and poisons every measurement afterwards —
  that happened here and briefly looked like a code regression.
- **Short replies survive, long ones often don't.** Ping (1-byte
  payload) decodes well; `pollb`/`poll` (3-byte payloads) frequently
  fail their checksum with corrupted bits that are consistently 0→1,
  i.e. dropped marks. Reading system parameters back via `poll 24,<n>`
  did not work reliably in any configuration tried.
- **There is real temporal variability** not explained by any setting:
  the same code and same settings scored 8/10, then 0/10, then 2/10
  across consecutive runs. Untested candidates: RCX battery sag,
  alignment, ambient IR, AGC recovery after our own point-blank echo.
- Firmware download does not care much, because block acks are short:
  the completed run acked every block it logged.

## Practical recipe that worked

1. `m5_power.power_off_speaker()` then `m5_power.power_on_grove_5v()`,
   settle 300 ms. **Every time — the rail auto-clears on reset.**
2. RX on the **hardware UART**: `UART(1, baudrate=2400, bits=8,
   parity=1, stop=1, tx=1, rx=42, rxbuf=4096)`. The receiver
   demodulates the carrier and its output *is* a 2400 8-O-1 line, idle
   high, mark = start bit — no inversion needed. TX still needs RMT for
   the 38 kHz carrier.
3. Transmit, then `rmt.wait_done()`, then listen. Flush the UART
   *before* transmitting.
4. Parse with `_find_reply()`; never require `55 FF 00`.
5. 100 ms between packets, retry an unanswered one up to 5 times.
6. RCX already in boot mode? Skip `reset` (0x65) — the ROM does not
   answer it again. Otherwise send it first.

Working scratch scripts live in this session's scratchpad (`dl.py`,
`lib_rcx.py`, `verify.py`, `ab.py`); `rcx_driver.py` has all five fixes
folded in.

## Two compiler bugs that only multi-program source exposes

Both found compiling `default_codes.lasm` (five programs, six tasks) and
both fixed in `lasm_compiler.py`. Neither could show up in
`examples/*.lasm`, which are all single-task, single-program — worth
knowing before trusting the compiler on anything larger.

1. **Labels were global, not per-task.** `labels` was one flat
   `name -> (block, offset)` dict, so a `Label0` in a later task
   overwrote the earlier one and every earlier jump then failed with
   "label isn't in the same task/sub as the jump referencing it". Real
   LASM reuses compiler-generated names (`Label0`, `Label1002`) in
   every program, so this fires immediately. Labels now live on
   `_Block.labels`, which is what the docstring always claimed
   ("scoped to the task/sub they're inside"). Duplicate labels within
   one block are now an error instead of a silent overwrite.

2. **Emit order hoisted every direct command to the front.** The old
   code emitted `direct_pending` first, then all blocks. `prgm N`
   (SelectProgram) is a direct command that chooses which slot the
   *following* BeginOfTask downloads into — so hoisting them meant all
   five `prgm` commands went out back-to-back and all six tasks landed
   in slot 4. Now a single `emit_order` list interleaves directs and
   blocks in source order. **If you touch the emit loop, this ordering
   is load-bearing.**

## Building and shipping the default programs

There is no build step and no generated bytecode module any more.
`load_firmware_on_rcx.py` reads `tools/default_codes.lasm`, compiles it
on the Mac, and pushes the resulting commands inline into an mpremote
`exec`. Same for `run_on_rcx.py` and the examples: the LASM lives as a
string in `examples/*.py`, and nothing compiled is ever written to
disk or left on the device. (`lasm_compiler.py` needs dataclasses,
pathlib and a 100KB OpCodes.json, none of which belong on the Stick --
that constraint is why the Mac does the compiling, not why we used to
generate modules.)

Compiled with **`nxt_compatible=False`** throughout, and that matters
twice for the default codes:

- `prgm N` compiles to the documented `0x91 N` (SelectProgram) instead
  of the NXT substitution `set 24,61,2,N`. Five uses in this file.
- `view 2,2` uses its dedicated opcode instead of the one substitution
  in this module still tagged `[REASONED, not pixel-confirmed]`.

`rcx_driver.RCX.download_firmware()` finishes with
`post_boot_init(power_down_minutes=15)` rather than stopping at the
image. Loading the default programs is the Mac's job — a second phase
in `load_firmware_on_rcx.py` — because it needs the compiler.

`set_system_param()` is fire-and-forget by default, deliberately. Its
acks are usually undecodable at this link's current quality, but the
commands do land: sending `nWatchFormat` visibly reset the RCX's clock
while its ack never decoded once. Don't "fix" that by gating on the
reply.

## Still open: the receiver went dead again (2026-08-12, end of session)

After the successful download and default-code work, GPIO42 went back
to reading low 2000/2000 — the same signature as the unpowered
receiver — but this time **with `BOOST_EN` confirmed set** (PWR_CFG
reg 0x06 reads 0x1F, and toggling it off/on cycles the bit correctly).
Ruled out, live: a stale RMT leaving our own transmit LED on (driving
GPIO46 low changed nothing), the RCX transmitting at us (unchanged
with the RCX off and pointed away), and stale peripheral state (a full
`machine.reset()` changed nothing — and the rail reads as already
enabled at boot, so it does *not* auto-clear the way m5_power's
docstring says).

**Narrowed down: the rail is fine, the fault is receive-side only.**
The decisive test is the transmit cross-check, because the IR928 LED
and the VSOP38338 share `5V_VBUS` on the daughterboard: sending beeps
with `rcx.beep()` made the RCX audibly beep. A dead rail cannot do
that, so the boost *is* delivering and `power_on_grove_5v()` *is*
working. GPIO42 being pinned low is therefore the receiver itself --
saturated by a continuous IR source, or latched/failed -- not a power
problem.

That also means every remaining symptom is one-directional: transmit
to the RCX is reliable (firmware download, direct commands, `set`
parameters all land), and only the RCX -> Stick path is broken. Any
acked operation (`send_program`, `download_firmware`) will fail at its
first block until this is fixed; fire-and-forget commands are fine.

**Not latched -- actively jammed.** Edge-timing capture on GPIO42
settles it. Over one second: 886 transitions, HIGH pulses all sitting
at the ~35us polling floor (min 35, median 35), LOW pulses spread from
35us to 10.4ms with a median of ~1.9ms. A failed or latched part gives
a static level, not 443 structured blips per second. This is the
signature of a receiver pinned down by a **continuous 38kHz carrier**
whose AGC keeps briefly recovering.

**And it is not optical at all.** With the RCX switched off and
removed from view AND aluminium foil over the receiver window, the
capture was essentially unchanged: 1961/2000 low, 722 transitions,
same pulse structure. Foil is genuinely opaque at 940nm, so no light
is reaching the photodiode and it is still chattering. (Black PVC
electrical tape, tried first, changed nothing either -- but that
proves little on its own, since black vinyl is largely IR-transparent.
Use foil.)

So the interference is **electrical, not light**, and it arrives with
the rail:

```
boost ON   low  986/1000   transitions/0.5s  652
boost OFF  low 1000/1000   transitions/0.5s    0     <- perfectly static
boost ON   low  987/1000   transitions/0.5s  368
```

Boost OFF gives a dead-static line, which also settles a side
question: `U12`/`EXT_5V_EN` does **not** pass USB 5V through to
GROVE_5V in this state, so the VSOP38338's only supply is the SY7088
boost. Whenever it has power it chatters; with no power the divider
just holds the pin at 0V.

Ruled out by now: our own transmit LED (driving GPIO46 low as a plain
output changed nothing), the rail being dead (the beep test), and any
optical source (foil). What remains is noise on GROVE_5V itself or a
damaged receiver -- and note this same boost gave a *clean* idle-high
line with zero transitions earlier in the same session, so the boost
is not inherently noisy.

### Everything eliminated so far

A true power cycle (USB out and battery, Grove port empty, foil on)
confirmed `BOOST_EN` genuinely does auto-clear -- `PWR_CFG` reads
`0x17` at boot and the line is dead static, 0 transitions, while
unpowered. Enabling the rail still brings the chatter straight back.
Ruled out, each tested live:

| Suspect | Result |
|---|---|
| Optical source (RCX, lighting) | Foil over the window: no change |
| Our own transmit LED | Driving GPIO46 low as plain GPIO: no change |
| Dead rail / no power | Beep test: RCX beeps, so 5V_VBUS delivers |
| Grove peripheral loading GROVE_5V | Port empty: no change |
| Stale peripheral state | Full power cycle: no change |
| BLE / WiFi radio near the front end | STA_IF, AP_IF and BLE all confirmed `active() == False`; forcing them off again changed nothing |
| LCD rail switching noise | Turning it off made it *worse* |

**Caveat on every foil measurement:** an AGC receiver in total
darkness cranks to maximum gain and will self-trigger on its own
thermal noise. That is normal behaviour, not a fault, so numbers taken
with foil on overstate the problem and must not be compared against
the healthy baseline (which was measured in ordinary room light). The
chatter does predate the foil -- 1992/2000 low with nothing covering
it -- so the fault is real, but re-measure uncovered before drawing
conclusions about severity.

The transition count also wanders wildly run to run -- 316, 328, 362,
708, 2586 per second under nominally identical conditions -- which is
itself the tell: this is an unstable analog front end, not a
reproducible interferer.

**Most likely conclusion: the VSOP38338 (or its decoupling C2 / the
R6-R5 divider) on this particular daughterboard is damaged.** It was
demonstrably healthy earlier in the same session -- a full second at
zero transitions, idle high -- and the firmware download in between
ran the IR LED hard off the same rail for five minutes straight.

### Verdict: this unit's receiver is dead

The echo test settles it. Uncovered, RCX out of the room, Grove empty:

```
idle:  low 1795/2000   transitions/s ~1700    (healthy baseline: 0 and 0)
echo:  0000000000...0008000000...  thousands of 0x00, no 55 FF 00 anywhere
```

Transmitting our own packet from an LED millimetres away -- by far the
strongest signal this receiver will ever see -- produced no
recognisable echo at all, only a continuous stream of zero bytes (the
UART reading a line held low). The identical test earlier the same day
returned a clean `55ff0018e718e7`.

A receiver that cannot hear a point-blank transmitter is not being
interfered with; its output is stuck asserted. Every environmental and
software explanation was eliminated first (table above), and transmit
still works perfectly on the same rail from the same daughterboard.

### A second StickS3 did NOT fix it -- and fails differently

A brand-new StickS3 (flashed with the same v1.28.0 Octal-SPIRAM build,
provisioned with `setup_wand.py`, `WAND READY`) behaves differently
again, so this is not one simple dead part:

| | old board | new board |
|---|---|---|
| Transmit to RCX (`beep`) | works | works |
| GPIO42 idle | ~1990/2000 low, 300-1700 transitions/s | 1000/1000 low, **0** transitions |
| GPIO42 with `PULL_UP` | stays low | goes high (~93%) |
| Own-LED echo | garbage / nothing | nothing |
| TV remote, pin 42 only, 30s, aimed, no film | (masked by chatter) | **0 transitions** |
| Full GPIO sweep during external IR | nothing but chatter on 42 | nothing on any pin |

Both boards transmit to the RCX, so both IR LEDs and both GROVE_5V
rails are good -- and since the LED and the VSOP38338 share `5V_VBUS`
on that daughterboard, the receiver has VCC on both. Yet:

- the **new** board's pin 42 never moves at all, and `PULL_UP` pulls it
  high, meaning the receiver's output is high-Z: it is not driving the
  pin. Consistent with the R5 20K divider leg alone holding it at 0V.
- the **old** board's pin 42 *is* driven, but chatters and cannot
  decode anything, including its own point-blank LED.

Pin 42 is definitely right -- the old board decoded real RCX acks
through it this morning.

**Measurement trap worth knowing:** the multi-pin sweep polls ~27 pins
per loop, so each pin is sampled ~27x less often than a focused read.
A count of 1248 transitions over 40s from that sweep is ~31/s, *below*
the same board's 514/s idle chatter -- it looked like "the remote was
detected" and was nothing of the sort. Always re-measure a candidate
signal with a single-pin, full-rate loop before believing it.

Corroboration from a separate source (Gemini, via the user): "some
units constantly read random or false noise signals on the receiver
regardless of firmware version" is a known StickS3 complaint, and
matches the old board's chatter exactly. Two other suggestions from
that same source do NOT apply here and shouldn't send anyone chasing
them: a firmware regression (this is stock MicroPython v1.28.0, and
the same board decoded real RCX acks on this same build hours
earlier), and LEDC PWM carrier misconfiguration (transmit uses the RMT
peripheral's own carrier generator, and transmit demonstrably works --
the RCX responds to both boards).

### Blind mode is the workaround, and it works

`--blind` / `acked=False` on `run_on_rcx.py`,
`load_firmware_on_rcx.py`, `RCX.send_program()` and
`RCX.download_firmware()` transmits on the normal 100 ms pacing without
waiting for any reply. **Confirmed on real hardware 2026-08-12**: LASM
programs compiled on the Mac, sent blind through a Stick with a
non-functional receiver, and running on the RCX.

This works because the failure is one-directional -- the RCX receives
us perfectly well; we just can't hear it. What is lost is verification,
and that loss is total for reads: no block status bytes, no unlock
easter-egg confirmation, and **no `poll`/`pollb`/memmap at all**.
Anything that reads a value back off the brick requires a working
receiver. `RCX.receiver_looks_alive()` exists so the tools can warn and
suggest `--blind` instead of failing on block 1 of 120.

Distinguishing "receiver output not connected" from "receiver
desensitised" needs a meter on the daughterboard (is `LED_RX` at ~3.3V
idle? is the VSOP38338's VCC pin at 5V?) -- not something more software
probing can settle.

**If you are picking this up on a third StickS3 and reception works,
nothing in the code needs "fixing".** Re-run the two-line check before
assuming otherwise:

```python
from machine import Pin; import time
from m5 import m5_power
m5_power.power_off_speaker(); m5_power.power_on_grove_5v(); time.sleep_ms(500)
p = Pin(42, Pin.IN)          # healthy: stays 1, zero transitions
print(sum(1 for _ in range(2000) if p.value() == 0), '/2000 low')
```

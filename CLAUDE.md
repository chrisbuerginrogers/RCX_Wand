# lasm_compiler.py — what's been verified, what hasn't

`lasm_compiler.py` compiles LASM (LEGO Assembler) source into RCX
bytecode: `Command(opcode, params)` objects ready for
`rcx_driver.RCX._send()`/`._send_and_recv()`. It's a from-scratch
reimplementation of LEGO's own LabVIEW compiler — `ConvertCodes.vi` and
its `LASM=>Bits` subVI (Copyright 2000 Tufts University) — not a port
of any existing open-source tool. Read `lasm_compiler.py`'s own module
docstring first; this file is the *narrative* of how the encoding rules
were established, for whoever picks this up next.

## Where the source material lives

- `~/Desktop/compiler/` — LabVIEW block diagrams and front panels for
  `ConvertCodes.vi` and its dependencies, exported to PNG/HTML. This is
  the primary source. Read the panel/diagram images directly (they're
  small enough) rather than guessing from filenames.
- `OpCodes.json` / `lasm-opcode-reference.md` (this directory) — a
  separate, independently-sourced opcode table (LEGO's P-Brick
  Communication Protocol doc, flattened to JSON then grouped into
  Markdown). Used for opcode numbers, LASM mnemonics, and `Structure`/
  `LASM format` text — but that text is often ambiguous prose about bit
  packing, so treat it as a *starting hypothesis*, not ground truth.
  Ground truth is real captured bytes (see below).

## The two things that actually pinned the encoding down

Everything genuinely verified in this module traces back to two real
captured examples, both checksum-valid, both reproduced exactly by
`_selftest()` in `lasm_compiler.py` (run `python3 lasm_compiler.py` to
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

`python3 lasm_compiler.py` runs `_selftest()`, which re-derives both
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

**Unresolved.** The RCX's screen showed real, live counting during
*exactly one* attempt, out of roughly a dozen since. That attempt
didn't finish (stalled before the end, the final unlock command had no
visible effect), and it has not been reproduced despite what looks like
the same sequence, the same timing, and multiple full power-cycles of
both the Stick and the RCX in between attempts. Something about that
one working attempt's circumstances hasn't been identified. Don't
assume the current code is close to working — assume the opposite until
proven otherwise on real hardware, live, again.

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

Start from a full power-cycle of both devices, send `reset` alone
first and confirm the blank boot-mode screen before anything else, and
resist the urge to jump straight to the full 120-chunk sequence again
— the open question is *why the one working attempt worked*, not
whether the sequence is byte-correct (it's been checked by hand against
the verified opcode table multiple times and is believed correct).

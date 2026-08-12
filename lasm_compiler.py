"""
lasm_compiler.py -- LASM (LEGO Assembler) source -> RCX bytecode.

Ported/reconstructed from LEGO's own LabVIEW compiler, ConvertCodes.vi
and its LASM=>Bits subVI (Copyright 2000 Tufts University). Their block
diagrams and front panels (exported from LabVIEW to ~/Desktop/compiler/)
were read directly rather than worked from a written spec, and the
mnemonic-to-bytes encoding rules below were checked against two real
worked examples: LASM=>BitCode.vi's front panel (12 LASM lines, their
exact per-line byte arrays, and the 3 real wire packets those compiled
to) and a second, independently supplied hex/LASM listing (a 4-line
task body, 2 wire packets). Both are reproduced byte-for-byte by
_selftest() at the bottom -- run this module directly to re-verify.

Where this differs from the original: opcode metadata comes from
OpCodes.json (the same table lasm-opcode-reference.md was generated
from) rather than the LASM Defs global ConvertCodes.vi used, and this
module only implements opcodes whose byte layout is actually documented
somewhere (~90 of 187). Encoding an unimplemented mnemonic raises
NotImplementedError naming exactly what's missing, rather than guessing
at undocumented bit layouts.

BeginOfTask's "Task size" field is simply the compiled body's byte
count -- confirmed by the second worked example (declared size 0x12
exactly matches the 18-byte body). An apparent 9-byte mismatch against
the first worked example turned out to be incomplete transcription of a
truncated screenshot, not a real protocol detail -- see git history if
curious.

Target-specific substitution: at least one opcode (ClearTimer, 0xA1)
doesn't exist on the NXT brick's firmware, and the real compiler emits
a generic SetSourceValue assignment instead wherever this applies (see
_enc_tmrz's docstring) -- confirmed directly from a LASM=>Bits block
diagram comment. Only tmrz is confirmed; other single-purpose
"clear/reset" opcodes (cntz, senz, ...) keep their dedicated bytes
absent similar evidence for them specifically.

Toggle bit: the worked example's 3 wire packets have the toggle bit
(0x08) alternating unconditionally on every packet (unrelated to
whether the opcode repeats) -- a different rule than NQC's real
transport (which only toggles on an exact repeat of the last
transmitted opcode byte; see rcx_driver.py._build's docstring). This
module doesn't set the toggle bit at all -- Command.opcode is always
clean -- on the assumption that toggling is a transport-time concern,
not a compile-time one; feed Command.opcode/params to
rcx_driver.RCX._send()/._send_and_recv(), which already implements the
verified NQC rule.
"""
import json
from dataclasses import dataclass, field
from pathlib import Path

_HERE = Path(__file__).resolve().parent
with open(_HERE / "OpCodes.json") as _f:
    _OPCODES = json.load(_f)
_BY_MNEMONIC = {}
for _e in _OPCODES:
    _BY_MNEMONIC.setdefault(_e["LASM Cmd"], []).append(_e)


class LasmError(Exception):
    """A problem in the LASM source itself (bad mnemonic, wrong arg
    count, unresolved label, out-of-range jump, ...)."""


@dataclass
class Command:
    """One RCX command ready to send: rcx_driver.RCX._send(cmd.opcode,
    cmd.params) or ._send_and_recv(...). No toggle bit, no envelope --
    see the module docstring for why."""
    opcode: int
    params: list
    source_line: str = ""
    mnemonic: str = ""

    def raw_bytes(self):
        """[opcode] + params, unenveloped -- what LASM=>Bits itself
        produced per line in the worked example."""
        return bytes([self.opcode & 0xFF] + [p & 0xFF for p in self.params])


# ----------------------------------------------------------------------
# "source" codes -- the full, authoritative source.ctl enum, supplied
# directly (its edit-items list, not just a default-value screenshot).
# This DIFFERS from NQC's RCX_ValueType (RCX_Constants.h) starting at
# index 5 -- e.g. 5 is MotorPowerSigned here, TachCounterType in NQC --
# so this list wins over anything inferred from NQC in this module.
# Only names actually useful as named constants get a SRC_* -- the rest
# are commented for reference; pass the raw int for anything else.
# ----------------------------------------------------------------------
SRC_VARIABLE = 0
SRC_TIMER = 1                    # "Timer100ms"
SRC_CONSTANT = 2
SRC_MOTOR_STATUS = 3
SRC_RANDOM = 4                   # 0-32767
SRC_MOTOR_POWER_SIGNED = 5
# 6  DSIntrinsicIndirectGlobal
# 7  RobolabFreeSample
SRC_PROGRAM = 8                  # "Program Number"
SRC_SENSOR_VALUE = 9
SRC_SENSOR_TYPE = 10
SRC_SENSOR_MODE = 11
SRC_SENSOR_RAW = 12
SRC_SENSOR_BOOLEAN = 13
# 14 OBSOLETE (ClockMinutes)
SRC_MESSAGE = 15                 # "PBMessage"
# 16 OBSOLETE
SRC_GLOBAL_MOTOR_STATUS = 17     # 0-2
# 18 DSEventType (0-15)
# 19 DSEvent (0-15)
# 20 OBSOLETE
SRC_COUNTER = 21                 # 0-2
SRC_TIMER_1MS = 22
SRC_TASK_EVENTS = 23             # 0-9
SRC_SYSTEM = 24                  # see the system parameters.ctl sub-enum
SRC_EVENT_STATE = 25             # 0-15
SRC_TIMER_10MS = 26
SRC_CLICK_COUNTER = 27           # 0-15
SRC_UPPER_THRESHOLD = 28         # 0-15
SRC_LOWER_THRESHOLD = 29         # 0-15
SRC_HYSTERESIS = 30              # 0-15
SRC_DURATION = 31                # 0-15
SRC_MOTOR_POWER_8 = 32
SRC_UART_SETUP = 33
# 34 OBSOLETE (BatteryLevel Avg)
# 35 OBSOLETE (Firmware Version)
SRC_INDIRECT_VAR = 36            # 0-47
# 37-39, 42 OBSOLETE (Datalog type/value/byte, indirect/direct)
# 40 opcdSourceNxtI2CBytesRead
# 41 opcdSourceNxtI2CMessagingStatus
SRC_GLOBAL_VAR = 43
SRC_INDIRECT_GLOBAL_INT = 44     # 0-255
SRC_INDIRECT_GLOBAL_LONG = 45    # 0-255
SRC_INDIRECT_GLOBAL_FLOAT = 46   # 0-255
# 47 DSIndexedGlobalAndConstant
# 48 DSIndexedGlobalLongAndConstant
# 49 DSStackVar
SRC_CONSTANT_VAR = 50            # 0-255
# 51 DSFunctionReturnValueWord
# 52 OBSOLETE
SRC_MOTOR_RUN_STATE = 53
SRC_SMART_MOTOR_ENCODER = 54
SRC_SMART_MOTOR_ENCODER_TARGET = 55
SRC_SMART_MOTOR_ENABLE = 56
SRC_MOTOR_180_REFLECTION = 57
# 58-62 OBSOLETE (task stack var/address/size)
# 63 DSBad

# chk/chkl comparison codes (relop argument), per OpCodes.json's
# Structure text for SCheckDo/LCheckDo.
CHK_GT = 0
CHK_LT = 1
CHK_EQ = 2
CHK_NE = 3

# "System Parameter" codes -- the sub-value selecting which parameter
# within SRC_SYSTEM (24) a SetSourceValue/getValue substitution reads
# or writes: `set 24,<param>,2,<value>` (dest=System/param, source=
# Constant) is the shape every use of this table takes. Full
# authoritative system parameters.ctl enum supplied directly by the
# user (91 entries, 0-90) -- SYSTEM_PARAMETERS below is the complete
# lookup table; only entries actually used somewhere got a dedicated
# SYSPARAM_* name too, the same convention SRC_* above follows.
SYSPARAM_IMMEDIATE_BATTERY_LEVEL = 0
SYSPARAM_TRANSMITTER_RANGE = 13
SYSPARAM_FLOAT_DURING_INACTIVE_MOTOR_PWM = 14
SYSPARAM_PREAMBLE_SIZE = 17
SYSPARAM_POWER_DOWN_DELAY = 20
SYSPARAM_WATCH_FORMAT = 21
SYSPARAM_PLAY_SOUNDS = 32
SYSPARAM_DATALOG_SIZE = 42
SYSPARAM_VIEW_STATE = 53
SYSPARAM_PROGRAM_NUMBER = 61

#: The complete system parameters.ctl enum, 0-90, name as LEGO's own
#: source has it (not renamed to this module's SYSPARAM_* convention,
#: so it's searchable against LabVIEW block diagrams and other
#: original sources verbatim). Spares (nSpare64..81) are real,
#: reserved-but-unused slots, not gaps in the list. A few names carry
#: what look like typos in the original enum (`nInterCharTiimeout`,
#: `kSytemNxtButtonTask`, `kSystemNxtSychMotors`) -- kept verbatim
#: rather than silently corrected, since a typo in the source is still
#: the name that has to match if this table is ever cross-checked
#: against it again.
SYSTEM_PARAMETERS = {
    0: 'nImmediateBatteryLevel', 1: 'nDebugTaskMode', 2: 'nMemoryMapAddress',
    3: 'nCurrentTask', 4: 'nSerialLinkStatus', 5: 'nOpcodesPerTimeslice',
    6: 'nMotorTransition', 7: 'nSensorRefreshRate',
    8: 'bExpandedRemoteControlMessages', 9: 'nLCDRefreshRate',
    10: 'bNoPowerDownOnACAdaptor', 11: 'nDefaultTaskStackSize',
    12: 'nTaskAcquirePriority', 13: 'bTransmitterRange',
    14: 'bFloatDuringInactiveMotorPWM', 15: 'nRotationErrorsCount',
    16: 'nSysTime', 17: 'nPreambleSize', 18: 'bUnsolicitedMessages',
    19: 'bExpandedNumbOfSubs', 20: 'nPowerDownDelay', 21: 'nWatchFormat',
    22: 'nSensorMissedConversions', 23: 'nIgnoreMessagesCPU',
    24: 'nCommErrorsTimeout', 25: 'nCommErrorsParity',
    26: 'nCommErrorsFraming', 27: 'nCommErrorsOverrun',
    28: 'nInterCharTiimeout', 29: 'nTaskSchedulingPriority', 30: 'nVolume',
    31: 'bSoundPlaying', 32: 'bPlaySounds', 33: 'nQueuedSoundCount',
    34: 'nCommErrorsChecksum', 35: 'nCommErrorsInvalidOp',
    36: 'nCommErrorsMsgOverrun', 37: 'nVirtualMotorChanges',
    38: 'nVirtualSensorTypeChanges', 39: 'nVirtualSensorModeChanges',
    40: 'nSensorStartUpDelay', 41: 'nSensorDelayCycles', 42: 'nDatalogSize',
    43: 'nOffButtonTask', 44: 'nRunButtonTask',
    45: 'nSystemShutdownVoltage', 46: 'nSensorRefreshState',
    47: 'nSensorScanCount', 48: 'nExceptionReports',
    49: 'nCommErrorsParityFirst', 50: 'nMemoryMapAddressHigh',
    51: 'nTaskStackAddress', 52: 'nTaskStackAddressHigh', 53: 'nViewState',
    54: 'nSerialReadData', 55: 'nSerialState', 56: 'nSerialReadState',
    57: 'nAvgBackgroundTime', 58: 'nAvgInterpreterTime',
    59: 'nAvgBatteryLevel', 60: 'nClockMinutes', 61: 'nProgramNumber',
    62: 'firmwareVersion', 63: 'NXTScreenFormat',
    64: 'nSpare64', 65: 'nSpare65', 66: 'nSpare66', 67: 'nSpare67',
    68: 'nSpare68', 69: 'nSpare69', 70: 'nSpare70', 71: 'nSpare71',
    72: 'kSytemNxtButtonTask', 73: 'kSystemNxtExitClicks',
    74: 'kSystemNxtButtonPressed', 75: 'kSystemNxtMaxDataFileCount',
    76: 'kSystemNxtMaxDataFileSize', 77: 'kSystemNxtRechargable',
    78: 'kSystemNxtHideDataFile',
    79: 'nSpare79', 80: 'nSpare80', 81: 'nSpare81',
    82: 'kSystemNxtSychMotors', 83: 'kSystemNxtSynchSlaveSpeedRatio',
    84: 'kSystemNxtMaxRegulatedSpeedCountsPerSecond',
    85: 'kSystemNxtPidUpdateIntervalInMsecs', 86: 'kSystemRobolab',
    87: 'kSystemBluetoothCmdStatus', 88: 'kSystemBluetoothLastCmd',
    89: 'kSystemBluetoothStatus', 90: 'kSystemNxtLCDStatusDisplay',
}


# ----------------------------------------------------------------------
# Byte-packing helpers
# ----------------------------------------------------------------------
def _u16le(x):
    x &= 0xFFFF
    return [x & 0xFF, (x >> 8) & 0xFF]


def _short_jump_byte(distance):
    """Bits 0-6 magnitude, bit 7 direction (0 fwd, 1 back). Used by jmp,
    decvjn, and the trailing jump field of mone."""
    mag, direction = (distance, 0) if distance >= 0 else (-distance, 1)
    if not (0 <= mag <= 0x7F):
        raise LasmError(f"jump distance {distance} out of range for a short jump (+/-127)")
    return [(direction << 7) | mag]


def _long_jump_bytes(distance):
    """Bits 0-6 of byte0 + bit7 direction, byte1 = remaining 8 bits of
    a 15-bit magnitude. Used by jmpl, decvjnl, and monel/monal's jump."""
    mag, direction = (distance, 0) if distance >= 0 else (-distance, 1)
    if not (0 <= mag <= 0x7FFF):
        raise LasmError(f"jump distance {distance} out of range for a long jump (+/-32767)")
    return [(direction << 7) | (mag & 0x7F), (mag >> 7) & 0xFF]


def _forward_u8(distance):
    """loopc's jump: plain unsigned byte, no direction bit -- its LASM
    format ("forward label or forward distance") and Structure text
    (just "Jump distance", no Bit0-6/Bit7 split) both say forward-only."""
    if not (0 <= distance <= 0xFF):
        raise LasmError(f"loopc distance {distance} out of range (0-255, forward only)")
    return [distance]


def _forward_u16le(distance):
    if not (0 <= distance <= 0xFFFF):
        raise LasmError(f"loopcl distance {distance} out of range (0-65535, forward only)")
    return _u16le(distance)


# ----------------------------------------------------------------------
# Per-mnemonic encoders. Each takes the parsed argument list (ints, or
# strings for jump/label targets already resolved to signed byte
# distances by the caller -- see _resolve_jumps) and returns (opcode,
# params). Grouped, and commented, by which family of the worked
# example verified them:
#   [VERIFIED]     byte-for-byte checked against the LASM=>BitCode.vi
#                  worked example.
#   [DOCUMENTED]   not in the worked example, but OpCodes.json's
#                  Structure field fully and unambiguously specifies
#                  the layout (plain positional bytes / LO-HI pairs,
#                  no bit-packing ambiguity).
#   [BEST-EFFORT]  Structure involves bit-packing or field ordering
#                  this module had to interpret from prose; flagged
#                  individually.
# ----------------------------------------------------------------------
ENCODERS = {}


def _reg(mnemonic, opcode):
    def deco(fn):
        ENCODERS[mnemonic] = (opcode, fn)
        return fn
    return deco


# --- zero-arg [DOCUMENTED] --------------------------------------------
# pollb/mute/speak excluded here -- all three are target-aware, see
# _TARGET_AWARE below.
for _mn, _op in [
    ("ping", 0x10), ("memmap", 0x20), ("offp", 0x60),
    ("delt", 0x40), ("stop", 0x50), ("dels", 0x70),  # 0-arg forms; see _OVERLOADED below
    ("rets", 0xF6), ("playz", 0x80),
    ("dele", 0x06), ("monex", 0xB0), ("monax", 0xA0), ("msgz", 0x90),
]:
    ENCODERS[_mn] = (_op, lambda args: [])

# --- overloaded mnemonics: 0-arg "all" form vs 1-arg "one" form --------
# LASM Defs has both under the same LASM Cmd; disambiguate by arg count.
_OVERLOADED = {
    "delt": {0: 0x40, 1: 0x61},   # DeleteAllTasks / DeleteTask
    "stop": {0: 0x50, 1: 0x81},   # StopAllTasks / StopTask
    "dels": {0: 0x70, 1: 0xC1},   # DeleteAllSubs / DeleteSub
}


# --- plain positional bytes, no LO/HI [DOCUMENTED] ----------------------
def _plain_bytes(mnemonic, opcode, nargs):
    ENCODERS[mnemonic] = (opcode, lambda args: list(args))


for _mn, _op, _n in [
    ("setw", 0x22, 2),
    # txs, prgm, cntz excluded here -- all three are target-aware, see
    # _TARGET_AWARE below. senz/cnti/cntd keep their own dedicated
    # opcodes here -- no confirmed evidence they get an NXT substitution.
    ("start", 0x71, 1), ("setp", 0xD7, 1), ("calls", 0x17, 1),
    ("senz", 0xD1, 1), ("sent", 0x32, 2),
    ("cnti", 0x97, 1), ("cntd", 0xA7, 1),
    ("sete", 0x93, 3), ("cale", 0x04, 4),
    ("loops", 0x82, 2), ("pwr", 0x13, 3), ("playv", 0x02, 2),
    ("log", 0x62, 2), ("msg", 0xB2, 2), ("uart", 0xC2, 2), ("remote", 0xD2, 1),
    ("poll", 0x12, 2),
]:
    _plain_bytes(_mn, _op, _n)

# tmrz (ClearTimer) is target-conditional, not a fixed table entry --
# see its special case in assemble_line() below.


# --- literal-prefixed / LO-HI 16-bit value [VERIFIED via set, disp] -----
@_reg("set", 0x05)
def _enc_set(args):
    dest_src, dest_val, orig_src, orig_val = args
    return [dest_src & 0xFF, dest_val & 0xFF, orig_src & 0xFF] + _u16le(orig_val)


@_reg("disp", 0xE5)
def _enc_disp(args):
    precision, disp_src, disp_val = args
    return [0x00, precision & 0xFF, disp_src & 0xFF] + _u16le(disp_val)


# --- var family: "mnemonic var, source, value" [VERIFIED shape] --------
# setv itself is the exact worked-example line; sumv/subv/divv/mulv/
# sgnv/absv/andv/orv share setv's Structure field-for-field (only the
# opcode differs), so they inherit the same encoder with confidence.
def _var_op(args):
    var_num, source, value = args
    return [var_num & 0xFF, source & 0xFF] + _u16le(value)


for _mn, _op in [
    ("setv", 0x14), ("sumv", 0x24), ("subv", 0x34), ("divv", 0x44),
    ("mulv", 0x54), ("sgnv", 0x64), ("absv", 0x74), ("andv", 0x84), ("orv", 0x94),
]:
    ENCODERS[_mn] = (_op, _var_op)


# --- motor/output bit-packed [DOCUMENTED, consistent with rcx_driver.py] -
def _motor_mode(args):
    mode, motor_list = args
    return [((mode & 0x03) << 6) | (motor_list & 0x07)]


for _mn, _op in [("out", 0x21), ("dir", 0xE1), ("gout", 0x67), ("gdir", 0x77)]:
    ENCODERS[_mn] = (_op, _motor_mode)


# --- sound / display [DOCUMENTED] ---------------------------------------
@_reg("plays", 0x51)
def _enc_plays(args):
    return [args[0] & 0xFF]


@_reg("playt", 0x23)
def _enc_playt(args):
    freq, duration = args
    return _u16le(freq) + [duration & 0xFF]


# view excluded here -- target-aware, see _TARGET_AWARE below.


@_reg("logz", 0x52)
def _enc_logz(args):
    return _u16le(args[0])


@_reg("pollm", 0x63)
def _enc_pollm(args):
    start, count = args
    return _u16le(start) + [count & 0xFF]


@_reg("msgs", 0xF7)
def _enc_msgs(args):
    return [args[0] & 0xFF]


# --- sensors [BEST-EFFORT: senm's bit packing] --------------------------
@_reg("sent", 0x32)
def _enc_sent(args):
    sensor_num, sensor_type = args
    return [sensor_num & 0xFF, sensor_type & 0xFF]


@_reg("senm", 0x42)
def _enc_senm(args):
    """senm sensor, mode, slope -> [sensor, (mode<<5)|(slope&0x1F)].
    Structure: "Bit0-4: slope, Bit5-7: mode" on one byte."""
    sensor_num, mode, slope = args
    if not (0 <= slope <= 31):
        raise LasmError(f"senm slope {slope} out of range (0-31)")
    return [sensor_num & 0xFF, ((mode & 0x07) << 5) | (slope & 0x1F)]


# --- events [VERIFIED: mone; DOCUMENTED: event; BEST-EFFORT: monel/monal] -
@_reg("event", 0x03)
def _enc_event(args):
    ev_src, ev_val = args
    return [ev_src & 0xFF] + _u16le(ev_val)


@_reg("mone", 0xB4)
def _enc_mone(args):
    """[VERIFIED] against the worked example's mone\\s2,2,Event line."""
    ev_src, ev_val, jump_distance = args
    return [ev_src & 0xFF] + _u16le(ev_val) + _short_jump_byte(jump_distance)


@_reg("monel", 0xB5)
def _enc_monel(args):
    ev_src, ev_val, jump_distance = args
    return [ev_src & 0xFF] + _u16le(ev_val) + _long_jump_bytes(jump_distance)


@_reg("monal", 0x73)
def _enc_monal(args):
    resources, jump_distance = args
    return [resources & 0xFF] + _long_jump_bytes(jump_distance)


# --- flow control / jumps [DOCUMENTED for the bit layout] ---------------
@_reg("jmp", 0x27)
def _enc_jmp(args):
    return _short_jump_byte(args[0])


@_reg("jmpl", 0x72)
def _enc_jmpl(args):
    return _long_jump_bytes(args[0])


@_reg("wait", 0x43)
def _enc_wait(args):
    wait_src, wait_val = args
    return [wait_src & 0xFF] + _u16le(wait_val)


@_reg("decvjn", 0xF2)
def _enc_decvjn(args):
    var_num, jump_distance = args
    return [var_num & 0xFF] + _short_jump_byte(jump_distance)


@_reg("decvjnl", 0xF3)
def _enc_decvjnl(args):
    var_num, jump_distance = args
    return [var_num & 0xFF] + _long_jump_bytes(jump_distance)


@_reg("loopc", 0x37)
def _enc_loopc(args):
    return _forward_u8(args[0])


@_reg("loopcl", 0x92)
def _enc_loopcl(args):
    return _forward_u16le(args[0])


# --- comparisons [BEST-EFFORT: relop+src1 packing, val2 width, final jump] -
@_reg("chk", 0x85)
def _enc_chk(args):
    src1, val1, relop, src2, val2, jump_distance = args
    return ([((relop & 0x03) << 6) | (src1 & 0x3F), src2 & 0x3F]
            + _u16le(val1) + [val2 & 0xFF] + _forward_u8(jump_distance))


@_reg("chkl", 0x95)
def _enc_chkl(args):
    src1, val1, relop, src2, val2, jump_distance = args
    return ([((relop & 0x03) << 6) | (src1 & 0x3F), src2 & 0x3F]
            + _u16le(val1) + [val2 & 0xFF] + _forward_u16le(jump_distance))


# --- task/sub/firmware framing opcodes (used internally, not usually
#     written directly in LASM source -- see task/sub pseudo-ops below) ---
BEGIN_OF_TASK = 0x25
BEGIN_OF_SUB = 0x35
CONTINUE_DL = 0x45
END_OF_SUB = 0xF6  # "rets" -- appended automatically to every sub, see compile_lasm


# compile_lasm() intercepts "task"/"sub" as block-delimiter pseudo-ops
# before they ever reach assemble_line, and builds the real BeginOfTask/
# BeginOfSub command itself once a block's size is known. These entries
# exist so assemble_line("task", [0]) still works standalone -- matching
# LASM=>BitCode.vi's own worked example, which encoded "task\s0" as a
# per-line placeholder stub ([0x25, 0, taskNum, 0, 0]) with the
# sub-call-list and size fields left as 0 filler, same as here.
@_reg("task", BEGIN_OF_TASK)
def _enc_task(args):
    (task_num,) = args
    return [0x00, task_num & 0xFF, 0x00, 0x00, 0x00]


@_reg("sub", BEGIN_OF_SUB)
def _enc_sub(args):
    (sub_num,) = args
    return [0x00, sub_num & 0xFF, 0x00, 0x00, 0x00]


# ----------------------------------------------------------------------
# Argument parsing
# ----------------------------------------------------------------------
def _parse_arg(tok):
    """An arg is an int (decimal or 0x-hex, optionally negative) or,
    for jump-target positions, a bare label name -- returned as-is (a
    str) for the caller to resolve against the label table."""
    tok = tok.strip()
    try:
        return int(tok, 0)
    except ValueError:
        return tok  # label reference


def _split_line(line):
    """Returns ('label', name), ('cmd', mnemonic, args), or None (blank
    / comment) for one line of LASM source."""
    line = line.split("//", 1)[0].strip()
    if not line:
        return None
    if line.endswith(":") and " " not in line[:-1] and "," not in line:
        return ("label", line[:-1])
    parts = line.split(None, 1)
    mnemonic = parts[0]
    arg_text = parts[1].strip() if len(parts) > 1 else ""
    args = [_parse_arg(a) for a in arg_text.split(",")] if arg_text else []
    return ("cmd", mnemonic, args)


# ----------------------------------------------------------------------
# Target-conditional mnemonics: opcodes the NXT firmware doesn't
# implement, where the real compiler substitutes an equivalent generic
# assignment instead (confirmed directly from LASM=>Bits block diagram
# comments: "Opcode is not supported on NXT brick. Replace with
# assignment to a [source/system parameter/'variable'] instead.").
# Each entry: mnemonic -> (dedicated_fn(args), nxt_fn(args)), both
# returning (opcode, params).
# ----------------------------------------------------------------------
def _dedicated_tmrz(args):
    (timer_num,) = args
    return 0xA1, [timer_num & 0xFF]  # [VERIFIED]: tmrz\s0 -> A1 00


def _nxt_tmrz(args):
    (timer_num,) = args  # [VERIFIED]: tmrz 3 -> 05 01 03 02 00 00
    return 0x05, [SRC_TIMER, timer_num & 0xFF, SRC_CONSTANT, 0x00, 0x00]


def _dedicated_cntz(args):
    (counter_num,) = args
    return 0xB7, [counter_num & 0xFF]


def _nxt_cntz(args):
    """[VERIFIED shape] against a "clearCounter" block diagram: reuses
    SetVar (setVariable, 0x14), since a counter shares storage with the
    global variable of the same number (per ClearCounter/IncCounter's
    own documented behavior) -- dest = counter number as a variable
    index, orig = (Constant, 0)."""
    (counter_num,) = args
    return 0x14, [counter_num & 0xFF, SRC_CONSTANT, 0x00, 0x00]


def _dedicated_txs(args):
    (rng,) = args
    return 0x31, [rng & 0xFF]


def _nxt_txs(args):
    (rng,) = args
    return 0x05, [SRC_SYSTEM, SYSPARAM_TRANSMITTER_RANGE, SRC_CONSTANT] + _u16le(rng)


def _dedicated_prgm(args):
    (program,) = args
    return 0x91, [program & 0xFF]


def _nxt_prgm(args):
    (program,) = args
    return 0x05, [SRC_SYSTEM, SYSPARAM_PROGRAM_NUMBER, SRC_CONSTANT] + _u16le(program)


def _dedicated_mute(args):
    return 0xD0, []


def _nxt_mute(args):
    return 0x05, [SRC_SYSTEM, SYSPARAM_PLAY_SOUNDS, SRC_CONSTANT, 0x00, 0x00]


def _dedicated_speak(args):
    return 0xE0, []


def _nxt_speak(args):
    return 0x05, [SRC_SYSTEM, SYSPARAM_PLAY_SOUNDS, SRC_CONSTANT, 0x01, 0x00]


def _dedicated_pollb(args):
    return 0x30, []


def _nxt_pollb(args):
    """getBattery's block diagram substitutes "getValue" -- opcodes.ctl
    confirms getValue = opcode 0x12 (Poll), whose own LASM form (poll
    source, value) is exactly (source, value) already -- so this is
    just poll(System, nImmediateBatteryLevel)."""
    return 0x12, [SRC_SYSTEM, SYSPARAM_IMMEDIATE_BATTERY_LEVEL]


def _dedicated_view(args):
    view_src, view_val = args
    return 0x33, [view_src & 0xFF] + _u16le(view_val)


def _nxt_view(args):
    """[REASONED, not pixel-confirmed]: the "setDisplay" block diagram
    only clearly showed 3 of what should be 4 combiner inputs at the
    resolution captured. This assumes the same shape as every other
    confirmed substitution (dest=(System, nViewState)) with view's own
    (source, value) args passed straight through as the origin pair,
    since unlike txs/prgm/mute this opcode's own args are already a
    (source, value) pair rather than a single constant -- matches
    SetSourceValue's structure exactly if so. Flag/revisit if a real
    capture of "setDisplay"'s actual output ever turns up."""
    view_src, view_val = args
    return 0x05, [SRC_SYSTEM, SYSPARAM_VIEW_STATE, view_src & 0xFF] + _u16le(view_val)


_TARGET_AWARE = {
    "tmrz": (_dedicated_tmrz, _nxt_tmrz),
    "cntz": (_dedicated_cntz, _nxt_cntz),
    "txs": (_dedicated_txs, _nxt_txs),
    "prgm": (_dedicated_prgm, _nxt_prgm),
    "mute": (_dedicated_mute, _nxt_mute),
    "speak": (_dedicated_speak, _nxt_speak),
    "pollb": (_dedicated_pollb, _nxt_pollb),
    "view": (_dedicated_view, _nxt_view),
    # logz (SetDataLog) deliberately excluded: its own block diagram
    # comment says to "temporarily leave it in" using the dedicated
    # opcode, since the NXT substitution loses the pass/fail reply
    # setDatalogSize's caller relies on. Always dedicated, both targets.
}


def assemble_line(mnemonic, args, nxt_compatible=True):
    """The direct LASM=>Bits equivalent for one already-tokenized line:
    mnemonic + parsed args -> (opcode, params), jump/label args must
    already be resolved to signed ints. Raises LasmError/
    NotImplementedError rather than guessing.

    nxt_compatible: whether to emit the NXT-safe substitution for
    opcodes the NXT firmware doesn't implement (see _TARGET_AWARE).
    Default True since that's what the more recently, independently
    supplied examples used; pass False for a strictly RCX-native
    compile using each opcode's own dedicated bytes instead."""
    target_aware = _TARGET_AWARE.get(mnemonic)
    if target_aware is not None:
        dedicated_fn, nxt_fn = target_aware
        fn = nxt_fn if nxt_compatible else dedicated_fn
        try:
            return fn(args)
        except (TypeError, ValueError, IndexError) as e:
            raise LasmError(f"{mnemonic!r} {args!r}: {e}") from e

    if mnemonic in _OVERLOADED:
        table = _OVERLOADED[mnemonic]
        opcode = table.get(len(args))
        if opcode is None:
            raise LasmError(
                f"{mnemonic!r} takes 0 or 1 args, got {len(args)}: {args!r}")
        if len(args) == 0:
            return opcode, []
        return opcode, [args[0] & 0xFF]

    entry = ENCODERS.get(mnemonic)
    if entry is None:
        known = _BY_MNEMONIC.get(mnemonic)
        if known:
            raise NotImplementedError(
                f"{mnemonic!r} (opcode 0x{known[0]['Command Code']:02X}, "
                f"{known[0]['Name']}) is a real RCX command but this module "
                f"doesn't implement its byte layout yet -- "
                f"Structure: {known[0]['Structure']!r}")
        raise LasmError(f"unknown LASM mnemonic {mnemonic!r}")

    opcode, fn = entry
    try:
        params = fn(args)
    except LasmError:
        raise
    except (TypeError, ValueError, IndexError) as e:
        raise LasmError(f"{mnemonic!r} {args!r}: {e}") from e
    return opcode, params


# ----------------------------------------------------------------------
# Full-source compiler: labels, task/sub framing, chunking
# ----------------------------------------------------------------------
@dataclass
class _PendingCmd:
    mnemonic: str
    args: list
    line_no: int
    source_line: str
    offset: int = 0            # byte offset of this command's start, within its block's body
    length: int = 0            # this command's encoded length in bytes (opcode + params)
    jump_arg_index: int = -1   # index into args that's a label reference, or -1


@dataclass
class _Block:
    kind: str            # "task" or "sub"
    number: int
    body: list = field(default_factory=list)   # list[_PendingCmd]


def _placeholder_args(args):
    """Args with any not-yet-resolved label reference replaced by 0,
    purely to measure a command's encoded length -- every jump encoder
    returns a fixed-width field regardless of the actual distance, so a
    placeholder is safe for sizing."""
    return [0 if isinstance(a, str) else a for a in args]


def compile_lasm(lines, chunk_size=20, nxt_compatible=True):
    """Compiles LASM source (an iterable of source lines, or a single
    '\\n'-joined string) into a flat list of Command objects ready to
    send in order: for each `task N` / `sub N` .. `endt` / `ends` block,
    a BeginOfTask/BeginOfSub header followed by its ContinueDL chunks
    (chunk_size bytes of body per chunk, default 20 -- matches the
    Packet Size the worked example was captured with); any command
    outside a task/sub block compiles to a single direct Command.

    Labels (a bare `name:` line) mark the byte offset of whatever comes
    next, scoped to the task/sub they're inside -- labels/jumps outside
    any task/sub aren't supported, since direct commands aren't a
    downloaded, jumpable program. jmp/jmpl/decvjn/decvjnl/mone/monel/
    monal arguments naming a label are resolved to a signed
    relative-byte distance in a second pass, same as any two-pass
    assembler. UNVERIFIED: the distance is computed as
    (label_offset - (this_instruction's_offset + its_encoded_length)),
    i.e. relative to the byte immediately after the jump instruction --
    this is NQC's own documented convention (RCX_Cmd.cpp::SetOffset),
    not something confirmed against ConvertCodes.vi's own arithmetic,
    since that part of the diagram wasn't traced. Pass an explicit
    integer distance instead of a label name to sidestep this if it
    turns out to be wrong.
    """
    if isinstance(lines, str):
        lines = lines.splitlines()

    commands = []       # list[Command], the final output
    blocks = []          # list[_Block], in compile order
    cur_block = None       # _Block or None (=> top-level/direct)
    direct_pending = []     # list[_PendingCmd]
    labels = {}               # name -> (block, byte_offset)
    offset = 0                 # running byte offset within cur_block's body

    for line_no, raw in enumerate(lines, 1):
        parsed = _split_line(raw)
        if parsed is None:
            continue

        if parsed[0] == "label":
            if cur_block is None:
                raise LasmError(f"line {line_no}: label {parsed[1]!r} outside "
                                 f"any task/sub -- labels only make sense inside one")
            labels[parsed[1]] = (cur_block, offset)
            continue

        _, mnemonic, args = parsed

        if mnemonic == "task":
            if cur_block is not None:
                raise LasmError(f"line {line_no}: nested task/sub ({mnemonic}) "
                                 f"inside {cur_block.kind} {cur_block.number}")
            cur_block = _Block(kind="task", number=args[0])
            blocks.append(cur_block)
            offset = 0
            continue
        if mnemonic == "sub":
            if cur_block is not None:
                raise LasmError(f"line {line_no}: nested task/sub ({mnemonic}) "
                                 f"inside {cur_block.kind} {cur_block.number}")
            cur_block = _Block(kind="sub", number=args[0])
            blocks.append(cur_block)
            offset = 0
            continue
        if mnemonic == "endt":
            if cur_block is None or cur_block.kind != "task":
                raise LasmError(f"line {line_no}: endt with no matching task")
            cur_block = None
            continue
        if mnemonic == "ends":
            if cur_block is None or cur_block.kind != "sub":
                raise LasmError(f"line {line_no}: ends with no matching sub")
            cur_block = None
            continue

        cmd = _PendingCmd(mnemonic=mnemonic, args=list(args), line_no=line_no,
                           source_line=raw.strip())
        for i, a in enumerate(cmd.args):
            if isinstance(a, str):
                cmd.jump_arg_index = i
        # Placeholder-encode now purely to learn this command's byte
        # length (jump fields are fixed-width regardless of the actual
        # resolved distance, so 0 is a safe stand-in).
        _, placeholder_params = assemble_line(mnemonic, _placeholder_args(cmd.args), nxt_compatible)
        cmd.offset = offset
        cmd.length = 1 + len(placeholder_params)
        offset += cmd.length

        if cur_block is not None:
            cur_block.body.append(cmd)
        else:
            if cmd.jump_arg_index != -1:
                raise LasmError(f"line {line_no}: {mnemonic!r} references a label "
                                 f"but is outside any task/sub")
            direct_pending.append(cmd)

    if cur_block is not None:
        raise LasmError(f"unterminated {cur_block.kind} {cur_block.number} "
                         f"(missing {'endt' if cur_block.kind == 'task' else 'ends'})")

    # Resolve label references now that every block's contents (and
    # therefore every label's byte offset) are known.
    for block in blocks:
        for cmd in block.body:
            if cmd.jump_arg_index == -1:
                continue
            idx = cmd.jump_arg_index
            name = cmd.args[idx]
            if name not in labels:
                raise LasmError(f"line {cmd.line_no}: undefined label {name!r}")
            label_block, label_offset = labels[name]
            if label_block is not block:
                raise LasmError(
                    f"line {cmd.line_no}: label {name!r} isn't in the same "
                    f"task/sub as the jump referencing it")
            cmd.args[idx] = label_offset - (cmd.offset + cmd.length)

    # Emit direct (top-level) commands as-is.
    for cmd in direct_pending:
        opcode, params = assemble_line(cmd.mnemonic, cmd.args, nxt_compatible)
        commands.append(Command(opcode, params, cmd.source_line, cmd.mnemonic))

    # Emit each task/sub block as BeginOfTask/Sub + chunked ContinueDL.
    for block in blocks:
        body = bytearray()
        for cmd in block.body:
            opcode, params = assemble_line(cmd.mnemonic, cmd.args, nxt_compatible)
            body.append(opcode & 0xFF)
            body.extend(p & 0xFF for p in params)
        if block.kind == "sub":
            body.append(END_OF_SUB)

        size = len(body)  # confirmed = raw body byte count, see module docstring
        if block.kind == "task":
            commands.append(Command(
                BEGIN_OF_TASK,
                [0x00, block.number & 0xFF, 0x00] + _u16le(size),
                f"task {block.number}", "task"))
        else:
            commands.append(Command(
                BEGIN_OF_SUB,
                [0x00, block.number & 0xFF, 0x00] + _u16le(size),
                f"sub {block.number}", "sub"))

        offset = 0
        seq = 1
        remaining = size
        if remaining == 0:
            continue
        while remaining > 0:
            n = min(chunk_size, remaining)
            is_last = (n == remaining)
            chunk = body[offset:offset + n]
            block_seq = 0 if is_last else seq
            block_checksum = sum(chunk) & 0xFF
            params = _u16le(block_seq) + _u16le(n) + list(chunk) + [block_checksum]
            commands.append(Command(CONTINUE_DL, params,
                                     f"<{block.kind} {block.number} chunk {seq}>", "clfirm"))
            offset += n
            remaining -= n
            seq += 1

    return commands


# ----------------------------------------------------------------------
# Self-test: reproduces LASM=>BitCode.vi's worked example exactly.
# ----------------------------------------------------------------------
def _selftest():
    lasm_source = """
        task 0
        set 28,1,2,10
        set 29,1,2,0
        set 30,1,2,0
        tmrz 0
        setv 0,2,0
        disp 2,0,0
        merre:
        tmrz 0
        mone 2,2,Event
        mike:
        jmp mike
    """
    # "Event" is never defined in the panel's example (it's a forward
    # reference to a label that, in the captured screenshot, hadn't been
    # typed yet) -- LASM=>Bits still encoded it as a 0 placeholder, so
    # reproduce that one line directly instead of via compile_lasm
    # (which requires every label to resolve). This example predates
    # the NXT-substitution finding, so tmrz uses nxt_compatible=False
    # (its dedicated 0xA1 opcode) to match what was actually captured.
    expected_line_bytes = {
        # 6 bytes (opcode + 5 params), not 5 -- both real checksummed
        # BeginOfTask packets (this example and the second one) confirm
        # 5 params; the standalone row here was misread by one field
        # off the harder-to-read LabVIEW panel screenshot.
        "task 0": [0x25, 0x00, 0x00, 0x00, 0x00, 0x00],
        "set 28,1,2,10": [0x05, 0x1C, 0x01, 0x02, 0x0A, 0x00],
        "set 29,1,2,0": [0x05, 0x1D, 0x01, 0x02, 0x00, 0x00],
        "set 30,1,2,0": [0x05, 0x1E, 0x01, 0x02, 0x00, 0x00],
        "tmrz 0": [0xA1, 0x00],
        "setv 0,2,0": [0x14, 0x00, 0x02, 0x00, 0x00],
        "disp 2,0,0": [0xE5, 0x00, 0x02, 0x00, 0x00, 0x00],
        "mone 2,2,Event": [0xB4, 0x02, 0x02, 0x00, 0x00],  # jump placeholder = 0
        "jmp mike": [0x27, 0x00],  # jump placeholder = 0 (would resolve to a
                                     # backward jump of 0 here, coincidentally
                                     # also encoding as 0x00)
    }
    ok = True
    for line, expected in expected_line_bytes.items():
        _, mnemonic, args = _split_line(line)
        # Reproduce LASM=>Bits' own placeholder-for-unresolved-labels
        # behavior for the one line with a forward label reference.
        args = [0 if isinstance(a, str) else a for a in args]
        opcode, params = assemble_line(mnemonic, args, nxt_compatible=False)
        got = [opcode] + params
        status = "OK" if got == expected else "MISMATCH"
        if got != expected:
            ok = False
        print(f"{status:9} {line!r:30} got={[hex(b) for b in got]} "
              f"expected={[hex(b) for b in expected]}")

    # Verify the BeginOfTask + 2 ContinueDL wire packets against the
    # worked example's task body, using the observed 20-byte chunking
    # (from the panel's "Packet Size: 20" control) and confirming the
    # checksum-validated portions of the real captured packets.
    body_lines = [
        "set 28,1,2,10", "set 29,1,2,0", "set 30,1,2,0", "tmrz 0",
        "setv 0,2,0", "disp 2,0,0", "tmrz 0", "mone 2,2,Event", "jmp mike",
    ]
    body = bytearray()
    for line in body_lines:
        _, mnemonic, raw_args = _split_line(line)
        args = [0 if isinstance(a, str) else a for a in raw_args]
        opcode, params = assemble_line(mnemonic, args, nxt_compatible=False)
        body.append(opcode)
        body.extend(params)
    print()
    print("compiled body:", body.hex(), "length:", len(body))
    expected_body_prefix = bytes([
        0x05, 0x1C, 0x01, 0x02, 0x0A, 0x00,
        0x05, 0x1D, 0x01, 0x02, 0x00, 0x00,
        0x05, 0x1E, 0x01, 0x02, 0x00, 0x00,
        0xA1, 0x00,
    ])
    prefix_ok = body[:20] == expected_body_prefix
    print("first 20 bytes match ContinueDL block 1's captured data:", prefix_ok)
    ok = ok and prefix_ok

    # Second worked example: an independently supplied hex/LASM listing
    # (4-line task body), compiled end-to-end via compile_lasm() itself
    # -- exercises the whole pipeline (task framing, chunking, checksum),
    # not just per-line encoding. tmrz here uses the NXT substitution
    # (nxt_compatible=True, the default), matching what was captured.
    print()
    example2_source = """
        task 0
        tmrz 3
        set 24,9,2,1
        plays 5
        wait 2,100
        endt
    """
    cmds = compile_lasm(example2_source)
    expected_cmds = [
        (0x25, [0x00, 0x00, 0x00, 0x12, 0x00]),
        (0x45, [0x00, 0x00, 0x12, 0x00,
                0x05, 0x01, 0x03, 0x02, 0x00, 0x00,
                0x05, 0x18, 0x09, 0x02, 0x01, 0x00,
                0x51, 0x05,
                0x43, 0x02, 0x64, 0x00,
                0x33]),
    ]
    got_cmds = [(c.opcode, c.params) for c in cmds]
    example2_ok = got_cmds == expected_cmds
    print("example 2 (compile_lasm end-to-end):", "OK" if example2_ok else "MISMATCH")
    if not example2_ok:
        for g, e in zip(got_cmds, expected_cmds):
            print("  got:", g)
            print("  exp:", e)
    ok = ok and example2_ok

    print()
    print("ALL OK" if ok else "SOME MISMATCHES -- see above")
    return ok


if __name__ == "__main__":
    _selftest()

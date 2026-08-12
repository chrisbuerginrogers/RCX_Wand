# LEGO P-brick (RCX / Scout / NXT) LASM Opcode Reference

Reformatted from a flat JSON dump into grouped tables.

## Legend

| Field | Meaning |
|---|---|
| **Hex / Dec** | Command byte |
| **LASM** | Assembler mnemonic |
| **Params** | Bytes following the opcode |
| **Reply** | Reply opcode and number of reply bytes |
| **Flags** | `D` = valid as a *direct* (immediate) command · `P` = valid inside a *downloaded program*, then the targets: `R` RCX, `S` Scout, `C` CLI, `N` NXT |
| **Pg** | Page in the source SDK document (`—` = not given) |

`Crkt Cmd` was `false` for every single entry in the source data, so that column is omitted.

### The reply-byte rule

In almost every entry, the reply opcode is the **bitwise complement of the command byte**. The classic RCX commands show it with the toggle bit (`0x08`) masked off; the newer NXT-era entries show the exact complement.

```python
def reply_opcode(cmd, classic=True):
    r = (~cmd) & 0xFF
    return r & ~0x08 if classic else r

reply_opcode(0x10)          # 0xE7  ping
reply_opcode(0x20)          # 0xD7  memmap
reply_opcode(0xF8, False)   # 0x07  genericAdd
```

A few rows don't fit (e.g. `0xC5` is listed as replying `46` rather than `32`) — most likely errors in the original document.

### Known opcode collisions

The Scout-only set overlaps the RCX/NXT set in places. Watch out for:

- **131 / `0x83`** — `setfb` (Scout) vs `uploadGlobalVariables` (RCX/NXT)
- **226 / `0xE2`** — `vll` (Scout) vs `rcxDebugSuspendResumeStep` (RCX/NXT)
- **Code 0** — listed for three Scout commands (`cnts`, `scout`, `tmrs`); the source clearly didn't record their real opcodes.

---

## 1. System, power and status

| Hex | Dec | Name | LASM | Params | Reply | Flags | Pg | Description |
|---|---|---|---|---|---|---|---|---|
| 10 | 16 | PBAliveOrNot | `ping` | — | E7 (1) | D P / R S C N | 12 | Tests for the presence of a P-brick. As a direct command it also re-initialises the protocol toggle bit; as a program command it resets the power-down timer. |
| 20 | 32 | MemMap | `memmap` | — | D7 + address table (189) | D / R | 13 | Returns addresses of downloaded tasks, subroutines and the datalog area. Use to compute free memory or feed `UploadRam`. Reply layout: HI/LO for Sub00–Sub47, Task00–Task49, then Log, CurrentLog, MemUsed, MemTop. |
| 30 | 48 | PBBattery | `pollb` | — | C7 lo hi (3) | D / R N | 14 | Returns battery level × 1000. |
| 60 | 96 | PBTurnOff | `offp` | — | 97 (1) | D P / R S | 17 | Turns the RCX off by resetting the power-down timer to 0. |
| B1 | 177 | PBPowerDownTime | `tout` | Power-down time in minutes (0 = never) | 46 (1) | D P / R S N | 35 | Sets the power-down time and resets the timer. The timer resets on every received command and on every `PBAliveOrNot` in a program. Backed by a 1-second low-level timer that can't be reset, so expect some inaccuracy. |
| 31 | 49 | PbTXPower | `txs` | 0 = short range, 1 = long range | C6 (1) | D P / R S | 28 | Sets the IR transmission range. |
| 22 | 34 | SetWatch | `setw` | Hours; Minutes | D5 (1) | D P / R | 51 | Sets the internal watch. The Watch is selected and the new time appears on the LCD at the next display update. |
| 15 | 21 | UnlockPBrick | `pollp` | 1, 3, 5, 7, 11 | E2 + version bytes (9) | D / R S N | 87 | Returns firmware version info: ROM major HI/LO, ROM minor HI/LO, RAM major HI/LO, RAM minor HI/LO. |

## 2. Programs, tasks and subroutines

| Hex | Dec | Name | LASM | Params | Reply | Flags | Pg | Description |
|---|---|---|---|---|---|---|---|---|
| 40 | 64 | DeleteAllTasks | `delt` | — | B7 (1) | D / R S | 15 | Deletes all tasks in the selected program. Tasks are stopped and their access resources released first. |
| 50 | 80 | StopAllTasks | `stop` | — | A7 (1) | D P / R S C N | 16 | Stops all running tasks in the selected program and releases their access resources. |
| 70 | 112 | DeleteAllSubs | `dels` | — | 87 (1) | D / R S | 18 | Deletes all subroutines in the selected program. Tasks that call subroutines are stopped as a precaution and their resources released. |
| 61 | 97 | DeleteTask | `delt` | Task number (0–9) | 96 (1) | D / R S | 30 | Deletes one task; stops it and releases its resources if running. |
| 71 | 113 | StartTask | `start` | Task number (0–9) | 86 (1) | D P / R S C N | 31 | (Re)starts the given task if its slot is non-empty. |
| 81 | 129 | StopTask | `stop` | Task number (0–9) | 76 (1) | D P / R S C N | 32 | Stops the given task and releases its access resources. |
| 91 | 145 | SelectProgram | `prgm` | Program number (0–4) | 66 (1) | D P / R | 33 | Stops all tasks in the current program, then selects the given slot. **If run from inside a downloaded program, task 0 of the new program is started** — this allows program chaining and matches the LEGO remote control's behaviour. The same split behaviour appears when writing source 8 via `SetSourceValue`. |
| C1 | 193 | DeleteSub | `dels` | Subroutine number (0–7) | 36 (1) | D / R S | 36 | Deletes a subroutine and stops every task that might call it, releasing their resources. |
| D7 | 215 | SetPriority | `setp` | Task priority | — | P / R S N | 47 | Sets the task's priority. All 256 levels are usable; 0 is the highest priority. |
| 17 | 23 | Gosub | `calls` | Subroutine number (0–7) | — | P / R S N | 39 | Jumps to the start of the subroutine and saves the return address in the task. **Call only from a task** — calling it from inside another subroutine corrupts the return address and will eventually kill the original calling task. |
| F6 | 246 | EndOfSub | `rets` | — | — | P / R S N | 26 | Appended to every downloaded subroutine; can also be used mid-subroutine for an early return. Exits any event monitoring or access-control region opened inside the subroutine (releasing resources), then clears the return address. If called outside a subroutine, the interpreter stops the task and releases its resources. |
| 25 | 37 | BeginOfTask | `task` | 0; Task number; Subroutine call list; Task size LO/HI | D2 status (2) | D P / R S C N | 88 | Starts a task download. Status: 0 = OK, 1 = not enough memory, 2 = illegal task number. Expect a series of `ContinueDL` commands after a successful reply. No direct LASM syntax — generated during download. |
| 35 | 53 | BeginOfSub | `sub` | 0; Subroutine number; 0; Sub size LO/HI | C2 status (2) | D P / R N | 89 | Starts a subroutine download. Status: 0 = OK, 1 = not enough memory, 2 = illegal subroutine number. Generated during download. |

## 3. Motors and outputs

| Hex | Dec | Name | LASM | Params | Reply | Flags | Pg | Description |
|---|---|---|---|---|---|---|---|---|
| 21 | 33 | OnOffFloat | `out` | Bits 0–2 motor list; bits 6–7: float(0), off(1), on(2) | D6 (1) | D P / R S C N | 27 | Sets the Motor Status register. |
| E1 | 225 | SetFwdSetRwdRewDir | `dir` | Bits 0–2 motor list; bits 6–7: backwards(0), reverse(1), forwards(2) | 16 (1) | D P / R S C N | 38 | Sets the Motor Status register. |
| 67 | 103 | ConnectDisconnect | `gout` | Bits 0–2 motor list; bits 6–7: float(0), off(1), on(2) | 90 (1) | D P / R S N | 42 | Sets the **Global** Motor Status register. |
| 77 | 119 | SetNormSetInvAltDir | `gdir` | Bits 0–2 motor list; bits 6–7: backwards(0), reverse(1), forwards(2) | 80 (1) | D P / R S | 43 | Sets the **Global** Motor Status register. |
| 13 | 19 | SetPower | `pwr` | Motor list; Power source; Power value | E4 (1) | D P / R S C N | 64 | Sets the new power level in the Motor Status registers. |

## 4. Sound

| Hex | Dec | Name | LASM | Params | Reply | Flags | Pg | Description |
|---|---|---|---|---|---|---|---|---|
| 80 | 128 | ClearSound | `playz` | — | 77 (1) | D P / R N | 19 | Immediately empties the sound buffer of all queued tones and system sounds — useful for instant sound feedback on events. |
| D0 | 208 | MuteSound | `mute` | — | 27 (1) | D P / R N | 23 | Empties the sound buffer and ignores future interpreter-requested sounds. System sounds (button presses, low battery) still play. |
| E0 | 224 | UnmuteSound | `speak` | — | 17 (1) | D P / R N | 24 | Resumes processing of interpreter-requested sounds. |
| 51 | 81 | PlaySystemSound | `plays` | 0 key click, 1 beep, 2 sweep up, 3 sweep down, 4 error, 5 fast sweep up | A6 (1) | D P / R S N | 29 | Plays a system sound if sound is globally enabled. |
| 02 | 2 | PlayToneVar | `playv` | Variable number; Duration in 1/100 s | F5 (1) | D P / R S N | 49 | Uses the variable's contents as the frequency and queues tone + duration in the sound buffer. |
| 23 | 35 | PlayTone | `playt` | Frequency LO/HI; Duration | D4 (1) | D P / R S N | 65 | Queues a tone in the sound buffer, if enabled. |
| C5 | 197 | playToneVarDurationVar | `playToneVarDurationVar` | freq-parm; freq-index; duration-parm; duration-index | 46 (1) | D P / R S N | — | Plays a tone for a duration, with **both** frequency and duration evaluated at run time. |
| EA | 234 | Play sound file | `playsoundfile` | File name (e.g. `Woops.rso`) | 15 (1) | D P / N | — | Plays any `.rso` sound file from the sound folder. |

## 5. Sensors

| Hex | Dec | Name | LASM | Params | Reply | Flags | Pg | Description |
|---|---|---|---|---|---|---|---|---|
| D1 | 209 | ClearSensorValue | `senz` | Sensor number (0–2) | 26 (1) | D P / R C N | 37 | Resets the source-9 register for the sensor; the value is recomputed by normal sensor processing. Mainly useful for zeroing an angle sensor (1/16-turn ticks). |
| 32 | 50 | SetSensorType | `sent` | Sensor number; Type: 0 NoSensor, 1 Switch, 2 Temperature, 3 Reflection, 4 Angle | C5 (1) | D P / R C N | 52 | Sets sensor type, resetting previous state and info. Default modes: NoSensor→Raw, Switch→Boolean, Temperature→Celsius, Reflection→PctFullScale, Angle→AngleSteps. Use `SetSensorMode` afterwards for anything else. |
| 42 | 66 | SetSensorMode | `senm` | Sensor number; bits 0–4 slope (0 = absolute, 1–31 = dynamic); bits 5–7 mode: 0 Raw, 1 Boolean, 2 TransitionCnt, 3 PeriodCounter, 4 PctFullScale, 5 Celsius, 6 Fahrenheit, 7 AngleSteps | B5 (1) | D P / R C N | 53 | Sets mode and measurement slope, and resets the processed sensor value. |

## 6. Timers and counters

| Hex | Dec | Name | LASM | Params | Reply | Flags | Pg | Description |
|---|---|---|---|---|---|---|---|---|
| A1 | 161 | ClearTimer | `tmrz` | Timer number (0–3) | 56 (1) | D P / R S C N | 34 | Resets the given system timer to zero. |
| 97 | 151 | IncCounter | `cnti` | Counter number (0–2) | 60 (1) | D P / R S | 44 | Increments the counter (same storage as the global variable of that number), then checks for events. |
| A7 | 167 | DecCounter | `cntd` | Counter number (0–2) | 50 (1) | D P / R S | 45 | Decrements the counter, then checks for events. |
| B7 | 183 | ClearCounter | `cntz` | Counter number (0–2) | 40 (1) | D P / R S | 46 | Resets the counter, then checks for events. |
| B6 | 182 | UploadTimers | `UploadTimers` | — | 49 | D P / R N | — | Uploads the current value of all timers. |

## 7. Events

| Hex | Dec | Name | LASM | Params | Reply | Flags | Pg | Description |
|---|---|---|---|---|---|---|---|---|
| 06 | 6 | ClearAllEvents | `dele` | — | F1 (1) | D P / R N | 25 | Clears all 16 events. Tasks currently waiting on events are **not** notified. |
| B0 | 176 | ExitEventCheck | `monex` | — | — | P / R S N | 22 | Stops event monitoring for the task, if any is running. |
| 03 | 3 | DirectEvent | `event` | Event source; Event value LO/HI | F4 (1) | D P / R S N | 63 | Forces the firmware to behave as if the events whose bits are set in the computed 16-bit value had occurred. |
| 93 | 147 | SetEvent | `sete` | Event number (0–15); Event sensor (0–2 Inputs 1–3 / 3–6 Timers 0–3 / 7 Mailbox / 8–10 Counters 0–2); Event type | 64 (1) | D P / R N | 70 | Starts monitoring for the event type on that sensor and sets the event bit when it fires. Types: 0 Pressed, 1 Released, 2 Period, 3 Transition, 7 Change rate, 8 Enter low, 9 Enter normal, 10 Enter high, 11 Click, 12 Double click, 14 Mailbox, **16 = delete/clear the event**. |
| 04 | 4 | CalibrateEvent | `cale` | Event number; Lower threshold %; Upper threshold %; Hysteresis % | F3 (1) | D P / R | 74 | Sets thresholds and hysteresis from live sensor measurements and the current mode. Averages 8 measurements for physical sensors, 1 for virtual sensors. **The program must be halted long enough for the firmware to calibrate.** Not useful for switch-sensor events. See the "Events" chapter (p. 100) for the maths. |
| B4 | 180 | SEnterEventCheck | `mone` | Event source; Event value LO/HI; bits 0–6 jump distance, bit 7 direction (0 fwd / 1 back) | — | P / R S N | 85 | Asks the firmware to notify the task when any of the given events occur. Execution continues at the given address and monitoring is suspended. Read source 23 afterwards to tell which event fired. |
| B5 | 181 | LEnterEventCheck | `monel` | Event source; Event value LO/HI; jump distance LO (bit 7 = direction) / HI | — | P / R S N | 96 | Long-jump form of `mone`. |
| 9C | 156 | signalEvent | `signalEvent` | src1, val1, src2, val2 | — | P / R N | — | *(No description in source.)* |
| 9D | 157 | resetEvent | `resetEvent` | src1, val1, src2, val2 | — | P / R N | — | *(No description in source.)* |

## 8. Access control

| Hex | Dec | Name | LASM | Params | Reply | Flags | Pg | Description |
|---|---|---|---|---|---|---|---|---|
| 73 | 115 | EnterAccessControl | `monal` | Resources (0x01 Motor A, 0x02 Motor B, 0x04 Sound, 0x08 Motor C — OR together); jump distance LO (bit 7 = direction) / HI | — | P / R S C N | 69 | Tries to acquire the requested resources at the task's priority. On success execution continues; on failure — or if a higher-priority task later claims them — execution jumps to the given address. The odd resource numbering gives Scout compatibility (its VLL diode acts as a third motor). Eight resource bits are supported, so you can implement priority-based access to your own virtual resources — but the state machine can't know about those, so **you** must release them when access is revoked. |
| A0 | 160 | ExitAccessControl | `monax` | — | — | P / R S N | 21 | Exits the access-control region if the task is in one. Resources are left in whatever state the task put them — the command doesn't change their setup. |

## 9. Flow control, jumps and loops

| Hex | Dec | Name | LASM | Params | Reply | Flags | Pg | Description |
|---|---|---|---|---|---|---|---|---|
| 27 | 39 | SJump | `jmp` | Bits 0–6 jump distance; bit 7 direction (0 fwd / 1 back) | — | P / R S C N | 40 | Short relative jump. `jmp 0` gives an infinite loop. |
| 72 | 114 | LJump | `jmpl` | Jump distance LO (bit 7 = direction) / HI | — | P / R S C N | 56 | Long relative jump. `jmpl 0` gives an infinite loop. |
| 43 | 67 | Wait | `wait` | Wait source; Wait value LO/HI | — | P / R S C N | 67 | Suspends the task for N × 10 ms. Negative values are ignored. |
| 47 | 71 | rcxWaitTimer1MSec | `WAIT1MS` | Wait source; Wait value | B8 | P / R N | — | Waits for the timer given by *wait source* to run for *wait value*. |
| F2 | 242 | SDecVarJumpLTZero | `decvjn` | Variable number; bits 0–6 jump distance, bit 7 direction | — | P / R S C N | 62 | Decrements the variable and jumps if it is then negative. **Preferred loop construct** — it uses ordinary variables rather than hidden interpreter state, so the counter can be read and manipulated for conditional exits. |
| F3 | 243 | LDecVarJumpLTZero | `decvjnl` | Variable number; jump distance LO (bit 7 = direction) / HI | — | P / R S C N | 73 | Long-jump form of `decvjn`. |
| 37 | 55 | SCheckLoopCounter | `loopc` | Jump distance (forward) | — | P / R C N | 41 | If the current loop counter is 0, pops the loop level and jumps forward; otherwise decrements it. Ignored if no loop level is active. ⚠️ **Not recommended** — relies on internal state that can be corrupted by firmware events or access-control revocation. Use `decvjn` instead. |
| 92 | 146 | LCheckLoopCounter | `loopcl` | Jump distance LO/HI | — | P / R | 58 | Long-jump form of `loopc`. Same warning applies. |
| 82 | 130 | SetLoopCounter | `loops` | Loop counter source; Loop counter value | — | P / R | 57 | Increments the loop nesting level and sets that level's counter. Only 4 levels exist; the command is ignored if all are in use. Values outside 1–255 become 0, which makes the next `loopc` exit immediately. ⚠️ Same warning as above. |
| 85 | 133 | SCheckDo | `chk` | Bits 0–5 source 1 + bits 6–7 comparison (0 >, 1 <, 2 ==, 3 !=); bits 0–5 source 2; value 1 LO/HI; value 2; jump distance | — | P / R S C N | 93 | If the comparison is false, jumps to the given address. Makes for efficient switch/case: a failed match jumps forward to the next check. |
| 95 | 149 | LCheckDo | `chkl` | As `chk`, with jump distance LO/HI | — | P / R S C N | 94 | Long-jump form of `chk`. |
| 3F | 63 | LCheckDoFloat | `chklf` | Byte 0: bits 6–7 condition, bits 0–5 left type; bytes 1–2 left value LO/HI; byte 3 right type; bytes 4–5 right value LO/HI; bytes 6–7 branch offset LO/HI | — | P / R N | — | Compares two floating-point numbers and computes the jump (Swan opcode). |
| 3D | 61 | LCheckDoLong | `chkll` | Same layout as `chklf` | — | P / R N | — | Compares two long values and computes the jump (Swan opcode). |
| 9E | 158 | switchIndexTableNear | `switchIndexTableNear` | src1, val1, src2, val2 | — | P / R N | — | *(No description in source.)* |
| 9F | 159 | switchIndexTableFar | `switchIndexTableFar` | src1, val1, src2, val2 | — | P / R N | — | *(No description in source.)* |
| D4 | 212 | switchByteCase | `switchByteCase` | src1, val1, src2, val2 | — | P / R N | — | *(No description in source.)* |

## 10. Variables and 16-bit maths

| Hex | Dec | Name | LASM | Params | Reply | Flags | Pg | Description |
|---|---|---|---|---|---|---|---|---|
| 14 | 20 | SetVar | `setv` | Variable number; Source; Value LO/HI | E3 (1) | D P / R S C N | 75 | `var = value` |
| 24 | 36 | SumVar | `sumv` | Variable number; Source; Value LO/HI | D3 (1) | D P / R S C N | 76 | `var += value` |
| 34 | 52 | SubVar | `subv` | Variable number; Source; Value LO/HI | C3 (1) | D P / R S C N | 77 | `var -= value` |
| 44 | 68 | DivVar | `divv` | Variable number; Source; Value LO/HI | B3 (1) | D P / R S C N | 78 | `var /= value`. Ignored if the divisor is zero. |
| 54 | 84 | MulVar | `mulv` | Variable number; Source; Value LO/HI | A3 (1) | D P / R S C N | 79 | `var *= value` |
| 64 | 100 | SgnVar | `sgnv` | Variable number; Source; Value LO/HI | 93 (1) | D P / R S C N | 80 | `var = sign(value)` → −1, 0 or +1. |
| 74 | 116 | AbsVar | `absv` | Variable number; Source; Value LO/HI | 83 (1) | D P / R S C N | 81 | `var = abs(value)` |
| 84 | 132 | AndVar | `andv` | Variable number; Source; Value LO/HI | 73 (1) | D P / R S C N | 82 | `var &= value` (bitwise) |
| 94 | 148 | OrVar | `orv` | Variable number; Source; Value LO/HI | 63 (1) | D P / R S C N | 83 | `var \|= value` (bitwise) |
| 05 | 5 | SetSourceValue | `set` | Bits 0–5 dest source; dest value; bits 0–5 origin source; value LO/HI | F2 (1) | D P / R C N | 86 | General-purpose write to **any** writable source in the RCX. Introduced to avoid a proliferation of specialised commands for new event-monitoring sources. |
| 2F | 47 | assignSourceValue | `assignSourceValue` | src1, val1, src2, val2 | D0 | D P / R N | — | 16-bit assign: `src1,val1 = src2,val2` |
| F8 | 248 | genericAdd | `genericAdd` | src1, val1, src2, val2 | 07 | D P / R N | — | 16-bit `+=` |
| F9 | 249 | genericMinus | `genericMinus` | src1, val1, src2, val2 | 06 | D P / R N | — | 16-bit `-=` |
| FA | 250 | genericTimes | `genericTimes` | src1, val1, src2, val2 | 05 | D P / R N | — | 16-bit `*=` |
| FB | 251 | genericDivide | `genericDivide` | src1, val1, src2, val2 | 04 | D P / R N | — | 16-bit `/=` |
| FC | 252 | genericAnd | `genericAnd` | src1, val1, src2, val2 | 03 | D P / R N | — | 16-bit `&=` |
| FD | 253 | genericOr | `genericOr` | src1, val1, src2, val2 | 02 | D P / R N | — | 16-bit `\|=` |
| FE | 254 | genericComp | `genericComp` | src1, val1, src2, val2 | 01 | D P / R N | — | 16-bit bit complement: `src1,val1 = ~src2,val2` |
| FF | 255 | genericMod | `genericMod` | src1, val1, src2, val2 | 00 | D P / R N | — | 16-bit `%=` |
| F5 | 245 | moduloTo | `moduloTo` | src1, val1, src2, val2 | — | P / R N | — | *(No description in source.)* |
| 2E | 46 | bitComplement | `bitComplement` | src1, val1, src2, val2 | — | P / R N | — | *(No description in source.)* |
| 7D | 125 | bitTest | `bitTest` | src1, val1, src2, val2 | — | P / R N | — | *(No description in source.)* |
| 7E | 126 | bitSet | `bitSet` | src1, val1, src2, val2 | — | P / R N | — | *(No description in source.)* |
| 7F | 127 | bitClear | `bitClear` | src1, val1, src2, val2 | — | P / R N | — | *(No description in source.)* |
| 98 | 152 | shiftLeftTo | `shiftLeftTo` | src1, val1, src2, val2 | — | P / R N | — | *(No description in source.)* |
| 99 | 153 | shiftRightTo | `shiftRightTo` | src1, val1, src2, val2 | — | P / R N | — | *(No description in source.)* |
| 9B | 155 | negate | `negate` | src1, val1, src2, val2 | — | P / R N | — | *(No description in source.)* |

### Global variables

| Hex | Dec | Name | LASM | Params | Reply | Flags | Pg | Description |
|---|---|---|---|---|---|---|---|---|
| 2D | 45 | setGlobalVar | `setGlobalVar` | src1, val1, src2, val2 | — | P / R N | — | *(No description in source.)* |
| C8 | 200 | addToGlobal | `addToGlobal` | src1, val1, src2, val2 | — | P / R N | — | *(No description in source.)* |
| C9 | 201 | minusToGlobal | `minusToGlobal` | src1, val1, src2, val2 | — | P / R N | — | *(No description in source.)* |
| CA | 202 | timesToGlobal | `timesToGlobal` | src1, val1, src2, val2 | — | P / R N | — | *(No description in source.)* |
| CB | 203 | divideToGlobal | `divideToGlobal` | src1, val1, src2, val2 | — | P / R N | — | *(No description in source.)* |
| CC | 204 | andToGlobal | `andToGlobal` | src1, val1, src2, val2 | — | P / R N | — | *(No description in source.)* |
| CD | 205 | orToGlobal | `orToGlobal` | src1, val1, src2, val2 | — | P / R N | — | *(No description in source.)* |
| CE | 206 | bitCompToGlobal | `bitCompToGlobal` | src1, val1, src2, val2 | — | P / R N | — | *(No description in source.)* |
| CF | 207 | moduloToGlobal | `moduloToGlobal` | src1, val1, src2, val2 | — | P / R N | — | *(No description in source.)* |
| 83 | 131 | uploadGlobalVariables | `uploadGlobalVariables` | Start var; End var (0–255) | 7C (33) | D P / R N | — | Uploads global variable values from *start var* through *end var*. ⚠️ Same opcode as the Scout's `setfb`. |

## 11. 32-bit long and float maths (Swan / NXT extensions)

| Hex | Dec | Name | LASM | Reply | Flags | Description |
|---|---|---|---|---|---|---|
| 26 | 38 | assignLong | `assignLong` | D9 | D P / R N | 32-bit assign (long or float): `src1,val1 = src2,val2` |
| 29 | 41 | assignLongConst | `assignLongConst` | — | P / R N | *(No description in source.)* |
| 38 | 56 | addToFloat | `addToFloat` | C7 | D P / R N | 32-bit float `+=` |
| 39 | 57 | minusToFloat | `minusToFloat` | C6 | D P / R N | 32-bit float `-=` |
| 3A | 58 | timesToFloat | `timesToFloat` | C5 | D P / R N | 32-bit float `*=` |
| 3B | 59 | divideToFloat | `divideToFloat` | C4 | D P / R N | 32-bit float `/=` |
| 18 | 24 | addToLong | `addToLong` | — | P / R N | 32-bit long `+=` |
| 19 | 25 | minusToLong | `minusToLong` | — | P / R N | 32-bit long `-=` |
| 1A | 26 | timesToLong | `timesToLong` | — | P / R N | 32-bit long `*=` |
| 1B | 27 | divideToLong | `divideToLong` | — | P / R N | 32-bit long `/=` |
| 1C | 28 | andToLong | `andToLong` | — | P / R N | 32-bit long `&=` |
| 1D | 29 | orToLong | `orToLong` | — | P / R N | 32-bit long `\|=` |
| 1E | 30 | bitCompToLong | `bitCompToLong` | — | P / R N | 32-bit long bit complement |
| 1F | 31 | moduloToLong | `moduloToLong` | — | P / R N | 32-bit long `%=` |
| 68 | 104 | lngAddConst | `lngAddConst` | — | P / R N | Long, constant operand |
| 69 | 105 | lngSubConst | `lngSubConst` | — | P / R N | Long, constant operand |
| 6A | 106 | lngMulConst | `lngMulConst` | — | P / R N | Long, constant operand |
| 6B | 107 | lngDivConst | `lngDivConst` | — | P / R N | Long, constant operand |
| 6C | 108 | lngAndConst | `lngAndConst` | — | P / R N | Long, constant operand |
| 6D | 109 | lngOrConst | `lngOrConst` | — | P / R N | Long, constant operand |
| 6E | 110 | lngCompConst | `lngCompConst` | — | P / R N | Long, constant operand |
| 6F | 111 | lngModConst | `lngModConst` | — | P / R N | Long, constant operand |
| 8C | 140 | floatToLong | `floatToLong` | — | P / R N | Type conversion |
| 8D | 141 | intToLong | `intToLong` | — | P / R N | Type conversion |
| AC | 172 | intToFloat | `intToFloat` | — | P / R N | Type conversion |
| AD | 173 | longToFloat | `longToFloat` | — | P / R N | Type conversion |
| AE | 174 | floatToInt | `floatToInt` | — | P / R N | Type conversion |
| AF | 175 | longToInt | `longToInt` | — | P / R N | Type conversion |
| E7 | 231 | transcendentalFunctions | `transcendentalFunctions` | — | P / R N | *(No description in source.)* |

All take the same `src1, val1, src2, val2` argument shape.

## 12. Display

| Hex | Dec | Name | LASM | Params | Reply | Flags | Pg | Description |
|---|---|---|---|---|---|---|---|---|
| 33 | 51 | SelectDisplay | `view` | View source; View value LO/HI | C4 (1) | D P / R C N | 66 | Selects what the LCD tracks: 0 Watch, 1–3 Inputs 1–3, 4–6 Outputs A–C, 7 User Selection (see `disp`). |
| E5 | 229 | ViewSourceValue | `disp` | 0; Display precision 0–3 (decimal point position); Display source; Display value LO/HI | 12 (1) | D P / R C | 97 | Sets the LCD's user option: the display tracks the given source with the given decimal point position. Very useful for debugging or surfacing application state that would otherwise need sounds or polling. **Local variables cannot be displayed.** |
| A3 | 163 | NXTLCDDisplay | `NXTLCDDisplay` | Draw command (variable length) | 54 (1) | P / N | — | Draws on the NXT screen. |
| C6 | 198 | setNXTUserTextDisplay | `setNXTUserTextDisplay` | src1, val1, src2, val2 | — | P / R N | — | *(No description in source.)* |

## 13. Datalog

| Hex | Dec | Name | LASM | Params | Reply | Flags | Pg | Description |
|---|---|---|---|---|---|---|---|---|
| 52 | 82 | SetDataLog | `logz` | Datalog size LO/HI | A5 (2) | D P / R C N | 54 | Clears the log and allocates a new one. One extra element is allocated to hold the point count; since it's immediately in use the count starts at 1 and its type field is `0xFF` to mark it. Real data points therefore start at index 1. |
| 62 | 98 | DataLogNext | `log` | Datalog source; Datalog value | 95 (1) | D P / R C N | 55 | Stores a 3-byte point: bits 0–4 source index, bits 5–7 source type (0 variable, 1 timer, 2 sensor value, 4 watch), then value LO/HI. Ignored if the log is full. **Task-local variables can't be logged** — their index exceeds the 5-bit (0–31) field. |
| A4 | 164 | Upload | `polld` | Datalog start LO/HI; Datalog size LO/HI | 53 + points | D / R N | 84 | Extracts data points (start and size are in *points*, not bytes) and returns them. Entry index 0 has type `0xFF` and its value is the number of valid points. |
| 7A | 122 | datalogNextEnhanced | `datalogNextEnhanced` | src1, val1, src2, val2 | — | P / R N | — | *(No description in source.)* |
| 07 | 7 | SaveDataLogFile | `savedata` | — | — | P / N | — | Writes the current datalog buffer to a file in flash RAM. |

## 14. Messaging, IR and comms

| Hex | Dec | Name | LASM | Params | Reply | Flags | Pg | Description |
|---|---|---|---|---|---|---|---|---|
| 90 | 144 | ClearPBMessage | `msgz` | — | 67 (1) | D P / R S N | 20 | Resets the IR message buffer to 0. |
| F7 | 247 | InternMessage | `msgs` | IR message (non-zero) | — | D P / R S N | 48 | Sets the IR message buffer to the given value, then checks for events. |
| B2 | 178 | SendPBMessage | `msg` | Message source; Message value | — | P / R S N | 59 | Sends the low 8 bits of the value as an IR message (`InternMessage`, `0xF7`) for other RCXs or Scouts to receive. |
| C2 | 194 | SendUARTData | `uart` | Data start; Data size | — | D P / R | 60 | Reads the comms setup from UART source (33) and sends data from that buffer. **`start + size <= 16` must hold** to avoid buffer overrun. With the right data-format (`0x03`) and transmit-control (`0x00`) parameters you can hand-assemble IR commands to other RCXs — mind the toggle bit and the other brick's reply. Do so at your own risk. |
| D2 | 210 | RemoteCommand | `remote` | Remote command LO: 0x01 Motor C backwards, 0x02–0x20 Programs 1–5, 0x40 stop program & motors, 0x80 remote sound | — | D / R S | 61 | Motor commands are interpreted continuously; the rest are one-shot. Send `remote 0` between commands to clear internal buffers. |
| A2 | 162 | setMsgByteParm | `setMsgByteParm` | src1, val1, src2, val2 | — | P / R N | — | *(No description in source.)* |
| F0 | 240 | SetMessage2WordParm | `SetMessage2WordParm` | src1, val1, src2, val2 | — | P / R N | — | *(No description in source.)* |
| F1 | 241 | SetMessage3WordParm | `SetMessage3WordParm` | src1, val1, src2, val2 | — | P / R N | — | *(No description in source.)* |
| 53 | 83 | SendI2CMsg | `SendI2CMsg` | — | — | D P / N | — | Sends an I²C message. |
| 55 | 85 | ReadI2CMsg | `ReadI2CMsg` | — | — | D P / N | — | Reads an I²C message. |

## 15. Memory, polling, download and firmware

| Hex | Dec | Name | LASM | Params | Reply | Flags | Pg | Description |
|---|---|---|---|---|---|---|---|---|
| 12 | 18 | Poll | `poll` | Poll source; Poll value | E5 lo hi (3) | D / R C N | 50 | Reads the given source/value pair and returns it. **Task-local variables can't be polled.** |
| 63 | 99 | UploadRam | `pollm` | RAM address LO/HI; Byte count | 94 + bytes | D / R S | 68 | Uploads raw memory from `0x8000`–`0xFD80`. Max 150 bytes per call because the IR tower shuts down automatically. |
| D6 | 214 | uploadDeviceVariables | `uploadDeviceVariables` | — | 29 (19) | D / R N | — | Uploads RCX device variables (light sensor values, motor status, etc.). |
| 45 | 69 | ContinueDL | `clfirm` | Block count LO/HI; Byte count LO/HI; Data bytes; Block checksum | B2 status (2) | D / R | 90 | Continues a download. Blocks are numbered 1, 2, …, n, 0 so the brick knows when the host thinks it's finished. Block checksum = sum of data bytes mod 256. Status: 0 OK, 3 block checksum error, 4 firmware checksum error, 6 download not active. |
| 75 | 117 | BeginFirmwareDownLoad | `bfirm` | Start address LO/HI; Checksum LO/HI; 0 | 82 status (2) | D / R | 92 | Start address is always `0x8000`. Firmware checksum = sum of the first 19456 bytes (19 KB) mod 65536. Status 0 = OK; a series of `ContinueDL` calls follows. |
| 65 | 101 | GoIntoBootMode | `reset` | 1, 3, 5, 7, 11 | 92 (1) | D / R | 91 | Puts the firmware into boot mode, where it responds to very few commands and awaits a new firmware download. |
| A5 | 165 | UnlockFirmware | `boot` | `0x4C 0x45 0x47 0x4F 0xAE` ("LEGO®") | 52 + text (26) | D / R S C | 95 | If valid firmware has been downloaded, starts it. Replies with a short LEGO easter-egg string. |

## 16. Debugging and breakpoints

| Hex | Dec | Name | LASM | Reply | Flags | Description |
|---|---|---|---|---|---|---|
| 01 | 1 | debugGetStatus | `debugGetStatus` | FE | D P / R N | Debug: get status |
| 16 | 22 | debugClearException | `debugClearException` | E9 | D P / R N | Debug: clear exception |
| E2 | 226 | rcxDebugSuspendResumeStep | `rcxDebugSuspendResumeStep` | 1D | D P / R N | Debug: suspend, resume or single-step. ⚠️ Same opcode as the Scout's `vll`. |
| E4 | 228 | rcxDebugSetBreakpoint | `DBSETBRK` | 1B | D P / R N | Debug: set breakpoint |
| F4 | 244 | DebugSetProgramCounter | `DebugSetProgramCounter` | 0B | D P / R N | Debug: set program counter |
| E6 | 230 | GetProgramDataBytesBig | `GetProgramDataBytesBig` | 19 | D P / R N | *(No description in source.)* |
| E8 | 232 | Breakpoint0 | `Breakpoint0` | 17 | D P / R N | Sets program breakpoint 2 |
| E9 | 233 | Breakpoint1 | `Breakpoint1` | 16 | D P / R N | Sets program breakpoint 3 |
| EB | 235 | Breakpoint3 | `Breakpoint3` | 14 | D P / R N | Sets program breakpoint 5 |
| EC | 236 | Breakpoint4 | `Breakpoint4` | 13 | D P / R N | Sets program breakpoint 6 |
| ED | 237 | Breakpoint5 | `Breakpoint5` | 12 | D P / R N | Sets program breakpoint 7 |
| EE | 238 | Breakpoint6 | `Breakpoint6` | 11 | D P / R N | Sets program breakpoint 8 |
| EF | 239 | Breakpoint7 | `Breakpoint7` | 10 | D P / R N | Sets program breakpoint 9 |

> Note the off-by-two naming in the source: `Breakpoint0` is documented as setting "breakpoint 2". `Breakpoint2` (`0xEA`) is absent — that opcode is used by the NXT `playsoundfile` command instead.

## 17. Scout-only commands

None of these reply, and all are Scout-only.

| Hex | Dec | Name | LASM | Params | Flags | Pg | Description |
|---|---|---|---|---|---|---|---|
| — | 0 | scout | `scout number` | 0 = Stand Alone mode, 1 = Power mode | / S | 42 | Selects Stand Alone (SA) or Power mode. |
| D5 | 213 | Rules | `rules` | motion, touch, light, time, fx | D P / S | 42 | Selects the motion, touch, light, time and FX rules used in Stand Alone mode. See "Inside the Scout: Basic functionality" for legal ranges. |
| 87 | 135 | Light | `light onoff` | 0 or 1 | D P / S | 38 | Turns the VLL output (red LED) on or off, decoratively. |
| C0 | 192 | Lscal | `lscal` | — | D P / S | 38 | Calibrates the light sensor from ambient light — sets upper/lower thresholds and hysteresis. |
| B3 | 179 | Lsut | `lsut src, val` | Source (0 variable, 2 constant); Value 0–1020 (**low = bright**) | D P / S | 39 | Light sensor upper threshold. |
| C3 | 195 | Lslt | `lslt src, val` | Source (0 variable, 2 constant); Value 0–1020 (**low = bright**) | D P / S | 39 | Light sensor lower threshold. |
| D3 | 211 | Lsh | `lsh src, val` | Source (0 variable, 2 constant) | D P / S | 38 | Light sensor hysteresis. |
| E3 | 227 | Lsbt | `lsbt src, val` | Source (0 variable, 2 constant); Value 1–32767 in 0.01 s | D P / S | 38 | Light sensor blink time. |
| 83 | 131 | Setfb | `setfb src, val` | Source (0 variable, 2 constant, 4 random); value is an event bitmask | D P / S | 42 | Selects which external events trigger a system sound. ⚠️ Same opcode as `uploadGlobalVariables`. |
| 57 | 87 | Sound | `sound sound_enable, sound_onoff, sound_set_number` | See Scout SDK p. 43 | D P / S | 43 | Global sound settings (i.e. mute) and which scheme is used for system sounds 10–27. |
| E2 | 226 | Vll | `vll` | Source (0 variable, 2 constant) | D P / S | 44 | Sends a 7-bit VLL command out over the VLL output. ⚠️ Same opcode as `rcxDebugSuspendResumeStep`. |
| — | 0 | cnts | `cnts number, src, val` | Number 0–1; Source (0 variable, 2 constant, 4 random); Value | / S | 36 | Sets the counter value, for overflow detection and event generation. |
| — | 0 | tmrs | `tmrs number, src, val` | Source (0 variable, 2 constant, 4 random) | D P / S | 43 | Sets the timer limit, for overflow/wrap-around detection and event generation. |

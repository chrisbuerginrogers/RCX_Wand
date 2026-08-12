# LASM examples

Five real RCX programs, in increasing order of what they touch, written
against `lasm_compiler.py`'s dialect (see that file's own docstring for
where the compiler came from and what it's verified against).

| File | Adds |
|---|---|
| [`01_hello_beep.lasm`](01_hello_beep.lasm) | One command: play a sound. |
| [`02_tune.lasm`](02_tune.lasm) | Several commands run once, in order. |
| [`03_loop_forever.lasm`](03_loop_forever.lasm) | A label + `jmp` — the first loop. |
| [`04_read_sensor.lasm`](04_read_sensor.lasm) | Reads a sensor, branches on it with `chk`. |
| [`05_motor_follow.lasm`](05_motor_follow.lasm) | Same sensor read, but drives a motor instead of a sound. |

Each file's own comments explain the opcodes it introduces — `chk`'s
backwards-feeling branch direction and the `dir` mode/direction mismatch
this project found on real hardware are both called out where they
matter (`04` and `05` respectively).

## Building

These `.lasm` files aren't run directly — they're compiled on the Mac
(where `lasm_compiler.py`'s `dataclasses`/`pathlib` imports are
available) into `lasm_programs.py` at the project root, which *is*
MicroPython-safe and gets deployed to the Stick:

```
python3 tools/build_examples.py
```

Re-run it after editing or adding a `.lasm` file — `lasm_programs.py`
is generated, not hand-edited.

## Running

On the Stick, with `rcx_driver.py`, `lasm_programs.py` and
`run_lasm.py` all deployed, and the StickS3's IR LED *and* receiver
pointed at the RCX (this needs both — see `run_lasm.py`'s docstring):

```python
import run_lasm
run_lasm.run_task('01_hello_beep')
```

Swap in any of the five names. `04`/`05` expect a light/reflection
sensor wired to sensor port 0.

## Status

Compiled output checked by hand against the compiler (chunk sizes,
checksums, and `05`'s backward loop-jump distance all verified
correct) — not yet sent to a real RCX. `run_lasm.py`'s module
docstring has the current status.

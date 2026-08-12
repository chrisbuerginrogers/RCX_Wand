"""Reads the same light sensor as 04, but drives motor A instead of
beeping: forward while bright, off in the dark. Combines a loop, a
sensor read, a branch, and motor control -- the shape a real RCX
program takes. Comparison is "sensor[0] < 40" (dark). Per chk's rule
(see 04's comment), a FALSE result -- bright -- is what triggers the
jump, so the "bright" label is where the motor turns on; the
fall-through (dark, comparison true) is where it turns off.
Direction note: LEGO's own opcode table for "dir" reads mode 0 as
"backwards" and mode 2 as "forwards". This project's own hardware
test found the opposite for motor A on the robot it was tested on
(see rcx_ir.py's _DIR_FORWARD/_DIR_REVERSE) -- mode 0 (0x00) is what
actually drove it forward. This file uses mode 0 for forward to
match that test; swap the two "dir 0,1" / mode value below if your
motor spins the wrong way.

Run it:  python3 run_on_rcx.py examples/05_motor_follow.py
"""

LASM = """\
task 0
sent 0,3                // sensor 0 = light sensor
pwr 1,2,5                // motor A (bit 0x01), constant power source, level 5
loop:
chk 9,0,1,2,40,bright     // if NOT (sensor[0] < 40) -- i.e. bright -- jump
out 1,1                   // (fell through: dark) motor A off
jmp cont
bright:
dir 0,1                   // forward -- see the note above
out 2,1                   // motor A on
cont:
wait 2,10
jmp loop
endt
"""


if __name__ == "__main__":
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
    import run_on_rcx
    run_on_rcx.run(LASM, name="05_motor_follow")

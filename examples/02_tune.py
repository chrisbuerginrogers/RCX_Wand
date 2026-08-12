"""A few notes in a row -- still no loop, just several commands that
each run once, in order. playt (opcode 0x23, "PlayTone"): frequency
in Hz, duration in 1/100s. wait (opcode 0x43): source 2 = a constant
value, value in units of 10ms -- "wait 2,20" pauses 200ms.

Run it:  python3 run_on_rcx.py examples/02_tune.py
"""

LASM = """\
task 0
playt 440,50
wait 2,20
playt 880,50
wait 2,20
playt 1320,80
endt
"""


if __name__ == "__main__":
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
    import run_on_rcx
    run_on_rcx.run(LASM, name="02_tune")

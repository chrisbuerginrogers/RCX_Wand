"""The simplest RCX program: play one system sound and stop.
PlaySystemSound codes (opcode 0x51, "plays"): 0 key click, 1 beep, 2
sweep up, 3 sweep down, 4 error, 5 fast sweep up.

Run it:  python3 run_on_rcx.py examples/01_hello_beep.py
"""

LASM = """\
task 0
plays 5
endt
"""


if __name__ == "__main__":
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
    import run_on_rcx
    run_on_rcx.run(LASM, name="01_hello_beep")

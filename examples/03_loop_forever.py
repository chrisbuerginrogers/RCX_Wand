"""Same idea as 02_tune, but wrapped in a loop with a label and jmp --
this one never ends on its own. Send a new program (or "stop", once
there's a way to send a bare direct command) to make it quit.

Run it:  python3 run_on_rcx.py examples/03_loop_forever.py
"""

LASM = """\
task 0
top:
plays 2
wait 2,50
jmp top
endt
"""


if __name__ == "__main__":
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
    import run_on_rcx
    run_on_rcx.run(LASM, name="03_loop_forever")

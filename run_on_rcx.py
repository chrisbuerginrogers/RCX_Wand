#!/usr/bin/env python3
"""Compile a LASM program and run it on a real RCX.

Runs on the Mac with the M5StickS3 plugged in over USB: this compiles
the LASM here, then has the Stick transmit it to the RCX over IR. The
Stick is the radio; the Mac is the compiler.

    python3 run_on_rcx.py examples/01_hello_beep.py
    python3 run_on_rcx.py some_program.lasm
    python3 run_on_rcx.py examples/03_loop_forever.py --no-start

or from Python (this is what every examples/*.py does):

    import run_on_rcx
    run_on_rcx.run(LASM, name='my program')

Accepts either a `.lasm` file or a `.py` file exposing a module-level
`LASM` string -- the examples are the latter so the source and the
thing that runs it live in one readable file.

Compiled programs are sent with the block-transfer protocol (each
BeginOfTask and ContinueDL chunk acked before the next goes out), then
task 0 is started unless --no-start is given.
"""
import argparse
import runpy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "tools"))
import stick_link

#: StartTask (0x71) -- what actually runs a downloaded task. Not part
#: of the compiled program; sent once the whole thing has landed.
_START_TASK = 0x71


def load_source(path):
    """LASM text from a .lasm file, or from a .py file's LASM string."""
    path = Path(path)
    if not path.exists():
        raise SystemExit("no such file: {}".format(path))
    if path.suffix == ".lasm":
        return path.read_text()
    if path.suffix == ".py":
        ns = runpy.run_path(str(path))
        if "LASM" not in ns:
            raise SystemExit(
                "{} has no module-level LASM string".format(path))
        return ns["LASM"]
    raise SystemExit("expected a .lasm or .py file, got {}".format(path))


def run(source, name=None, start_task=0, port=None, blind=False):
    """Compile LASM `source`, send it to the RCX, and start it.

    `blind=True` transmits without waiting for the RCX's block acks --
    the only way to download a program from a Stick whose IR receiver
    does not work. Delivery is usually fine (the RCX receives us
    reliably); what you lose is any confirmation, so the program
    actually running on the brick is your only feedback.
    """
    label = name or "program"
    commands = stick_link.compile_lasm_source(source)
    total = sum(1 + len(p) for _op, p in commands)
    print("{}: {} command(s), {} bytes".format(label, len(commands), total), flush=True)

    port = port or stick_link.find_port()
    print("stick on {}".format(port), flush=True)
    stick_link.ensure_runtime(port=port)

    if blind:
        print("blind mode: not waiting for acks", flush=True)
    snippet = _DEVICE_SNIPPET.format(
        commands=stick_link.commands_literal(commands),
        start=start_task if start_task is not None else -1,
        start_op=_START_TASK,
        acked="False" if blind else "True",
    )
    stick_link.execute(snippet, port=port, timeout=600)
    return True


#: Runs on the Stick. Kept deliberately small -- all the compiling
#: already happened on the Mac, so this only transmits.
_DEVICE_SNIPPET = """
from rcx_driver import RCX, BOARD_M5STICKS3
import time
rcx = RCX(board=BOARD_M5STICKS3)
cmds = {commands}
if {acked} and not rcx.receiver_looks_alive():
    print('WARNING: IR receiver looks dead/noisy - retry with --blind')
ok = rcx.send_program(cmds, acked={acked})
print('program sent' if ok else 'PROGRAM FAILED')
if ok and {start} >= 0:
    time.sleep_ms(200)
    rcx._send({start_op}, [{start}])
    print('started task {start}')
"""


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("program", help="a .lasm file, or a .py file with a LASM string")
    ap.add_argument("--no-start", action="store_true",
                    help="download the program but do not start task 0")
    ap.add_argument("--task", type=int, default=0,
                    help="which task to start (default 0)")
    ap.add_argument("--port", default=None, help="serial port override")
    ap.add_argument("--blind", action="store_true",
                    help="send without waiting for acks (needed if the "
                         "Stick's IR receiver does not work)")
    args = ap.parse_args(argv)

    source = load_source(args.program)
    try:
        run(source, name=Path(args.program).stem,
            start_task=None if args.no_start else args.task,
            port=args.port, blind=args.blind)
    except stick_link.StickError as e:
        raise SystemExit("error: {}".format(e))


if __name__ == "__main__":
    main()

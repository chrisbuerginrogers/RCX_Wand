"""Send a compiled LASM program (from lasm_programs.py, built on the
Mac by tools/build_examples.py from examples/*.lasm) to a real RCX,
then start it.
  >>> Runs ON the M5StickS3. Needs rcx_driver.py and lasm_programs.py.
      Point the StickS3 at the RCX's IR window -- the receiver too,
      block-transfer commands are acked the same way firmware download
      is. <<<

Two-way, acked, for exactly the commands that need it: BeginOfTask/
BeginOfSub and every ContinueDL chunk get their reply checked before
the next one goes out -- the same real RCX block-transfer mechanism
rcx_driver.py's download_firmware() already uses for firmware, just
with task/sub opcodes instead of firmware's. Anything compiled from a
bare top-level LASM line (outside any task/sub block) is a one-shot
direct command and goes out fire-and-forget, same as every other
direct command elsewhere in this project.

    import run_lasm
    run_lasm.run_task('01_hello_beep')

Only tested by construction (byte-level output checked by hand against
tools/build_examples.py's compiler) -- not yet against a real RCX.
"""

from rcx_driver import RCX, BOARD_M5STICKS3
import lasm_programs

#: Opcodes that get an acked reply checked -- the block-transfer
#: commands. Everything else compiled from LASM source is one-shot.
_ACKED = {0x25, 0x35, 0x45}  # BeginOfTask, BeginOfSub, ContinueDL

#: StartTask (opcode 0x71, "start task_number") -- what actually runs
#: a downloaded task. Not part of the compiled program itself; sent
#: once the whole thing has landed.
_START_TASK = 0x71


def send_program(rcx, program, timeout_ms=1500, on_progress=None):
    """program: [(opcode, params_bytes), ...], as built by
    tools/build_examples.py / stored in lasm_programs.PROGRAMS.

    Returns True if every acked step got a reply, False on the first
    one that didn't -- data already sent that far is simply discarded
    by the RCX (same as a failed firmware download: try again, nothing
    is bricked).
    """
    for i, (opcode, params) in enumerate(program):
        if opcode in _ACKED:
            if rcx._send_and_recv(opcode, params, timeout_ms=timeout_ms) is None:
                print('RCX: no reply to opcode {:#04x}, step {}/{}'.format(
                    opcode, i + 1, len(program)))
                return False
        else:
            rcx._send(opcode, params)
        if on_progress:
            on_progress(i + 1, len(program))
    return True


def run_task(name, task_num=0, on_progress=None):
    """Send lasm_programs.PROGRAMS[name] and start task_num (0 unless
    the .lasm source declared a different `task N`). Returns True/False,
    same convention as send_program()."""
    program = lasm_programs.PROGRAMS[name]
    rcx = RCX(board=BOARD_M5STICKS3)
    print('sending {} ({} command(s))...'.format(name, len(program)))
    if not send_program(rcx, program, on_progress=on_progress):
        print('FAILED -- see the message above for which step did not ack')
        return False
    if rcx._send_and_recv(_START_TASK, [task_num & 0xFF]) is None:
        print('RCX: sent the program but no reply to start task', task_num)
        return False
    print('sent + started task', task_num)
    return True

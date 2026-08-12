"""Mac-side plumbing for driving the M5StickS3 over USB via mpremote.

Everything the RCX ever hears is transmitted by the Stick's IR LED, so
the Mac's job is only ever: compile, copy what the device needs, then
run a snippet on it. This module is that job, and nothing else -- the
two top-level entry points (run_on_rcx.py, load_firmware_on_rcx.py)
share it so neither has to know about serial ports or mpremote.

Needs `mpremote` on PATH (`pip install mpremote`).
"""
import glob
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Modules the device needs in order to talk to an RCX at all. Copied
#: up before every run so the Stick can't be running a stale driver --
#: mpremote skips files whose content already matches, so this is cheap.
#:
#: These live in tools/ in the repo but land at the *root* of the
#: device's filesystem, which is flat and has no import path to speak
#: of. Repo layout and device layout are unrelated; this list is where
#: the mapping is defined.
DEVICE_RUNTIME = ["tools/rcx_driver.py"]

#: Everything the standalone wand app needs, for a from-scratch deploy.
#: Not used by the entry points (they only push what they need); it is
#: here so the README's copy command has a single source of truth.
WAND_FILES = [
    "main.py", "RCXWand.py", "cardID.py",
    "stick_menu.py", "stick_ui.py", "lego_card.py",
    "tools/rcx_ir.py", "tools/rcx_driver.py",
]


class StickError(Exception):
    """No Stick, no mpremote, or a device-side command failed."""


def find_port():
    """The Stick's USB serial port.

    Excludes the Bluetooth and debug-console devices macOS always
    lists, which otherwise get picked ahead of a real board.
    """
    ports = [p for p in sorted(glob.glob("/dev/cu.usbmodem*"))
             if "Bluetooth" not in p and "debug-console" not in p]
    if not ports:
        raise StickError(
            "no M5StickS3 found on /dev/cu.usbmodem* -- is it plugged in?\n"
            "If it is, check nothing else holds the port: VS Code's serial\n"
            "monitor and an open mpremote session both lock it exclusively.")
    if len(ports) > 1:
        # Silently picking one when several are connected is how you spend
        # an afternoon testing the wrong board.
        print("WARNING: {} boards connected: {}".format(len(ports), ", ".join(ports)))
        print("         using {} -- pass --port to choose".format(ports[0]))
    return ports[0]


def _mpremote():
    exe = shutil.which("mpremote")
    if not exe:
        raise StickError("mpremote not found on PATH -- `pip install mpremote`")
    return exe


def _run(args, port=None, stream=True, timeout=None):
    cmd = [_mpremote(), "connect", port or find_port()] + args
    if stream:
        proc = subprocess.run(cmd, timeout=timeout)
        rc = proc.returncode
        out = ""
    else:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        rc = proc.returncode
        out = (proc.stdout or "") + (proc.stderr or "")
    if rc != 0:
        raise StickError("mpremote failed (exit {}):\n{}".format(rc, out.strip()))
    return out


def copy(paths, port=None):
    """Copy files to the device's filesystem root."""
    port = port or find_port()
    for p in paths:
        src = Path(p)
        if not src.is_absolute():
            src = ROOT / src
        if not src.exists():
            raise StickError("missing file to copy: {}".format(src))
        _run(["cp", str(src), ":"], port=port, stream=False)


def copy_dir(path, port=None):
    """Copy a directory (recursively) to the device's filesystem root."""
    src = Path(path)
    if not src.is_absolute():
        src = ROOT / src
    if not src.is_dir():
        raise StickError("not a directory: {}".format(src))
    _run(["cp", "-r", str(src), ":"], port=port or find_port(), stream=False)


def execute(code, port=None, stream=True, timeout=None):
    """Run `code` on the device, streaming its output to our stdout."""
    return _run(["exec", code], port=port, stream=stream, timeout=timeout)


def ensure_runtime(extra=(), port=None):
    """Push the driver (plus anything else named) to the device."""
    copy(list(DEVICE_RUNTIME) + list(extra), port=port)


def commands_literal(commands):
    """Compiled LASM commands as Python source, for embedding in an
    exec'd snippet.

    Programs are small -- a few hundred bytes of bytecode -- so pushing
    them inline beats generating a module and copying it, and it means
    nothing stale is ever left on the device's filesystem.
    """
    parts = ["({:#04x}, {!r})".format(op, bytes(params)) for op, params in commands]
    return "[" + ", ".join(parts) + "]"


def compile_lasm_source(source, nxt_compatible=False):
    """LASM text -> [(opcode, params_bytes), ...].

    nxt_compatible=False by default: everything here targets a real
    RCX, so `prgm` compiles to the documented SelectProgram and `view`
    to its dedicated opcode rather than the NXT-firmware substitutions
    (one of which lasm_compiler.py still tags as unverified).
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import lasm_compiler
    commands = lasm_compiler.compile_lasm(source, nxt_compatible=nxt_compatible)
    return [(c.opcode, bytes(c.params)) for c in commands]

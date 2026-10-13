"""Atomic publication without overwriting an existing deliverable."""
import ctypes
import os
import sys
from pathlib import Path


def publish_exclusive(source: Path, target: Path):
    if sys.platform == "darwin":
        # macOS 10.12+: RENAME_EXCL works without requiring hard-link support on
        # a network volume. The staged ZIP is already on the destination volume.
        rename = ctypes.CDLL(None, use_errno=True).renamex_np
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        if rename(os.fsencode(source), os.fsencode(target), 0x00000004) != 0:
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code), str(target))
    else:
        # Development tests on NTFS/Linux exercise the same no-overwrite rule.
        os.link(source, target)

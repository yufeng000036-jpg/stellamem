"""Shared stdout encoding guard for CLI entry points.

On Windows, Python's default stdout encoding follows the console code
page (often cp936 / cp1252), not UTF-8. CLI commands that print JSON
with ``ensure_ascii=False`` therefore raise ``UnicodeEncodeError`` as
soon as a record contains Chinese text, even though the JSON itself
is valid. Reconfiguring stdout to UTF-8 at CLI startup fixes the
mechanism, without escaping the output (readable Chinese is kept).

The guard is best-effort by design: a replaced or closed stdout
(e.g. a test double without ``reconfigure``) must never turn a CLI
call into a new failure, so every failure here is swallowed.
"""

import sys


def ensure_utf8_stdout():
    """Reconfigure stdout to UTF-8 when possible; never raise."""
    stream = getattr(sys, "stdout", None)
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return False
    try:
        reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        return False
    return True

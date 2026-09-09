"""No command pays for a renderer it will not use."""

from __future__ import annotations

import subprocess
import sys


def test_the_cli_does_not_import_the_document_renderer() -> None:
    """WeasyPrint loads the native GTK stack the moment it is imported, and on Windows
    that prints several GLib-GIO-WARNING lines to stderr. The operator's whole acceptance
    pass — `aer diagnose`, `aer preflight`, `aer acceptance`, `aer step` — was read
    through that noise, and none of those commands renders a document.
    """
    probe = "import sys, aer.cli; print('weasyprint' in sys.modules)"
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "False", (
        "importing aer.cli loaded WeasyPrint. Something on the CLI's import graph reaches "
        "aer.render.pdf at module scope; defer it to the branch that renders."
    )

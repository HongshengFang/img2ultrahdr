from __future__ import annotations

import os
from pathlib import Path


def runtime_dir() -> Path:
    """Resolve the explicit runtime override or the current project's cache."""
    return Path(os.environ.get("IMG2UHDR_JPEG_HOME", ".jpeg-hdr")).expanduser().resolve()

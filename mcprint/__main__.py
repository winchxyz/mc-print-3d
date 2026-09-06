"""``python -m mcprint`` entry point (CLI; use ``python -m mcprint gui`` for the desktop app)."""
import sys

from .cli import main

sys.exit(main())

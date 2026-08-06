"""
Entry point alias — `python -m app.evaluations.run`.

Delegates to app.evaluations.__main__.main().
"""

from __future__ import annotations

import sys

from app.evaluations.__main__ import main

if __name__ == "__main__":
    sys.exit(main())

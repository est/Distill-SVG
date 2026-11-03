"""Frozen entry point for the GUI helper (``distill-core``).

PyInstaller bundles this module together with ``distill_svg``, numpy, OpenCV
and Pillow.  The macOS shell starts it once and speaks NDJSON over stdio.
"""

import sys

from distill_svg.worker import main

if __name__ == "__main__":
    sys.exit(main())

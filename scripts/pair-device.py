#!/usr/bin/env python3
"""Open the local QR pairing page; does not install or start the Bridge."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pairing_ui import main

if __name__ == '__main__':
    raise SystemExit(main())

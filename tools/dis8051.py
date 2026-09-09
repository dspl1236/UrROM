"""tools/dis8051.py — CLI shim; the disassembler lives in urrom/dis8051.py."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from urrom.dis8051 import main  # noqa: E402

if __name__ == "__main__":
    main()

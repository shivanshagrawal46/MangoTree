"""Free space on every drive, to decide where the object store should live."""
from __future__ import annotations

import shutil
import string
from pathlib import Path

print("\n  drive      total GB    free GB")
print("  " + "-" * 34)
for letter in string.ascii_uppercase:
    root = Path(f"{letter}:\\")
    if not root.exists():
        continue
    try:
        usage = shutil.disk_usage(root)
    except OSError:
        continue
    print(f"  {letter}:      {usage.total / 1024**3:>9.1f}  {usage.free / 1024**3:>9.1f}")
print()

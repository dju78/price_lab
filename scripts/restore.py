"""Restore a backup made by scripts/backup.py.

    python scripts/restore.py BACKUP_DIR [--overwrite]

Refuses to write over an existing database or a non-empty store unless
--overwrite is given. Stop the application first. See docs/admin_guide.md.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pricelab.core.backup import restore  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup_dir")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    result = restore(args.backup_dir, overwrite=args.overwrite)
    print(json.dumps({k: v for k, v in result.items() if k != "manifest"}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Back up the database and the Parquet store into a timestamped directory.

    python scripts/backup.py [--to BACKUPS_DIR]

Reads PRICELAB_DATABASE_URL and PRICELAB_STORE_DIR from the environment
like the application does. See docs/admin_guide.md.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pricelab.core.backup import BackupError, backup  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--to", default="backups", help="directory to create the backup under")
    args = parser.parse_args()
    dest = Path(args.to) / datetime.now(UTC).strftime("pricelab-%Y%m%dT%H%M%SZ")
    try:
        manifest = backup(dest)
    except BackupError as exc:
        print(f"Backup refused: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"backup": str(dest), "files": len(manifest["files"])}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

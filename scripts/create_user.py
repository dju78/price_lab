"""Create a PriceLab user.

There is no self-registration by design: every account is provisioned
deliberately. Run this once against a fresh database to create the first
administrator, and again for anyone else who needs an account.

    python scripts/create_user.py --username admin --role administrator

Prompts for the password rather than taking it as an argument, so it never
ends up in shell history or a process listing.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pricelab.core import db
from pricelab.core.models import Role
from pricelab.core.security import create_user


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", required=True)
    parser.add_argument("--role", required=True, choices=[r.value for r in Role])
    args = parser.parse_args()

    password = getpass.getpass("Password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("Passwords did not match.", file=sys.stderr)
        return 1
    if len(password) < 8:
        print("Use a password of at least 8 characters.", file=sys.stderr)
        return 1

    db.init_db()
    try:
        with db.session_scope() as session:
            create_user(session, args.username, password, Role(args.role))
    except ValueError as exc:
        print(f"Could not create user: {exc}", file=sys.stderr)
        return 1

    print(f"Created user '{args.username}' with role '{args.role}'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

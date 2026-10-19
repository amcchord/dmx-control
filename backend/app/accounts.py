"""Create an initial administrator over SSH, never through a public setup route.

DMX_DATA_DIR=/var/lib/dmx-control python -m app.accounts austin
Password is read from stdin (or a terminal prompt). No default password exists.
"""
import getpass
import sys
from .db import init_db
from .auth import create_user

if __name__ == "__main__":
    init_db()
    password = getpass.getpass("Password: ") if sys.stdin.isatty() else sys.stdin.readline().rstrip("\n")
    user = create_user(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else sys.argv[1], "admin", password)
    print(f"Administrator {user.username} created")

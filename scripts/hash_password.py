"""Make the login settings for the server. Nothing is stored; copy the output into Railway.

python scripts/hash_password.py            # asks for names + passwords
python scripts/hash_password.py --secret   # just a new COUPONAI_SECRET_KEY
"""

import getpass
import json
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.auth import hash_password  # noqa: E402


def main() -> None:
    if "--secret" in sys.argv:
        print(secrets.token_urlsafe(48))
        return
    users: dict[str, str] = {}
    print("Add the people who can log in. Leave the name empty to finish.")
    while True:
        name = input("Name: ").strip()
        if not name:
            break
        pw = getpass.getpass(f"Password for {name} (hidden, 10+ chars): ")
        if len(pw) < 10:
            print("  Too short, try again.")
            continue
        if pw != getpass.getpass("Repeat password: "):
            print("  Passwords differ, try again.")
            continue
        users[name] = hash_password(pw)
    if not users:
        return
    print("\nSet these variables on the server (Railway -> service -> Variables):\n")
    print("COUPONAI_USERS=" + json.dumps(users, ensure_ascii=False))
    print("COUPONAI_SECRET_KEY=" + secrets.token_urlsafe(48))


if __name__ == "__main__":
    main()

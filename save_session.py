"""Helper script to create an Instaloader session file directly from browser cookies.

Bypasses login password prompts and Instagram checkpoint bot checks.
"""

import argparse
import os
from pathlib import Path
import pickle
import sys


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create Instaloader session file directly from browser cookies.",
    )
    parser.add_argument("--username", help="Instagram username (e.g., teoo.mnl)")
    parser.add_argument("--sessionid", help="Value of 'sessionid' cookie from browser")
    parser.add_argument("--csrftoken", help="Value of 'csrftoken' cookie from browser")
    parser.add_argument("--ds-user-id", default="", help="Value of 'ds_user_id' cookie (optional)")
    args = parser.parse_args()

    username = (args.username or input("Instagram username: ")).strip()
    sessionid = (args.sessionid or input("sessionid cookie: ")).strip()
    csrftoken = (args.csrftoken or input("csrftoken cookie: ")).strip()
    ds_user_id = (args.ds_user_id or input("ds_user_id cookie (optional, press Enter to skip): ")).strip()

    if not username or not sessionid or not csrftoken:
        print("Error: username, sessionid, and csrftoken are required.", file=sys.stderr)
        return 1

    session_dir = Path(os.environ.get("LOCALAPPDATA", "")) / "Instaloader"
    session_dir.mkdir(parents=True, exist_ok=True)
    session_file = session_dir / f"session-{username}"

    cookie_dict = {
        "sessionid": sessionid,
        "csrftoken": csrftoken,
    }
    if ds_user_id:
        cookie_dict["ds_user_id"] = ds_user_id

    with open(session_file, "wb") as f:
        pickle.dump(cookie_dict, f)

    print(f"\n[+] Session successfully created and saved to:")
    print(f"    {session_file}")
    print(f"\nYou can now enter '{username}' as the Session Username in the Web UI or pass --session-user {username} in the CLI!")
    return 0


if __name__ == "__main__":
    sys.exit(main())


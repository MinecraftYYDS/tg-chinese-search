from __future__ import annotations

import base64
import os
import sys

import bcrypt
from cryptography.fernet import Fernet


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python scripts/generate_secrets.py <admin_password>")
        sys.exit(1)
    password = sys.argv[1].encode("utf-8")
    pwd_hash = bcrypt.hashpw(password, bcrypt.gensalt()).decode("utf-8")
    fernet_key = Fernet.generate_key().decode("utf-8")
    key_len = len(base64.urlsafe_b64decode(fernet_key.encode("utf-8")))
    print(f"ADMIN_PASSWORD_HASH={pwd_hash}")
    print(f"CONFIG_ENCRYPTION_KEY={fernet_key}  # decoded_length={key_len}")


if __name__ == "__main__":
    main()


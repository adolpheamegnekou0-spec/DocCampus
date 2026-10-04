from __future__ import annotations

import getpass
import hashlib
import os
import re
import secrets
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from main import BASE_DIR, PASSWORD_ITERATIONS

load_dotenv(BASE_DIR / ".env")


def create_admin_account(
    database_path: Path,
    email: str,
    password: str,
    *,
    replace_existing: bool = False,
) -> bool:
    normalized_email = email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalized_email):
        raise ValueError("Adresse e-mail invalide.")
    if len(password) < 12 or len(password) > 128:
        raise ValueError("Le mot de passe doit contenir entre 12 et 128 caractères.")

    salt = secrets.token_bytes(16)
    password_hash = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PASSWORD_ITERATIONS,
    )
    database_path.parent.mkdir(parents=True, exist_ok=True)

    with closing(sqlite3.connect(database_path)) as connection:
        with connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT NOT NULL UNIQUE,
                    password_salt BLOB NOT NULL,
                    password_hash BLOB NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('user', 'admin')),
                    created_at TEXT NOT NULL
                )
                """,
            )
            existing_user = connection.execute(
                "SELECT id FROM users WHERE email = ?",
                (normalized_email,),
            ).fetchone()
            if existing_user and not replace_existing:
                return False
            if existing_user:
                connection.execute(
                    """
                    UPDATE users
                    SET password_salt = ?, password_hash = ?, role = 'admin'
                    WHERE email = ?
                    """,
                    (salt, password_hash, normalized_email),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO users
                        (email, password_salt, password_hash, role, created_at)
                    VALUES (?, ?, ?, 'admin', ?)
                    """,
                    (
                        normalized_email,
                        salt,
                        password_hash,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
    return True


def main() -> int:
    database_path = Path(os.getenv(
        "DOCCAMPUS_DATABASE",
        BASE_DIR / "data" / "doccampus.sqlite3",
    ))
    email = input("Adresse e-mail administrateur : ").strip().lower()
    password = getpass.getpass("Mot de passe (12 caractères minimum) : ")
    confirmation = getpass.getpass("Confirmer le mot de passe : ")

    if password != confirmation:
        print("Erreur : les mots de passe ne correspondent pas.", file=sys.stderr)
        return 1

    try:
        created = create_admin_account(database_path, email, password)
        if not created:
            answer = input(
                "Ce compte existe déjà. Le promouvoir et remplacer son mot de passe ? [o/N] "
            ).strip().lower()
            if answer not in {"o", "oui"}:
                print("Opération annulée.")
                return 1
            create_admin_account(
                database_path,
                email,
                password,
                replace_existing=True,
            )
    except (ValueError, sqlite3.Error) as error:
        print(f"Erreur : {error}", file=sys.stderr)
        return 1

    print(f"Compte administrateur configuré pour {email}.")
    print(f"Base de données : {database_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

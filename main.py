import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import uuid
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any, AsyncGenerator, Generator

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, field_validator
from supabase import create_client
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
DEFAULT_CATEGORIES = ("Cours", "TD", "Annale", "Corrigé")
MAX_PDF_SIZE = 30 * 1024 * 1024
PASSWORD_ITERATIONS = 310_000
TOKEN_LIFETIME = timedelta(hours=12)
bearer_scheme = HTTPBearer(auto_error=False)


class Credentials(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=10, max_length=128)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalized):
            raise ValueError("Adresse e-mail invalide.")
        return normalized


class CategoryInput(BaseModel):
    name: str = Field(min_length=1, max_length=40)


def _password_hash(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PASSWORD_ITERATIONS,
    )


def _encode_token(payload: dict[str, Any], secret_key: bytes) -> str:
    encoded_payload = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).rstrip(b"=")
    signature = hmac.new(secret_key, encoded_payload, hashlib.sha256).digest()
    encoded_signature = base64.urlsafe_b64encode(signature).rstrip(b"=")
    return f"{encoded_payload.decode('ascii')}.{encoded_signature.decode('ascii')}"


def _decode_token(token: str, secret_key: bytes) -> dict[str, Any]:
    try:
        payload_part, signature_part = token.split(".", maxsplit=1)
        encoded_payload = payload_part.encode("ascii")
        provided_signature = base64.urlsafe_b64decode(
            signature_part + "=" * (-len(signature_part) % 4)
        )
        expected_signature = hmac.new(
            secret_key,
            encoded_payload,
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(provided_signature, expected_signature):
            raise ValueError("Invalid signature")
        payload = json.loads(
            base64.urlsafe_b64decode(
                payload_part + "=" * (-len(payload_part) % 4)
            )
        )
        if not isinstance(payload, dict) or int(payload["exp"]) <= int(
            datetime.now(timezone.utc).timestamp()
        ):
            raise ValueError("Expired or malformed token")
        return payload
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session invalide ou expirée.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from error


def create_app(
    *,
    database_path: Path | None = None,
    uploads_path: Path | None = None,
    secret_key: str | None = None,
    admin_email: str | None = None,
    admin_password: str | None = None,
) -> FastAPI:
    db_path = database_path or Path(
        os.getenv("DOCCAMPUS_DATABASE", BASE_DIR / "data" / "doccampus.sqlite3")
    )
    file_storage = uploads_path or Path(
        os.getenv("DOCCAMPUS_UPLOADS", BASE_DIR / "data" / "uploads")
    )
    configured_secret = secret_key or os.getenv("DOCCAMPUS_SECRET_KEY")
    has_configured_secret = bool(configured_secret)
    if configured_secret is None:
        configured_secret = secrets.token_urlsafe(48)
    signing_key = configured_secret.encode("utf-8")
    initial_admin_email = (admin_email or os.getenv("ADMIN_EMAIL", "")).strip().lower()
    initial_admin_password = admin_password or os.getenv("ADMIN_PASSWORD", "")

    @contextmanager
    def connect() -> Generator[sqlite3.Connection, None, None]:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize_database() -> None:
        if not has_configured_secret:
            raise RuntimeError("Configure DOCCAMPUS_SECRET_KEY dans le fichier .env.")
        if bool(initial_admin_email) != bool(initial_admin_password):
            raise RuntimeError("Configure ADMIN_EMAIL et ADMIN_PASSWORD ensemble, ou crée le compte avec create_admin.py.")
        file_storage.mkdir(parents=True, exist_ok=True)
        with connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT NOT NULL UNIQUE,
                    password_salt BLOB NOT NULL,
                    password_hash BLOB NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('user', 'admin')),
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    type TEXT NOT NULL,
                    institution TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    year TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    downloads INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS categories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE
                );
                """
            )
            connection.executemany(
                "INSERT OR IGNORE INTO categories (name) VALUES (?)",
                ((category,) for category in DEFAULT_CATEGORIES),
            )
            if initial_admin_email and initial_admin_password:
                if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", initial_admin_email):
                    raise RuntimeError("ADMIN_EMAIL doit être une adresse valide.")
                if len(initial_admin_password) < 12:
                    raise RuntimeError("ADMIN_PASSWORD doit contenir au moins 12 caractères.")
                exists = connection.execute(
                    "SELECT id FROM users WHERE email = ?",
                    (initial_admin_email,),
                ).fetchone()
                if exists is None:
                    salt = secrets.token_bytes(16)
                    connection.execute(
                        """
                        INSERT INTO users
                            (email, password_salt, password_hash, role, created_at)
                        VALUES (?, ?, ?, 'admin', ?)
                        """,
                        (
                            initial_admin_email,
                            salt,
                            _password_hash(initial_admin_password, salt),
                            datetime.now(timezone.utc).isoformat(),
                        ),
                    )

    def get_supabase_client() -> Any | None:
        supabase_url = os.getenv("SUPABASE_URL")
        supabase_key = os.getenv("SUPABASE_KEY")
        if not supabase_url or not supabase_key:
            return None
        try:
            return create_client(supabase_url, supabase_key)
        except Exception:
            return None

    def get_supabase_bucket() -> str:
        return os.getenv("SUPABASE_BUCKET", "documents")

    def remove_document_from_storage(file_name: str) -> None:
        supabase_client = get_supabase_client()
        if supabase_client is not None:
            try:
                supabase_client.storage.from_(get_supabase_bucket()).remove([file_name])
            except Exception:
                pass
            return
        local_file = file_storage / file_name
        local_file.unlink(missing_ok=True)

    def issue_token(user: sqlite3.Row) -> str:
        now = datetime.now(timezone.utc)
        return _encode_token(
            {
                "sub": user["id"],
                "iat": int(now.timestamp()),
                "exp": int((now + TOKEN_LIFETIME).timestamp()),
            },
            signing_key,
        )

    def get_user(
        credentials: Annotated[
            HTTPAuthorizationCredentials | None,
            Depends(bearer_scheme),
        ],
    ) -> sqlite3.Row:
        if credentials is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Connexion requise.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        payload = _decode_token(credentials.credentials, signing_key)
        with connect() as connection:
            user = connection.execute(
                "SELECT id, email, role, created_at FROM users WHERE id = ?",
                (payload["sub"],),
            ).fetchone()
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Ce compte n'existe plus.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return user

    def require_admin(
        user: Annotated[sqlite3.Row, Depends(get_user)],
    ) -> sqlite3.Row:
        if user["role"] != "admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Accès réservé à l'administration.",
            )
        return user

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
        initialize_database()
        yield

    application = FastAPI(
        title="DocCampus API",
        version="1.0.0",
        lifespan=lifespan,
    )
    allowed_origins = [
        origin.strip()
        for origin in os.getenv(
            "DOCCAMPUS_ALLOWED_ORIGINS",
            "http://localhost:8000,http://127.0.0.1:8000",
        ).split(",")
        if origin.strip()
    ]
    application.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @application.get("/", response_class=HTMLResponse)
    def homepage() -> FileResponse:
        return FileResponse(BASE_DIR / "index.html")

    @application.get("/index.html", response_class=HTMLResponse)
    def index_page() -> FileResponse:
        return FileResponse(BASE_DIR / "index.html")

    @application.get("/admin.html", response_class=HTMLResponse)
    def admin_page() -> FileResponse:
        return FileResponse(BASE_DIR / "admin.html")

    @application.get("/style.css")
    def stylesheet() -> FileResponse:
        return FileResponse(BASE_DIR / "style.css", media_type="text/css")

    @application.get("/script.js")
    def site_script() -> FileResponse:
        return FileResponse(BASE_DIR / "script.js", media_type="text/javascript")

    @application.get("/admin.js")
    def admin_script() -> FileResponse:
        return FileResponse(BASE_DIR / "admin.js", media_type="text/javascript")

    @application.get("/assets/logo-doccampus.svg")
    def logo_asset() -> FileResponse:
        return FileResponse(BASE_DIR / "assets" / "logo-doccampus.svg", media_type="image/svg+xml")

    @application.get("/assets/logo-mark.svg")
    def logo_mark_asset() -> FileResponse:
        return FileResponse(BASE_DIR / "assets" / "logo-mark.svg", media_type="image/svg+xml")

    @application.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.post("/api/auth/register", status_code=status.HTTP_201_CREATED)
    def register(payload: Credentials) -> dict[str, Any]:
        if payload.email == initial_admin_email:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cette adresse e-mail est réservée à l'administration.",
            )
        salt = secrets.token_bytes(16)
        with connect() as connection:
            try:
                cursor = connection.execute(
                    """
                    INSERT INTO users
                        (email, password_salt, password_hash, role, created_at)
                    VALUES (?, ?, ?, 'user', ?)
                    """,
                    (
                        payload.email,
                        salt,
                        _password_hash(payload.password, salt),
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Cette adresse e-mail est déjà utilisée.",
                ) from error
            user = connection.execute(
                "SELECT id, email, role, created_at FROM users WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()
        return {"access_token": issue_token(user), "token_type": "bearer", "user": dict(user)}

    @application.post("/api/auth/login")
    def login(payload: Credentials) -> dict[str, Any]:
        with connect() as connection:
            user = connection.execute(
                "SELECT * FROM users WHERE email = ?",
                (payload.email,),
            ).fetchone()
        candidate_hash = (
            _password_hash(payload.password, user["password_salt"])
            if user is not None
            else _password_hash(payload.password, b"doccampus-invalid-salt")
        )
        if user is None or not hmac.compare_digest(
            candidate_hash,
            user["password_hash"],
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Adresse e-mail ou mot de passe incorrect.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return {
            "access_token": issue_token(user),
            "token_type": "bearer",
            "user": {
                "id": user["id"],
                "email": user["email"],
                "role": user["role"],
                "created_at": user["created_at"],
            },
        }

    @application.get("/api/auth/me")
    def current_user(
        user: Annotated[sqlite3.Row, Depends(get_user)],
    ) -> dict[str, Any]:
        return dict(user)

    @application.get("/api/categories")
    def list_categories() -> list[dict[str, Any]]:
        with connect() as connection:
            rows = connection.execute(
                "SELECT id, name FROM categories ORDER BY id"
            ).fetchall()
        return [dict(row) for row in rows]

    @application.post("/api/admin/categories", status_code=status.HTTP_201_CREATED)
    def add_category(
        payload: CategoryInput,
        _: Annotated[sqlite3.Row, Depends(require_admin)],
    ) -> dict[str, Any]:
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=422, detail="Le nom est obligatoire.")
        with connect() as connection:
            try:
                cursor = connection.execute(
                    "INSERT INTO categories (name) VALUES (?)",
                    (name,),
                )
            except sqlite3.IntegrityError as error:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Cette catégorie existe déjà.",
                ) from error
            row = connection.execute(
                "SELECT id, name FROM categories WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()
        return dict(row)

    @application.delete("/api/admin/categories/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
    def remove_category(
        category_id: int,
        _: Annotated[sqlite3.Row, Depends(require_admin)],
    ) -> None:
        with connect() as connection:
            row = connection.execute(
                "SELECT name FROM categories WHERE id = ?",
                (category_id,),
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Catégorie introuvable.")
            if row["name"] in DEFAULT_CATEGORIES:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Les catégories de base ne peuvent pas être supprimées.",
                )
            used_by_documents = connection.execute(
                "SELECT 1 FROM documents WHERE type = ? LIMIT 1",
                (row["name"],),
            ).fetchone()
            if used_by_documents is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Cette catégorie est encore utilisée par des documents.",
                )
            connection.execute("DELETE FROM categories WHERE id = ?", (category_id,))

    @application.get("/api/documents")
    def list_documents() -> list[dict[str, Any]]:
        with connect() as connection:
            rows = connection.execute(
                """
                SELECT id, title, type, institution, subject, year, downloads
                FROM documents ORDER BY created_at DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    @application.get("/api/admin/documents")
    def list_admin_documents(
        _: Annotated[sqlite3.Row, Depends(require_admin)],
    ) -> list[dict[str, Any]]:
        with connect() as connection:
            rows = connection.execute(
                "SELECT * FROM documents ORDER BY created_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    @application.post("/api/admin/documents", status_code=status.HTTP_201_CREATED)
    async def add_document(
        _: Annotated[sqlite3.Row, Depends(require_admin)],
        title: Annotated[str, Form(min_length=1, max_length=120)],
        document_type: Annotated[str, Form(alias="type", min_length=1, max_length=40)],
        institution: Annotated[str, Form(min_length=1, max_length=40)],
        subject: Annotated[str, Form(min_length=1, max_length=80)],
        year: Annotated[str, Form(max_length=4)],
        file: Annotated[UploadFile, File()],
    ) -> dict[str, Any]:
        with connect() as connection:
            category = connection.execute(
                "SELECT id FROM categories WHERE name = ?",
                (document_type.strip(),),
            ).fetchone()
        if category is None:
            raise HTTPException(status_code=422, detail="Catégorie de document inconnue.")
        if not file.filename or Path(file.filename).suffix.lower() != ".pdf":
            raise HTTPException(status_code=415, detail="Seuls les fichiers PDF sont acceptés.")
        if file.content_type not in ("application/pdf", "application/octet-stream"):
            raise HTTPException(status_code=415, detail="Le fichier doit être un PDF.")

        document_id = str(uuid.uuid4())
        file_name = f"{document_id}.pdf"
        file_bytes = await file.read()
        total_size = len(file_bytes)
        if total_size > MAX_PDF_SIZE:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Le PDF dépasse la limite de 30 Mo.",
            )
        if not file_bytes[:5].startswith(b"%PDF-"):
            raise HTTPException(status_code=415, detail="Le fichier envoyé n'est pas un PDF valide.")

        supabase_client = get_supabase_client()
        if supabase_client is not None:
            try:
                supabase_client.storage.from_(get_supabase_bucket()).upload(
                    file_name,
                    file_bytes,
                    {"content-type": "application/pdf", "upsert": "false"},
                )
            except Exception as error:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Le stockage Supabase est indisponible pour l'upload du document.",
                ) from error
        else:
            destination = file_storage / file_name
            try:
                with destination.open("xb") as output:
                    output.write(file_bytes)
            except Exception:
                destination.unlink(missing_ok=True)
                raise

        try:
            with connect() as connection:
                connection.execute(
                    """
                    INSERT INTO documents
                        (id, title, type, institution, subject, year, file_name, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        document_id,
                        title.strip(),
                        document_type.strip(),
                        institution.strip().upper(),
                        subject.strip(),
                        year.strip(),
                        file_name,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
        except Exception:
            if supabase_client is not None:
                try:
                    supabase_client.storage.from_(get_supabase_bucket()).remove([file_name])
                except Exception:
                    pass
            else:
                (file_storage / file_name).unlink(missing_ok=True)
            raise
        finally:
            await file.close()

        return {
            "id": document_id,
            "title": title.strip(),
            "type": document_type.strip(),
            "institution": institution.strip().upper(),
            "subject": subject.strip(),
            "year": year.strip(),
            "downloads": 0,
        }

    @application.get("/api/documents/{document_id}/file")
    def download_document(document_id: str) -> Response:
        with connect() as connection:
            row = connection.execute(
                "SELECT file_name, title FROM documents WHERE id = ?",
                (document_id,),
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Document introuvable.")
            file_name = row["file_name"]
            connection.execute(
                "UPDATE documents SET downloads = downloads + 1 WHERE id = ?",
                (document_id,),
            )

        safe_title = re.sub(r"[^A-Za-z0-9._-]+", "-", row["title"]).strip("-") or "document"
        supabase_client = get_supabase_client()
        if supabase_client is not None:
            try:
                file_bytes = supabase_client.storage.from_(get_supabase_bucket()).download(file_name)
            except Exception as error:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Le fichier PDF n'est plus disponible sur Supabase Storage.",
                ) from error
            return Response(
                content=file_bytes,
                media_type="application/pdf",
                headers={"Content-Disposition": f'attachment; filename="{safe_title}.pdf"'},
            )

        file_path = file_storage / file_name
        if not file_path.is_file() or file_path.parent.resolve() != file_storage.resolve():
            raise HTTPException(status_code=404, detail="Le fichier PDF n'est plus disponible.")
        return FileResponse(
            file_path,
            media_type="application/pdf",
            filename=f"{safe_title}.pdf",
        )

    @application.delete("/api/admin/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_document(
        document_id: str,
        _: Annotated[sqlite3.Row, Depends(require_admin)],
    ) -> None:
        with connect() as connection:
            row = connection.execute(
                "SELECT file_name FROM documents WHERE id = ?",
                (document_id,),
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Document introuvable.")
            connection.execute("DELETE FROM documents WHERE id = ?", (document_id,))
        remove_document_from_storage(row["file_name"])

    @application.get("/api/admin/users")
    def list_users(
        _: Annotated[sqlite3.Row, Depends(require_admin)],
    ) -> list[dict[str, Any]]:
        with connect() as connection:
            rows = connection.execute(
                "SELECT id, email, role, created_at FROM users ORDER BY created_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    @application.delete("/api/admin/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
    def remove_user(
        user_id: int,
        current_admin: Annotated[sqlite3.Row, Depends(require_admin)],
    ) -> None:
        if user_id == current_admin["id"]:
            raise HTTPException(status_code=409, detail="Tu ne peux pas supprimer ton propre compte.")
        with connect() as connection:
            target = connection.execute(
                "SELECT role FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
            if target is None:
                raise HTTPException(status_code=404, detail="Utilisateur introuvable.")
            if target["role"] == "admin":
                raise HTTPException(status_code=409, detail="Un administrateur ne peut pas être supprimé ici.")
            connection.execute("DELETE FROM users WHERE id = ?", (user_id,))

    @application.get("/api/admin/stats")
    def admin_stats(
        _: Annotated[sqlite3.Row, Depends(require_admin)],
    ) -> dict[str, int]:
        with connect() as connection:
            documents_count = connection.execute(
                "SELECT COUNT(*) AS total FROM documents"
            ).fetchone()["total"]
            downloads_count = connection.execute(
                "SELECT COALESCE(SUM(downloads), 0) AS total FROM documents"
            ).fetchone()["total"]
            users_count = connection.execute(
                "SELECT COUNT(*) AS total FROM users WHERE role = 'user'"
            ).fetchone()["total"]
        return {
            "documents": documents_count,
            "downloads": downloads_count,
            "users": users_count,
            "subscriptions": 0,
        }

    @application.get("/api/payments/status")
    def payment_status() -> dict[str, Any]:
        return {
            "provider": "Mixx by Yas",
            "enabled": False,
            "message": (
                "Le paiement Mixx by Yas est en attente du compte marchand "
                "et de la documentation API."
            ),
        }

    return application


app = create_app()

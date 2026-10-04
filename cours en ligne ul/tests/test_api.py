from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

from main import create_app
from create_admin import create_admin_account


def make_client(directory: str) -> TestClient:
    base = Path(directory)
    app = create_app(
        database_path=base / "test.sqlite3",
        uploads_path=base / "uploads",
        secret_key="test-signing-key-that-is-not-used-in-production",
        admin_email="admin@example.com",
        admin_password="correct-horse-battery",
    )
    return TestClient(app)


def get_admin_token(client: TestClient) -> str:
    response = client.post(
        "/api/auth/login",
        json={"email": "ADMIN@example.com", "password": "correct-horse-battery"},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def test_admin_auth_and_protected_routes() -> None:
    with TemporaryDirectory() as temporary:
        with make_client(temporary) as client:
            assert client.get("/api/admin/users").status_code == 401
            token = get_admin_token(client)
            headers = {"Authorization": f"Bearer {token}"}
            users = client.get("/api/admin/users", headers=headers)
            assert users.status_code == 200
            assert users.json()[0]["role"] == "admin"


def test_user_registration_cannot_access_admin_endpoints() -> None:
    with TemporaryDirectory() as temporary:
        with make_client(temporary) as client:
            created = client.post(
                "/api/auth/register",
                json={"email": "student@example.com", "password": "a-long-password"},
            )
            assert created.status_code == 201
            token = created.json()["access_token"]
            response = client.get(
                "/api/admin/users",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert response.status_code == 403


def test_admin_uploads_pdf_and_public_can_download_it() -> None:
    with TemporaryDirectory() as temporary:
        with make_client(temporary) as client:
            token = get_admin_token(client)
            headers = {"Authorization": f"Bearer {token}"}
            uploaded = client.post(
                "/api/admin/documents",
                headers=headers,
                data={
                    "title": "Document test",
                    "type": "Cours",
                    "institution": "FDS",
                    "subject": "Chimie",
                    "year": "2026",
                },
                files={"file": ("test.pdf", b"%PDF-1.7\ncontenu", "application/pdf")},
            )
            assert uploaded.status_code == 201
            document_id = uploaded.json()["id"]
            public_list = client.get("/api/documents")
            assert public_list.status_code == 200
            assert public_list.json()[0]["id"] == document_id
            file_response = client.get(f"/api/documents/{document_id}/file")
            assert file_response.status_code == 200
            assert file_response.content.startswith(b"%PDF-")


def test_public_static_pdf_can_be_downloaded_from_configured_documents_path() -> None:
    with TemporaryDirectory() as temporary:
        base = Path(temporary)
        documents_path = base / "documents"
        pdf_path = documents_path / "fds" / "mathematiques" / "analyse.pdf"
        pdf_path.parent.mkdir(parents=True)
        pdf_path.write_bytes(b"%PDF-1.7\ncontenu")
        app = create_app(
            database_path=base / "test.sqlite3",
            uploads_path=base / "uploads",
            documents_path=documents_path,
            secret_key="test-signing-key-that-is-not-used-in-production",
            admin_email="admin@example.com",
            admin_password="correct-horse-battery",
        )
        with TestClient(app) as client:
            response = client.get("/documents/fds/mathematiques/analyse.pdf")
            assert response.status_code == 200
            assert response.content.startswith(b"%PDF-")
            assert response.headers["content-type"] == "application/pdf"
            assert "attachment" in response.headers["content-disposition"]


def test_concours_pdf_can_be_previewed_inline_and_downloaded() -> None:
    with TemporaryDirectory() as temporary:
        with make_client(temporary) as client:
            token = get_admin_token(client)
            headers = {"Authorization": f"Bearer {token}"}
            uploaded = client.post(
                "/api/admin/documents",
                headers=headers,
                data={
                    "title": "Concours FDD 2025",
                    "type": "Concours",
                    "institution": "FDD",
                    "subject": "Droit",
                    "year": "2025",
                },
                files={"file": ("concours.pdf", b"%PDF-1.7\ncontenu", "application/pdf")},
            )
            assert uploaded.status_code == 201
            document_id = uploaded.json()["id"]

            preview = client.get(f"/api/documents/{document_id}/preview")
            assert preview.status_code == 200
            assert preview.content.startswith(b"%PDF-")
            assert preview.headers["content-type"] == "application/pdf"
            assert preview.headers["content-disposition"].startswith("inline;")

            download = client.get(f"/api/documents/{document_id}/file")
            assert download.status_code == 200
            assert download.headers["content-disposition"].startswith("attachment;")


def test_student_concours_submission_requires_review_before_publication() -> None:
    with TemporaryDirectory() as temporary:
        with make_client(temporary) as client:
            assert client.post(
                "/api/documents/submissions",
                data={
                    "title": "Épreuve proposée",
                    "institution": "FDD",
                    "subject": "Droit",
                    "year": "2025",
                    "rights_confirmed": "true",
                },
                files={"file": ("epreuve.pdf", b"%PDF-1.7\ncontenu", "application/pdf")},
            ).status_code == 401

            registered = client.post(
                "/api/auth/register",
                json={"email": "student@example.com", "password": "a-long-password"},
            )
            assert registered.status_code == 201
            student_token = registered.json()["access_token"]
            submitted = client.post(
                "/api/documents/submissions",
                headers={"Authorization": f"Bearer {student_token}"},
                data={
                    "title": "Épreuve proposée",
                    "institution": "FDD",
                    "subject": "Droit",
                    "year": "2025",
                    "rights_confirmed": "true",
                },
                files={"file": ("epreuve.pdf", b"%PDF-1.7\ncontenu", "application/pdf")},
            )
            assert submitted.status_code == 201
            document_id = submitted.json()["id"]
            assert submitted.json()["status"] == "pending"
            assert client.get("/api/documents").json() == []
            assert client.get(f"/api/documents/{document_id}/preview").status_code == 404
            assert client.get(f"/api/documents/{document_id}/file").status_code == 404

            admin_token = get_admin_token(client)
            admin_headers = {"Authorization": f"Bearer {admin_token}"}
            admin_documents = client.get(
                "/api/admin/documents",
                headers=admin_headers,
            )
            assert admin_documents.status_code == 200
            assert admin_documents.json()[0]["submitter_email"] == "student@example.com"
            assert admin_documents.json()[0]["status"] == "pending"

            admin_preview = client.get(
                f"/api/admin/documents/{document_id}/preview",
                headers=admin_headers,
            )
            assert admin_preview.status_code == 200
            assert admin_preview.headers["content-disposition"].startswith("inline;")

            approved = client.post(
                f"/api/admin/documents/{document_id}/approve",
                headers=admin_headers,
            )
            assert approved.status_code == 200
            assert approved.json()["status"] == "published"
            public_documents = client.get("/api/documents").json()
            assert len(public_documents) == 1
            assert public_documents[0]["id"] == document_id
            assert client.get(f"/api/documents/{document_id}/preview").status_code == 200
            assert client.get(f"/api/documents/{document_id}/file").status_code == 200


def test_student_submission_emails_admin_a_direct_review_link() -> None:
    with TemporaryDirectory() as temporary:
        with patch.dict(
            "os.environ",
            {
                "SMTP_HOST": "smtp.example.com",
                "SMTP_PORT": "587",
                "SMTP_USERNAME": "sender@example.com",
                "SMTP_PASSWORD": "test-smtp-password",
                "SMTP_FROM_EMAIL": "sender@example.com",
                "SUBMISSION_NOTIFICATION_EMAIL": "adolpheamegnekou0@gmail.com",
                "DOCCAMPUS_PUBLIC_URL": "https://doccampus.example",
            },
        ):
            with patch("main.smtplib.SMTP") as smtp_factory:
                smtp = smtp_factory.return_value.__enter__.return_value
                with make_client(temporary) as client:
                    registered = client.post(
                        "/api/auth/register",
                        json={"email": "student@example.com", "password": "a-long-password"},
                    )
                    token = registered.json()["access_token"]
                    response = client.post(
                        "/api/documents/submissions",
                        headers={"Authorization": f"Bearer {token}"},
                        data={
                            "title": "Épreuve de mathématiques",
                            "institution": "FDS",
                            "subject": "Mathématiques",
                            "year": "2026",
                            "rights_confirmed": "true",
                        },
                        files={"file": ("epreuve.pdf", b"%PDF-1.7\ncontenu", "application/pdf")},
                    )

                assert response.status_code == 201
                assert response.json()["email_sent"] is True
                assert "envoyé par e-mail" in response.json()["email_message"]
                sent_message = smtp.send_message.call_args.args[0]
                assert sent_message["To"] == "adolpheamegnekou0@gmail.com"
                assert (
                    f"https://doccampus.example/admin.html?submission={response.json()['id']}"
                    in sent_message.get_content()
                )


def test_submission_stays_pending_and_reports_smtp_failure() -> None:
    with TemporaryDirectory() as temporary:
        with patch.dict(
            "os.environ",
            {
                "SMTP_HOST": "smtp.example.com",
                "SMTP_PORT": "587",
                "SMTP_USERNAME": "",
                "SMTP_PASSWORD": "test-smtp-password",
                "SMTP_FROM_EMAIL": "sender@example.com",
                "SUBMISSION_NOTIFICATION_EMAIL": "adolpheamegnekou0@gmail.com",
                "DOCCAMPUS_PUBLIC_URL": "https://doccampus.example",
            },
        ):
            with patch("main.smtplib.SMTP") as smtp_factory:
                smtp = smtp_factory.return_value.__enter__.return_value
                smtp.send_message.side_effect = OSError("SMTP unavailable")
                with make_client(temporary) as client:
                    registered = client.post(
                        "/api/auth/register",
                        json={"email": "student@example.com", "password": "a-long-password"},
                    )
                    response = client.post(
                        "/api/documents/submissions",
                        headers={
                            "Authorization": f"Bearer {registered.json()['access_token']}"
                        },
                        data={
                            "title": "Épreuve de droit",
                            "institution": "FDD",
                            "subject": "Droit",
                            "year": "2026",
                            "rights_confirmed": "true",
                        },
                        files={"file": ("epreuve.pdf", b"%PDF-1.7\ncontenu", "application/pdf")},
                    )
                    admin_documents = client.get(
                        "/api/admin/documents",
                        headers={"Authorization": f"Bearer {get_admin_token(client)}"},
                    )

                assert response.status_code == 201
                assert response.json()["email_sent"] is False
                assert "l’e-mail n’a pas pu être envoyé" in response.json()["email_message"]
                assert admin_documents.status_code == 200
                assert admin_documents.json()[0]["status"] == "pending"


def test_existing_database_migrates_documents_as_published() -> None:
    with TemporaryDirectory() as temporary:
        base = Path(temporary)
        database_path = base / "test.sqlite3"
        connection = sqlite3.connect(database_path)
        try:
            connection.executescript(
                """
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT NOT NULL UNIQUE,
                    password_salt BLOB NOT NULL,
                    password_hash BLOB NOT NULL,
                    role TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE documents (
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
                CREATE TABLE categories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE
                );
                INSERT INTO documents
                    (id, title, type, institution, subject, year, file_name, created_at)
                VALUES
                    ('legacy-id', 'Ancien document', 'Concours', 'FDD', 'Droit', '2024',
                     'legacy.pdf', '2024-01-01T00:00:00+00:00');
                """
            )
        finally:
            connection.close()
        app = create_app(
            database_path=database_path,
            uploads_path=base / "uploads",
            secret_key="test-signing-key-that-is-not-used-in-production",
            admin_email="admin@example.com",
            admin_password="correct-horse-battery",
        )
        with TestClient(app) as client:
            public_documents = client.get("/api/documents")
            assert public_documents.status_code == 200
            assert public_documents.json()[0]["id"] == "legacy-id"
            admin_documents = client.get(
                "/api/admin/documents",
                headers={"Authorization": f"Bearer {get_admin_token(client)}"},
            )
            assert admin_documents.status_code == 200
            assert admin_documents.json()[0]["status"] == "published"
            assert admin_documents.json()[0]["submitted_by"] is None


def test_admin_can_reject_pending_concours_submission() -> None:
    with TemporaryDirectory() as temporary:
        with make_client(temporary) as client:
            registration = client.post(
                "/api/auth/register",
                json={"email": "student@example.com", "password": "a-long-password"},
            ).json()
            submitted = client.post(
                "/api/documents/submissions",
                headers={"Authorization": f"Bearer {registration['access_token']}"},
                data={
                    "title": "Épreuve à refuser",
                    "institution": "FDD",
                    "subject": "Droit",
                    "year": "2025",
                    "rights_confirmed": "true",
                },
                files={"file": ("epreuve.pdf", b"%PDF-1.7\ncontenu", "application/pdf")},
            )
            assert submitted.status_code == 201
            document_id = submitted.json()["id"]

            admin_headers = {"Authorization": f"Bearer {get_admin_token(client)}"}
            response = client.delete(
                f"/api/admin/documents/{document_id}/submission",
                headers=admin_headers,
            )
            assert response.status_code == 204
            assert client.get("/api/admin/documents", headers=admin_headers).json() == []
            assert client.get(
                f"/api/admin/documents/{document_id}/preview",
                headers=admin_headers,
            ).status_code == 404


def test_missing_static_pdf_returns_a_clear_not_found_message() -> None:
    with TemporaryDirectory() as temporary:
        base = Path(temporary)
        app = create_app(
            database_path=base / "test.sqlite3",
            uploads_path=base / "uploads",
            documents_path=base / "documents",
            secret_key="test-signing-key-that-is-not-used-in-production",
            admin_email="admin@example.com",
            admin_password="correct-horse-battery",
        )
        with TestClient(app) as client:
            response = client.get("/documents/missing.pdf")
            assert response.status_code == 404
            assert "PDF indisponible" in response.text


def test_static_pdf_can_be_previewed_inline() -> None:
    with TemporaryDirectory() as temporary:
        base = Path(temporary)
        documents_path = base / "documents"
        pdf_path = documents_path / "concours" / "fdd-2025.pdf"
        pdf_path.parent.mkdir(parents=True)
        pdf_path.write_bytes(b"%PDF-1.7\ncontenu")
        app = create_app(
            database_path=base / "test.sqlite3",
            uploads_path=base / "uploads",
            documents_path=documents_path,
            secret_key="test-signing-key-that-is-not-used-in-production",
            admin_email="admin@example.com",
            admin_password="correct-horse-battery",
        )
        with TestClient(app) as client:
            response = client.get("/preview/concours/fdd-2025.pdf")
            assert response.status_code == 200
            assert response.content.startswith(b"%PDF-")
            assert response.headers["content-type"] == "application/pdf"
            assert response.headers["content-disposition"].startswith("inline;")


def test_upload_rejects_non_pdf_content() -> None:
    with TemporaryDirectory() as temporary:
        with make_client(temporary) as client:
            token = get_admin_token(client)
            response = client.post(
                "/api/admin/documents",
                headers={"Authorization": f"Bearer {token}"},
                data={
                    "title": "Pas un PDF",
                    "type": "Cours",
                    "institution": "FDS",
                    "subject": "Test",
                    "year": "2026",
                },
                files={"file": ("fake.pdf", b"not a pdf", "application/pdf")},
            )
            assert response.status_code == 415


def test_default_categories_cannot_be_deleted() -> None:
    with TemporaryDirectory() as temporary:
        with make_client(temporary) as client:
            token = get_admin_token(client)
            categories = client.get("/api/categories").json()
            assert "Concours" in {category["name"] for category in categories}
            course = next(category for category in categories if category["name"] == "Cours")
            response = client.delete(
                f"/api/admin/categories/{course['id']}",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert response.status_code == 409
            concours = next(category for category in categories if category["name"] == "Concours")
            response = client.delete(
                f"/api/admin/categories/{concours['id']}",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert response.status_code == 409


def test_admin_can_manage_custom_categories_and_view_live_statistics() -> None:
    with TemporaryDirectory() as temporary:
        with make_client(temporary) as client:
            token = get_admin_token(client)
            headers = {"Authorization": f"Bearer {token}"}
            created = client.post(
                "/api/admin/categories",
                headers=headers,
                json={"name": "Mémoire"},
            )
            assert created.status_code == 201
            category_id = created.json()["id"]
            stats = client.get("/api/admin/stats", headers=headers)
            assert stats.status_code == 200
            assert stats.json() == {
                "documents": 0,
                "downloads": 0,
                "users": 0,
                "subscriptions": 0,
            }
            removed = client.delete(
                f"/api/admin/categories/{category_id}",
                headers=headers,
            )
            assert removed.status_code == 204


def test_payment_endpoint_reports_unconfigured_provider_honestly() -> None:
    with TemporaryDirectory() as temporary:
        with make_client(temporary) as client:
            response = client.get("/api/payments/status")
            assert response.status_code == 200
            assert response.json()["provider"] == "Mixx by Yas"
            assert response.json()["enabled"] is False


def test_admin_email_is_reserved_from_public_registration() -> None:
    with TemporaryDirectory() as temporary:
        with make_client(temporary) as client:
            response = client.post(
                "/api/auth/register",
                json={"email": "admin@example.com", "password": "another-long-password"},
            )
            assert response.status_code == 409


def test_category_in_use_cannot_be_deleted() -> None:
    with TemporaryDirectory() as temporary:
        with make_client(temporary) as client:
            token = get_admin_token(client)
            headers = {"Authorization": f"Bearer {token}"}
            category = client.post(
                "/api/admin/categories",
                headers=headers,
                json={"name": "Mémoire"},
            ).json()
            uploaded = client.post(
                "/api/admin/documents",
                headers=headers,
                data={
                    "title": "Mémoire test",
                    "type": "Mémoire",
                    "institution": "FDS",
                    "subject": "Test",
                    "year": "2026",
                },
                files={"file": ("memoire.pdf", b"%PDF-1.7\ncontenu", "application/pdf")},
            )
            assert uploaded.status_code == 201
            response = client.delete(
                f"/api/admin/categories/{category['id']}",
                headers=headers,
            )
            assert response.status_code == 409


def test_admin_can_remove_documents_and_regular_accounts() -> None:
    with TemporaryDirectory() as temporary:
        with make_client(temporary) as client:
            token = get_admin_token(client)
            headers = {"Authorization": f"Bearer {token}"}
            new_user = client.post(
                "/api/auth/register",
                json={"email": "remove-me@example.com", "password": "long-enough-password"},
            ).json()["user"]
            uploaded = client.post(
                "/api/admin/documents",
                headers=headers,
                data={
                    "title": "À supprimer",
                    "type": "Cours",
                    "institution": "FDS",
                    "subject": "Test",
                    "year": "2026",
                },
                files={"file": ("delete.pdf", b"%PDF-1.7\ncontenu", "application/pdf")},
            ).json()

            assert client.delete(
                f"/api/admin/users/{new_user['id']}",
                headers=headers,
            ).status_code == 204
            admin_id = client.get("/api/auth/me", headers=headers).json()["id"]
            assert client.delete(
                f"/api/admin/users/{admin_id}",
                headers=headers,
            ).status_code == 409
            assert client.delete(
                f"/api/admin/documents/{uploaded['id']}",
                headers=headers,
            ).status_code == 204
            assert client.get(f"/api/documents/{uploaded['id']}/file").status_code == 404


def test_create_admin_cli_persists_account_with_compatible_password_hash() -> None:
    with TemporaryDirectory() as temporary:
        database_path = Path(temporary) / "users.sqlite3"
        assert create_admin_account(
            database_path,
            "New-Admin@example.com",
            "a-very-long-admin-password",
        )
        connection = sqlite3.connect(database_path)
        try:
            user = connection.execute(
                "SELECT email, password_salt, password_hash, role FROM users"
            ).fetchone()
        finally:
            connection.close()
        assert user is not None
        email, salt, password_hash, role = user
        assert email == "new-admin@example.com"
        assert role == "admin"
        assert hashlib.pbkdf2_hmac(
            "sha256",
            b"a-very-long-admin-password",
            salt,
            310_000,
        ) == password_hash
        assert not create_admin_account(
            database_path,
            email,
            "another-very-long-password",
        )

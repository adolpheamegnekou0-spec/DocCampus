from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory

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
            course = next(category for category in categories if category["name"] == "Cours")
            response = client.delete(
                f"/api/admin/categories/{course['id']}",
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

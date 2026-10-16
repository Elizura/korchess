"""Google sign-in must not hand over accounts it can't vouch for; verify must not leak accounts."""

from __future__ import annotations

import os
import secrets
import unittest
from unittest.mock import ANY, AsyncMock, MagicMock, patch

os.environ.setdefault("DATABASE_URL", "postgresql://test@localhost:1/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:1/0")
os.environ.setdefault("JWT_SECRET", "test")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from dependencies import get_db
from routers import auth as auth_router

ID_TOKEN = secrets.token_urlsafe(16)


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(auth_router.router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: MagicMock()
    return TestClient(app)


def _user(email_verified: bool) -> dict:
    return {
        "id": "user-1",
        "email": "a@b.com",
        "password_hash": "x",
        "email_verified": email_verified,
    }


class GoogleSignInTest(unittest.TestCase):
    def _sign_in(self, id_info: dict, existing: dict | None):
        with patch.object(auth_router, "GOOGLE_CLIENT_ID", "client-id"), \
                patch.object(auth_router.google_id_token, "verify_oauth2_token", return_value=id_info), \
                patch.object(auth_router, "get_user_by_email", return_value=existing), \
                patch.object(auth_router, "create_auth_user") as create, \
                patch.object(auth_router, "mark_email_verified_and_clear_password") as claim, \
                patch.object(auth_router, "delete_user_refresh_tokens") as revoke, \
                patch.object(auth_router, "store_refresh_token") as store, \
                patch.object(auth_router, "track_server_event", AsyncMock()):
            res = _client().post("/api/v1/auth/google", json={"id_token": ID_TOKEN})
        return res, create, claim, revoke, store

    def test_unverified_google_email_is_rejected(self) -> None:
        for claim_value in (False, None, "false"):
            info = {"email": "a@b.com"}
            if claim_value is not None:
                info["email_verified"] = claim_value
            res, create, claim, _, store = self._sign_in(info, _user(email_verified=True))
            self.assertEqual(res.status_code, 400)
            create.assert_not_called()
            claim.assert_not_called()
            store.assert_not_called()

    def test_pre_registered_unverified_account_loses_its_password(self) -> None:
        res, create, claim, revoke, store = self._sign_in(
            {"email": "A@b.com", "email_verified": True}, _user(email_verified=False)
        )
        self.assertEqual(res.status_code, 200)
        create.assert_not_called()
        claim.assert_called_once_with(ANY, "user-1")
        revoke.assert_called_once_with(ANY, "user-1")
        store.assert_called_once()

    def test_verified_account_is_left_alone(self) -> None:
        res, create, claim, revoke, store = self._sign_in(
            {"email": "a@b.com", "email_verified": True}, _user(email_verified=True)
        )
        self.assertEqual(res.status_code, 200)
        create.assert_not_called()
        claim.assert_not_called()
        revoke.assert_not_called()
        store.assert_called_once()

    def test_new_google_user_is_created_verified(self) -> None:
        res, create, claim, _, _ = self._sign_in({"email": "a@b.com", "email_verified": "true"}, None)
        self.assertEqual(res.status_code, 200)
        create.assert_called_once()
        self.assertTrue(create.call_args.kwargs["email_verified"])
        claim.assert_not_called()


class VerifyDoesNotEnumerateTest(unittest.TestCase):
    def test_unknown_email_looks_like_a_wrong_code(self) -> None:
        with patch.object(auth_router, "get_user_by_email", return_value=None):
            res = _client().post(
                "/api/v1/auth/verify",
                json={"email": "nobody@b.com", "code": "123456", "password": secrets.token_urlsafe(12)},
            )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["detail"], "Invalid code.")


if __name__ == "__main__":
    unittest.main()

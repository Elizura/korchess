"""Email sign-up recovery: re-signup, resend, reset, and code attempt limits."""

from __future__ import annotations

import os
import secrets
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import ANY, AsyncMock, MagicMock, patch

os.environ.setdefault("DATABASE_URL", "postgresql://test@localhost:1/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:1/0")
os.environ.setdefault("JWT_SECRET", "test")

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from dependencies import get_db
from routers import auth as auth_router

# Throwaway passwords generated per run, so no password literals live in the repo
PASSWORD = secrets.token_urlsafe(12)
OTHER_PASSWORD = secrets.token_urlsafe(12)


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(auth_router.router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: MagicMock()
    return TestClient(app)


def _unverified_user(code_issued_seconds_ago: int | None = None) -> dict:
    user = {
        "id": "user-1",
        "email": "a@b.com",
        "password_hash": "x",
        "email_verified": False,
        "verification_code": None,
        "verification_code_expires_at": None,
    }
    if code_issued_seconds_ago is not None:
        issued = datetime.now(timezone.utc) - timedelta(seconds=code_issued_seconds_ago)
        user["verification_code"] = "123456"
        user["verification_code_expires_at"] = issued + timedelta(
            minutes=auth_router.VERIFICATION_CODE_EXPIRY_MINUTES
        )
    return user


class CheckEmailCodeTest(unittest.TestCase):
    def _check(self, state: dict | None, code: str = "123456") -> None:
        with patch.object(auth_router, "consume_email_code_attempt", return_value=state):
            auth_router._check_email_code(MagicMock(), "user-1", code)

    def _state(self, attempts: int = 1, minutes_left: int = 5) -> dict:
        return {
            "verification_attempts": attempts,
            "verification_code": "123456",
            "verification_code_expires_at": datetime.now(timezone.utc) + timedelta(minutes=minutes_left),
        }

    def test_correct_code_passes(self) -> None:
        self._check(self._state())

    def test_wrong_code_rejected(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            self._check(self._state(), code="000000")
        self.assertEqual(ctx.exception.detail, "Invalid code.")

    def test_correct_code_rejected_after_attempt_limit(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            self._check(self._state(attempts=auth_router.MAX_CODE_ATTEMPTS + 1))
        self.assertIn("Too many incorrect attempts", ctx.exception.detail)

    def test_expired_code_rejected(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            self._check(self._state(minutes_left=-1))
        self.assertIn("expired", ctx.exception.detail)

    def test_missing_code_rejected(self) -> None:
        with self.assertRaises(HTTPException):
            self._check({"verification_attempts": 1, "verification_code": None,
                         "verification_code_expires_at": None})


class SignupRecoveryTest(unittest.TestCase):
    def test_resignup_of_unverified_account_issues_new_code(self) -> None:
        send = MagicMock(return_value={"success": True})
        with patch.object(auth_router, "get_user_by_email", return_value=_unverified_user(600)), \
                patch.object(auth_router, "set_email_code") as set_code, \
                patch.object(auth_router, "send_verification_email", send):
            res = _client().post("/api/v1/auth/signup", json={"email": "a@b.com", "password": PASSWORD})

        self.assertEqual(res.status_code, 201)
        set_code.assert_called_once()
        self.assertIsNotNone(set_code.call_args.args[4])  # new password hash stored
        send.assert_called_once()

    def test_signup_of_verified_account_still_conflicts(self) -> None:
        user = {**_unverified_user(), "email_verified": True}
        with patch.object(auth_router, "get_user_by_email", return_value=user):
            res = _client().post("/api/v1/auth/signup", json={"email": "a@b.com", "password": PASSWORD})
        self.assertEqual(res.status_code, 409)

    def test_signup_reports_email_send_failure(self) -> None:
        with patch.object(auth_router, "get_user_by_email", return_value=None), \
                patch.object(auth_router, "create_auth_user"), \
                patch.object(auth_router, "track_server_event", AsyncMock()), \
                patch.object(auth_router, "send_verification_email",
                             return_value={"success": False, "error": "nope"}):
            res = _client().post("/api/v1/auth/signup", json={"email": "a@b.com", "password": PASSWORD})
        self.assertEqual(res.status_code, 503)

    def test_resend_code_respects_cooldown(self) -> None:
        with patch.object(auth_router, "get_user_by_email", return_value=_unverified_user(10)), \
                patch.object(auth_router, "set_email_code") as set_code:
            res = _client().post("/api/v1/auth/resend-code", json={"email": "a@b.com"})
        self.assertEqual(res.status_code, 429)
        set_code.assert_not_called()

    def test_resend_code_for_unknown_email_is_generic(self) -> None:
        with patch.object(auth_router, "get_user_by_email", return_value=None):
            res = _client().post("/api/v1/auth/resend-code", json={"email": "nobody@b.com"})
        self.assertEqual(res.status_code, 200)


class VerifyRequiresSignupPasswordTest(unittest.TestCase):
    """Re-signup replaces the password, so verifying must prove who chose it."""

    def _verify(self, password: str):
        import bcrypt

        user = {**_unverified_user(30),
                "password_hash": bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(4)).decode()}
        with patch.object(auth_router, "get_user_by_email", return_value=user), \
                patch.object(auth_router, "_check_email_code"), \
                patch.object(auth_router, "mark_email_verified") as mark, \
                patch.object(auth_router, "store_refresh_token"), \
                patch.object(auth_router, "track_server_event", AsyncMock()):
            res = _client().post(
                "/api/v1/auth/verify",
                json={"email": "a@b.com", "code": "123456", "password": password},
            )
        return res, mark

    def test_matching_password_verifies(self) -> None:
        res, mark = self._verify(PASSWORD)
        self.assertEqual(res.status_code, 200)
        mark.assert_called_once()

    def test_other_password_is_rejected(self) -> None:
        res, mark = self._verify(OTHER_PASSWORD)
        self.assertEqual(res.status_code, 400)
        mark.assert_not_called()

    def test_missing_password_is_rejected(self) -> None:
        res, mark = self._verify("")
        self.assertEqual(res.status_code, 400)
        mark.assert_not_called()


class ResetPasswordTest(unittest.TestCase):
    def test_reset_sets_password_revokes_sessions_and_signs_in(self) -> None:
        user = {**_unverified_user(30), "email_verified": True}
        with patch.object(auth_router, "get_user_by_email", return_value=user), \
                patch.object(auth_router, "_check_email_code") as check, \
                patch.object(auth_router, "reset_user_password") as reset, \
                patch.object(auth_router, "delete_user_refresh_tokens") as revoke, \
                patch.object(auth_router, "store_refresh_token"):
            res = _client().post(
                "/api/v1/auth/reset-password",
                json={"email": "a@b.com", "code": "123456", "password": PASSWORD},
            )

        self.assertEqual(res.status_code, 200)
        self.assertIn("access_token", res.json())
        check.assert_called_once()
        reset.assert_called_once()
        revoke.assert_called_once_with(ANY, "user-1")

    def test_reset_rejects_short_password(self) -> None:
        res = _client().post(
            "/api/v1/auth/reset-password",
            json={"email": "a@b.com", "code": "123456", "password": PASSWORD[:7]},
        )
        self.assertEqual(res.status_code, 400)


if __name__ == "__main__":
    unittest.main()

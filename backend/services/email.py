"""Email service for sending verification and password-reset codes via Resend."""

from __future__ import annotations

import logging
import os

import resend

logger = logging.getLogger(__name__)

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
# Must be an address on a domain verified in Resend. The default sandbox sender
# only delivers to the Resend account owner's own address.
FROM_EMAIL = os.environ.get("FROM_EMAIL", "Korchess <onboarding@resend.dev>")


def send_verification_email(email: str, code: str) -> dict:
    """Send a 6-digit sign-up verification code to the user's email.

    Returns {"success": True} on success or {"success": False, "error": "..."}.
    """
    return _send_code_email(
        email,
        code,
        subject="Verify your Korchess account",
        heading="Welcome to Korchess!",
        intro="Use the verification code below to complete your registration:",
    )


def send_password_reset_email(email: str, code: str) -> dict:
    """Send a 6-digit password-reset code to the user's email.

    Returns {"success": True} on success or {"success": False, "error": "..."}.
    """
    return _send_code_email(
        email,
        code,
        subject="Reset your Korchess password",
        heading="Reset your password",
        intro="Use the code below to choose a new password for your Korchess account:",
    )


def _send_code_email(email: str, code: str, *, subject: str, heading: str, intro: str) -> dict:
    if not RESEND_API_KEY:
        logger.warning("RESEND_API_KEY not set — skipping email to %s", email)
        return {"success": False, "error": "RESEND_API_KEY not configured"}

    resend.api_key = RESEND_API_KEY

    html = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 480px; margin: 0 auto; padding: 40px 20px;">
      <h1 style="color: #1a1a2e; margin-bottom: 24px;">{heading}</h1>
      <p style="color: #4a4a4a; font-size: 16px; line-height: 1.6;">
        {intro}
      </p>
      <div style="background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); border-radius: 12px; padding: 24px; text-align: center; margin: 32px 0;">
        <span style="font-size: 32px; font-weight: bold; letter-spacing: 8px; color: #22d3ee;">{code}</span>
      </div>
      <p style="color: #6b6b6b; font-size: 14px;">
        This code expires in 10 minutes. If you didn't request this, you can safely ignore this email.
      </p>
    </div>
    """

    try:
        params: resend.Emails.SendParams = {
            "from": FROM_EMAIL,
            "to": [email],
            "subject": subject,
            "html": html,
        }
        resend.Emails.send(params)
        return {"success": True}
    except Exception as exc:
        msg = str(exc)
        logger.error("Failed to send email to %s: %s", email, msg)
        return {"success": False, "error": msg}

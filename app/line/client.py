"""Thin wrapper around the LINE Messaging API.

Responsibilities:
* verify the ``x-line-signature`` HMAC so forged requests are rejected;
* send reply / push text messages;
* look up a user's display name.

When no channel access token is configured the client runs in *log-only* mode:
outbound messages are logged rather than sent, which lets the whole flow be
tested locally without real LINE credentials.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging

import httpx

from app.line.config import LineSettings, settings

logger = logging.getLogger("coin_master.line")

_TIMEOUT_SECONDS = 10.0
_MAX_LINE_TEXT = 5000  # LINE rejects text messages longer than 5000 chars.


class LineClient:
    def __init__(self, config: LineSettings = settings) -> None:
        self._config = config

    # -- security ------------------------------------------------------------
    def verify_signature(self, body: bytes, signature: str) -> bool:
        """Constant-time check of LINE's request signature."""
        if not self._config.channel_secret or not signature:
            return False
        digest = hmac.new(
            self._config.channel_secret.encode("utf-8"),
            body,
            hashlib.sha256,
        ).digest()
        expected = base64.b64encode(digest).decode("utf-8")
        return hmac.compare_digest(expected, signature)

    def sign(self, body: bytes) -> str:
        """Produce a signature for ``body`` (used by the local test harness)."""
        digest = hmac.new(
            self._config.channel_secret.encode("utf-8"),
            body,
            hashlib.sha256,
        ).digest()
        return base64.b64encode(digest).decode("utf-8")

    # -- outbound messages ---------------------------------------------------
    def reply(self, reply_token: str, text: str) -> None:
        if not self._config.reply_enabled:
            logger.info("[log-only] reply -> %s", text)
            return
        self._post(
            self._config.reply_endpoint,
            {"replyToken": reply_token, "messages": self._text_messages(text)},
        )

    def push(self, to_line_user_id: str, text: str) -> None:
        if not self._config.reply_enabled:
            logger.info("[log-only] push %s -> %s", to_line_user_id, text)
            return
        self._post(
            self._config.push_endpoint,
            {"to": to_line_user_id, "messages": self._text_messages(text)},
        )

    # -- profile -------------------------------------------------------------
    def get_display_name(self, line_user_id: str) -> str | None:
        if not self._config.reply_enabled:
            return None
        try:
            response = httpx.get(
                f"{self._config.profile_endpoint}/{line_user_id}",
                headers=self._auth_headers(),
                timeout=_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            return response.json().get("displayName")
        except httpx.HTTPError as error:  # non-fatal: display name is optional
            logger.warning("profile lookup failed for %s: %s", line_user_id, error)
            return None

    # -- internals -----------------------------------------------------------
    def _post(self, url: str, payload: dict) -> None:
        try:
            response = httpx.post(
                url,
                headers=self._auth_headers(),
                json=payload,
                timeout=_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
        except httpx.HTTPError as error:  # never let a send failure 500 the webhook
            logger.error("LINE send to %s failed: %s", url, error)

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._config.channel_access_token}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _text_messages(text: str) -> list[dict]:
        return [{"type": "text", "text": text[:_MAX_LINE_TEXT]}]

"""FastAPI entrypoint for the Coin-Master LINE loan bot.

Exposes the webhook LINE calls on ``POST /line-bot``.  The heavy lifting lives in
:class:`~app.service.LoanBotService`; this module only handles HTTP concerns:
signature verification, payload parsing and dispatching replies.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request, Response

from app.line import LineClient, WebhookRequest
from app.repository import build_repository
from app.repository.config import settings as supabase_settings
from app.service import LoanBotService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("coin_master")


app = FastAPI(title="Coin-Master LINE Loan Bot")
line_client = LineClient()
service = LoanBotService(build_repository())


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "storage": "supabase" if supabase_settings.enabled else "memory"}


@app.post("/line-bot")
async def line_bot(request: Request) -> Response:
    """Receive a LINE webhook. Always returns 200 unless the signature is bad."""
    body = await request.body()
    signature = request.headers.get("x-line-signature", "")
    if not line_client.verify_signature(body, signature):
        return Response(status_code=403, content="Invalid signature")

    payload = WebhookRequest.model_validate_json(body)
    replies = [reply for event in payload.events if (reply := _handle(event)) is not None]

    # LINE ignores the response body; we echo replies to aid local testing.
    return _ok({"handled": len(payload.events), "replies": replies})


def _handle(event) -> str | None:
    """Process one event and dispatch its reply; never raise back to the webhook."""
    try:
        reply = service.handle_event(event)
    except Exception:  # noqa: BLE001 - a single bad event must not fail the batch
        logger.exception("Failed to handle event %s", event.webhookEventId)
        return None
    if reply and event.replyToken:
        line_client.reply(event.replyToken, reply)
    return reply


def _ok(extra: dict) -> Response:
    import json

    return Response(
        content=json.dumps({"status": "ok", **extra}, ensure_ascii=False),
        media_type="application/json",
    )

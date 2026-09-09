import base64
import hashlib
import hmac
import json
from typing import Any

from httpx import ASGITransport, AsyncClient
from linebot.v3.webhook import WebhookParser
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from line_assistant.core.config import Settings
from line_assistant.db.models import LineUser, WebhookEvent
from line_assistant.line.dispatcher import EventDispatcher
from line_assistant.line.messages import BotMessage
from line_assistant.main import create_app


class FakeReplyClient:
    def __init__(self) -> None:
        self.replies: list[tuple[str, list[BotMessage]]] = []

    async def reply(self, reply_token: str, messages: list[BotMessage]) -> None:
        self.replies.append((reply_token, messages))

    async def close(self) -> None:
        return None


def signed_body(payload: dict[str, Any], secret: str) -> tuple[bytes, str]:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    signature = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()
    return body, signature


def follow_payload(*, event_id: str = "01TESTEVENT0000000000000001") -> dict[str, Any]:
    return {
        "destination": "U-bot",
        "events": [
            {
                "type": "follow",
                "webhookEventId": event_id,
                "deliveryContext": {"isRedelivery": False},
                "timestamp": 1_788_487_518_000,
                "source": {"type": "user", "userId": "U-webhook-user"},
                "replyToken": "reply-token",
                "mode": "active",
                "follow": {"isUnblocked": False},
            }
        ],
    }


async def test_webhook_verifies_signature_and_is_idempotent(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    secret = "test-channel-secret"
    fake_client = FakeReplyClient()
    settings = Settings(
        app_env="test",
        database_url="sqlite+aiosqlite://",
        line_channel_secret=secret,
        line_channel_access_token="test-token",
    )
    app = create_app(
        settings,
        session_factory=session_factory,
        reply_client=fake_client,
        webhook_parser=WebhookParser(secret),
    )
    body, signature = signed_body(follow_payload(), secret)

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            first = await client.post(
                "/webhooks/line",
                content=body,
                headers={"X-Line-Signature": signature, "Content-Type": "application/json"},
            )
            second = await client.post(
                "/webhooks/line",
                content=body,
                headers={"X-Line-Signature": signature, "Content-Type": "application/json"},
            )
            invalid = await client.post(
                "/webhooks/line",
                content=body,
                headers={"X-Line-Signature": "invalid", "Content-Type": "application/json"},
            )
            ready = await client.get("/health/ready")

    assert first.status_code == 200
    assert second.status_code == 200
    assert invalid.status_code == 400
    assert ready.status_code == 200
    assert len(fake_client.replies) == 1
    assert len(fake_client.replies[0][1]) == 1

    async with session_factory() as session:
        assert await session.scalar(select(func.count(WebhookEvent.id))) == 1
        assert await session.scalar(select(func.count(LineUser.id))) == 1


async def test_failed_event_is_rolled_back_and_retried(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: Any,
) -> None:
    secret = "retry-channel-secret"
    fake_client = FakeReplyClient()
    settings = Settings(
        app_env="test",
        database_url="sqlite+aiosqlite://",
        line_channel_secret=secret,
        line_channel_access_token="test-token",
    )
    app = create_app(
        settings,
        session_factory=session_factory,
        reply_client=fake_client,
        webhook_parser=WebhookParser(secret),
    )
    original_dispatch = EventDispatcher.dispatch
    attempts = 0

    async def fail_once(dispatcher: EventDispatcher, event: Any) -> list[BotMessage]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            await dispatcher.identities.ensure_user("U-should-rollback")
            raise RuntimeError("temporary failure")
        return await original_dispatch(dispatcher, event)

    monkeypatch.setattr(EventDispatcher, "dispatch", fail_once)
    body, signature = signed_body(
        follow_payload(event_id="01RETRYEVENT000000000000001"), secret
    )
    headers = {"X-Line-Signature": signature, "Content-Type": "application/json"}

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            failed = await client.post("/webhooks/line", content=body, headers=headers)
            retried = await client.post("/webhooks/line", content=body, headers=headers)

    assert failed.status_code == 500
    assert retried.status_code == 200
    assert attempts == 2
    assert len(fake_client.replies) == 1
    async with session_factory() as session:
        row = await session.scalar(select(WebhookEvent))
        assert row is not None
        assert row.status.value == "processed"
        assert row.attempt_count == 2
        rolled_back_user = await session.scalar(
            select(LineUser).where(LineUser.line_user_id == "U-should-rollback")
        )
        assert rolled_back_user is None

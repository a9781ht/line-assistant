from __future__ import annotations

import logging
from typing import cast

from fastapi import APIRouter, HTTPException, Request, Response, status
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import FlexMessage, TextMessage
from linebot.v3.webhook import WebhookParser
from linebot.v3.webhooks import Event
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from line_assistant.core.config import Settings
from line_assistant.core.errors import DomainError
from line_assistant.db.models import WebhookEvent, WebhookStatus
from line_assistant.line.client import ReplyClient
from line_assistant.line.dispatcher import EventDispatcher
from line_assistant.line.messages import BotMessage, text_message

logger = logging.getLogger(__name__)
router = APIRouter(tags=["LINE"])


# 在真正呼叫 LINE API 前，再用 LINE SDK 驗證即將回覆的訊息格式
# 如果意外產出不合法的 Flex 格式，系統會先攔下來，避免送出錯誤 LINE payload
def _message_is_valid(message: BotMessage) -> bool:
    try:
        if message.get("type") == "flex":
            FlexMessage.from_dict(message)
        else:
            TextMessage.from_dict(message)
    except Exception:
        return False
    return True


async def _claim_event(session: AsyncSession, event: Event) -> tuple[WebhookEvent, bool]:
    """
    webhook 事件的去重複處理。

    邏輯：
      1. 查詢 webhook_event_id 是否已存在；
      2. 若已成功處理，通常不再重跑商業邏輯；
      3. 若上次失敗，將狀態改回 PROCESSING，允許重試；
      4. 若不存在，建立新的 WebhookEvent；
      5. 若兩個請求同時競爭同一事件，靠資料庫 unique constraint 與 nested transaction 處理。
    """
    event_id = event.webhook_event_id
    existing = await session.scalar(
        select(WebhookEvent).where(WebhookEvent.webhook_event_id == event_id)
    )
    if existing is not None:
        existing.attempt_count += 1
        if existing.status is WebhookStatus.FAILED:
            existing.status = WebhookStatus.PROCESSING
            existing.error_summary = None
            existing.response_messages = []
            existing.reply_sent = False
            return existing, True
        return existing, False

    row = WebhookEvent(
        webhook_event_id=event_id,
        event_timestamp=event.timestamp,
        status=WebhookStatus.PROCESSING,
    )
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError:
        existing = await session.scalar(
            select(WebhookEvent).where(WebhookEvent.webhook_event_id == event_id)
        )
        if existing is None:
            raise
        existing.attempt_count += 1
        return existing, False
    return row, True


@router.post("/webhooks/line", status_code=status.HTTP_200_OK)
async def receive_line_webhook(request: Request) -> Response:
    """
    實際的 FastAPI endpoint

    處理順序：
      1. 讀取原始 bytes；
      2. 限制請求大小；
      3. 讀取 X-Line-Signature；
      4. 用 WebhookParser 驗證 LINE 簽章；
      5. 簽章通過才解析事件；
      6. 建立 EventDispatcher；
      7. 逐一處理事件；
      8. 將回覆訊息記到 WebhookEvent；
      9. commit 資料庫交易；
      10. 呼叫 LINE Reply API；
      11. 記錄回覆已送出；
      12. 回傳純文字 OK。
    """
    settings = cast(Settings, request.app.state.settings)
    body_bytes = await request.body()
    if len(body_bytes) > settings.max_request_bytes:
        raise HTTPException(status_code=413, detail="Request body too large")
    signature = request.headers.get("X-Line-Signature")
    if not signature:
        raise HTTPException(status_code=400, detail="Missing LINE signature")

    parser = cast(WebhookParser | None, request.app.state.webhook_parser)
    if parser is None:
        raise HTTPException(status_code=503, detail="LINE integration is not configured")
    try:
        events = cast(list[Event], parser.parse(body_bytes.decode("utf-8"), signature))
    except (InvalidSignatureError, UnicodeDecodeError) as error:
        raise HTTPException(status_code=400, detail="Invalid LINE signature") from error

    factory = cast(async_sessionmaker[AsyncSession], request.app.state.session_factory)
    pending_replies: list[tuple[WebhookEvent, str, list[BotMessage]]] = []
    async with factory() as session:
        dispatcher = EventDispatcher(
            session,
            timezone=settings.timezone,
            draft_ttl_minutes=settings.draft_ttl_minutes,
            max_amount=settings.max_amount,
        )
        for event in events:
            row, is_new = await _claim_event(session, event)
            reply_token = cast(str | None, getattr(event, "reply_token", None))
            if not is_new:
                if not row.reply_sent and row.response_messages and reply_token:
                    pending_replies.append((row, reply_token, row.response_messages))
                continue
            try:
                async with session.begin_nested():
                    messages = await dispatcher.dispatch(event)
            except DomainError as error:
                messages = [text_message(str(error))]
            except Exception as error:
                row.status = WebhookStatus.FAILED
                row.error_summary = type(error).__name__
                logger.exception(
                    "webhook_event_failed",
                    extra={"webhook_event_id": row.webhook_event_id},
                )
                await session.commit()
                raise HTTPException(status_code=500, detail="Webhook processing failed") from error

            if not all(_message_is_valid(message) for message in messages):
                raise HTTPException(status_code=500, detail="Generated an invalid LINE message")
            row.response_messages = messages
            row.status = WebhookStatus.PROCESSED
            if reply_token and messages:
                pending_replies.append((row, reply_token, messages))
            else:
                row.reply_sent = True
        await session.commit()

        reply_client = cast(ReplyClient, request.app.state.reply_client)
        for row, reply_token, messages in pending_replies:
            try:
                await reply_client.reply(reply_token, messages)
            except Exception as error:
                logger.exception(
                    "line_reply_failed",
                    extra={"webhook_event_id": row.webhook_event_id},
                )
                raise HTTPException(status_code=502, detail="LINE reply failed") from error
            row.reply_sent = True
            await session.commit()

    return Response(content="OK", media_type="text/plain")

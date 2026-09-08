from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from line_assistant.db.models import ConversationSession


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


# 管理多步驟對話流程狀態。
# LINE 記帳不是一個 HTTP request 就完成，每一步都是不同 webhook 事件，因此需要將目前進度存在資料庫。
# 其中的 version 很重要，因為 LINE 可能保留舊訊息，用戶點舊按鈕時，按鈕中帶的舊版本與資料庫目前版本不同，系統就拒絕那次操作。
class ConversationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(
        self, scope_id: uuid.UUID, actor_user_id: uuid.UUID
    ) -> ConversationSession | None:
        """取得目前對話狀態，若已過期則自動清除。"""
        conversation = await self.session.scalar(
            select(ConversationSession).where(
                ConversationSession.scope_id == scope_id,
                ConversationSession.actor_user_id == actor_user_id,
            )
        )
        if conversation is not None and _as_utc(conversation.expires_at) <= datetime.now(UTC):
            await self.session.delete(conversation)
            await self.session.flush()
            return None
        return conversation

    async def set(
        self,
        scope_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        *,
        flow: str,
        state: str,
        payload: dict[str, Any],
        ttl_minutes: int,
    ) -> ConversationSession:
        """建立或完整覆寫流程，若已有流程，會增加 version。"""
        conversation = await self.get(scope_id, actor_user_id)
        expires_at = datetime.now(UTC) + timedelta(minutes=ttl_minutes)
        if conversation is None:
            conversation = ConversationSession(
                scope_id=scope_id,
                actor_user_id=actor_user_id,
                flow=flow,
                state=state,
                payload=dict(payload),
                expires_at=expires_at,
            )
            self.session.add(conversation)
        else:
            conversation.flow = flow
            conversation.state = state
            conversation.payload = dict(payload)
            conversation.version += 1
            conversation.expires_at = expires_at
        await self.session.flush()
        return conversation

    async def update(
        self,
        conversation: ConversationSession,
        *,
        state: str | None = None,
        payload: dict[str, Any] | None = None,
        ttl_minutes: int,
    ) -> ConversationSession:
        """更新現有流程的部分內容，每次更新也會增加 version。"""
        if state is not None:
            conversation.state = state
        if payload is not None:
            conversation.payload = dict(payload)
        conversation.version += 1
        conversation.expires_at = datetime.now(UTC) + timedelta(minutes=ttl_minutes)
        await self.session.flush()
        return conversation

    async def clear(self, scope_id: uuid.UUID, actor_user_id: uuid.UUID) -> None:
        """完成、取消或失敗時清除對話流程。"""
        await self.session.execute(
            delete(ConversationSession).where(
                ConversationSession.scope_id == scope_id,
                ConversationSession.actor_user_id == actor_user_id,
            )
        )

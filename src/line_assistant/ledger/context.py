from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from line_assistant.db.models import (
    AssistantScope,
    Ledger,
    LineUser,
    ScopeMembership,
    ScopeType,
)
from line_assistant.ledger.catalog import CategoryService


@dataclass(frozen=True, slots=True)
class ScopeContext:
    user: LineUser          # 目前操作的 LINE 用戶
    scope: AssistantScope   # 私人、群組範圍
    ledger: Ledger          # 對應的帳本



# 處理「LINE 身分」與「系統帳本範圍」的對應。
class IdentityService:
    def __init__(self, session: AsyncSession, *, timezone: str = "Asia/Taipei") -> None:
        self.session = session
        self.timezone = timezone

    async def ensure_user(
        self,
        line_user_id: str,
        *,
        display_name: str | None = None,
        is_friend: bool | None = None,
    ) -> LineUser:
        """確認 LINE 用戶已存在，若不存在則建立新用戶。"""
        user = await self.session.scalar(
            select(LineUser).where(LineUser.line_user_id == line_user_id)
        )
        if user is None:
            user = LineUser(
                line_user_id=line_user_id,
                display_name=display_name,
                is_friend=bool(is_friend),
            )
            self.session.add(user)
            await self.session.flush()
        else:
            if display_name:
                user.display_name = display_name
            if is_friend is not None:
                user.is_friend = is_friend
        return user

    async def ensure_scope(
        self,
        scope_type: ScopeType,
        line_source_id: str,
        *,
        actor: LineUser | None = None,
        name: str | None = None,
    ) -> tuple[AssistantScope, Ledger]:
        """確認私人或群組範圍已存在，若不存在則建立新範圍與對應帳本。"""
        scope = await self.session.scalar(
            select(AssistantScope).where(
                AssistantScope.scope_type == scope_type,
                AssistantScope.line_source_id == line_source_id,
            )
        )
        if scope is None:
            scope = AssistantScope(
                scope_type=scope_type,
                line_source_id=line_source_id,
                owner_user_id=actor.id if scope_type is ScopeType.PERSONAL and actor else None,
                name=name,
                timezone=self.timezone,
            )
            self.session.add(scope)
            await self.session.flush()
        else:
            scope.is_active = True
            if name:
                scope.name = name

        ledger = await self.session.scalar(select(Ledger).where(Ledger.scope_id == scope.id))
        if ledger is None:
            ledger = Ledger(scope_id=scope.id)
            self.session.add(ledger)
            await self.session.flush()
            await CategoryService(self.session).seed_fixed_categories(ledger)

        if actor is not None:
            await self.ensure_membership(scope, actor)
        return scope, ledger

    async def ensure_context(
        self,
        scope_type: ScopeType,
        line_source_id: str,
        line_user_id: str,
        *,
        is_friend: bool | None = None,
    ) -> ScopeContext:
        """確保用戶、範圍與帳本都已存在，並回傳 LINE 事件所需的 context。"""
        user = await self.ensure_user(line_user_id, is_friend=is_friend)
        scope, ledger = await self.ensure_scope(
            scope_type,
            line_source_id,
            actor=user,
        )
        return ScopeContext(user=user, scope=scope, ledger=ledger)

    async def ensure_membership(self, scope: AssistantScope, user: LineUser) -> None:
        membership = await self.session.get(ScopeMembership, (scope.id, user.id))
        if membership is None:
            self.session.add(ScopeMembership(scope_id=scope.id, user_id=user.id))
        else:
            membership.is_active = True
            membership.left_at = None
        await self.session.flush()

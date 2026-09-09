from __future__ import annotations

import calendar
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from line_assistant.core.errors import (
    DomainError,
    InvalidStateError,
    NotFoundError,
    PermissionDeniedError,
)
from line_assistant.db.base import utc_now
from line_assistant.db.models import (
    AssistantScope,
    AuditLog,
    ConversationSession,
    Direction,
    LedgerEntry,
    PaymentKind,
    ScopeType,
)
from line_assistant.ledger.catalog import CategoryService, PaymentMethodService, payment_kind_label
from line_assistant.ledger.context import ScopeContext
from line_assistant.ledger.conversation import ConversationService


@dataclass(frozen=True, slots=True)
class MonthlySummary:
    income: int
    expense: int
    balance: int
    by_category: dict[str, int]


class AccountingService:
    def __init__(
        self,
        session: AsyncSession,
        conversations: ConversationService,
        *,
        draft_ttl_minutes: int = 30,
        max_amount: int = 999_999_999,
    ) -> None:
        self.session = session
        self.conversations = conversations
        self.draft_ttl_minutes = draft_ttl_minutes
        self.max_amount = max_amount
        self.categories = CategoryService(session)
        self.payments = PaymentMethodService(session)

    async def start_entry(
        self, context: ScopeContext, direction: Direction
    ) -> ConversationSession:
        today = datetime.now(ZoneInfo(context.scope.timezone)).date()
        return await self.conversations.set(
            context.scope.id,
            context.user.id,
            flow="entry",
            state="selecting_category",
            payload={"direction": direction.value, "transaction_date": today.isoformat()},
            ttl_minutes=self.draft_ttl_minutes,
        )

    async def require_entry_session(self, context: ScopeContext) -> ConversationSession:
        conversation = await self.conversations.get(context.scope.id, context.user.id)
        if conversation is None or conversation.flow != "entry":
            raise InvalidStateError("這個記帳流程已過期，請重新開始")
        return conversation

    async def select_category(
        self,
        context: ScopeContext,
        category_id: uuid.UUID,
    ) -> ConversationSession:
        conversation = await self.require_entry_session(context)
        if conversation.state != "selecting_category":
            raise InvalidStateError("目前不是選擇記帳分類的步驟")
        category = await self.categories.get(context.ledger.id, category_id)
        if not category.is_active:
            raise InvalidStateError("此記帳分類已停用")
        if category.direction is not Direction(conversation.payload["direction"]):
            raise InvalidStateError("記帳分類與收入／支出方向不一致")
        path, system_path = await self.categories.path(category)
        payload = dict(conversation.payload)
        payload.update(
            {
                "category_id": str(category.id),
                "category_path": path,
                "category_system_path": system_path,
                "category_changed": True,
            }
        )
        next_state = (
            "review"
            if "editing_entry_id" in payload or payload.get("return_to_review")
            else "selecting_payment_kind"
        )
        return await self.conversations.update(
            conversation,
            state=next_state,
            payload=payload,
            ttl_minutes=self.draft_ttl_minutes,
        )

    async def select_payment_kind(
        self, context: ScopeContext, kind: PaymentKind
    ) -> ConversationSession:
        conversation = await self.require_entry_session(context)
        if conversation.state != "selecting_payment_kind":
            raise InvalidStateError("目前不是選擇付款方式的步驟")
        if conversation.payload.get("payment_locked"):
            raise PermissionDeniedError("只有原付款人可以修改付款方式與付款工具")
        if "category_id" not in conversation.payload:
            raise InvalidStateError("請先選擇記帳分類")
        payload = dict(conversation.payload)
        payload.update(
            {
                "payment_kind": kind.value,
                "payment_method_id": None,
                "payment_name": payment_kind_label(kind),
            }
        )
        return await self.conversations.update(
            conversation,
            state="selecting_payment_method",
            payload=payload,
            ttl_minutes=self.draft_ttl_minutes,
        )

    async def select_payment_method(
        self,
        context: ScopeContext,
        method_id: uuid.UUID | None,
    ) -> ConversationSession:
        conversation = await self.require_entry_session(context)
        if conversation.state != "selecting_payment_method":
            raise InvalidStateError("目前不是選擇付款工具的步驟")
        if conversation.payload.get("payment_locked"):
            raise PermissionDeniedError("只有原付款人可以修改付款方式與付款工具")
        kind = PaymentKind(conversation.payload["payment_kind"])
        payload = dict(conversation.payload)
        if method_id is None:
            payload["payment_method_id"] = None
            payload["payment_name"] = payment_kind_label(kind)
        else:
            method = await self.payments.get_for_user(context.user.id, method_id)
            if not method.is_active or method.kind is not kind:
                raise InvalidStateError("付款工具無效或已停用")
            payload["payment_method_id"] = str(method.id)
            payload["payment_name"] = method.name
        payload["payment_changed"] = True
        next_state = (
            "review"
            if "editing_entry_id" in payload or payload.get("return_to_review")
            else "awaiting_amount"
        )
        return await self.conversations.update(
            conversation,
            state=next_state,
            payload=payload,
            ttl_minutes=self.draft_ttl_minutes,
        )

    async def set_amount(self, context: ScopeContext, amount: int) -> ConversationSession:
        conversation = await self.require_entry_session(context)
        if conversation.state != "awaiting_amount":
            raise InvalidStateError("目前不能輸入金額")
        if amount <= 0 or amount > self.max_amount:
            raise DomainError(f"金額必須介於 1 與 {self.max_amount:,} 元之間")
        payload = dict(conversation.payload)
        payload["amount"] = amount
        return await self.conversations.update(
            conversation,
            state="review",
            payload=payload,
            ttl_minutes=self.draft_ttl_minutes,
        )

    async def begin_note(self, context: ScopeContext) -> ConversationSession:
        conversation = await self.require_entry_session(context)
        if conversation.state != "review":
            raise InvalidStateError("請先完成交易內容再新增備註")
        return await self.conversations.update(
            conversation,
            state="awaiting_note",
            ttl_minutes=self.draft_ttl_minutes,
        )

    async def set_note(self, context: ScopeContext, note: str | None) -> ConversationSession:
        conversation = await self.require_entry_session(context)
        if conversation.state != "awaiting_note":
            raise InvalidStateError("目前不是輸入備註的步驟")
        if note is not None and len(note.strip()) > 500:
            raise DomainError("備註最多 500 個字元")
        payload = dict(conversation.payload)
        payload["note"] = note.strip() if note and note.strip() else None
        return await self.conversations.update(
            conversation,
            state="review",
            payload=payload,
            ttl_minutes=self.draft_ttl_minutes,
        )

    async def set_date(self, context: ScopeContext, transaction_date: date) -> ConversationSession:
        conversation = await self.require_entry_session(context)
        if conversation.state != "review":
            raise InvalidStateError("請先完成交易內容再修改日期")
        today = datetime.now(ZoneInfo(context.scope.timezone)).date()
        if transaction_date > today:
            raise InvalidStateError("交易日期不能是未來日期")
        payload = dict(conversation.payload)
        payload["transaction_date"] = transaction_date.isoformat()
        return await self.conversations.update(
            conversation,
            state="review",
            payload=payload,
            ttl_minutes=self.draft_ttl_minutes,
        )

    async def begin_modify(self, context: ScopeContext, target: str) -> ConversationSession:
        conversation = await self.require_entry_session(context)
        if conversation.state != "review":
            raise InvalidStateError("目前不能修改交易欄位")
        allowed = {"category", "payment", "amount", "date", "note"}
        if target not in allowed:
            raise InvalidStateError("不支援的修改欄位")
        if target == "payment" and conversation.payload.get("payment_locked"):
            raise PermissionDeniedError("只有原付款人可以修改付款方式與付款工具")
        state = {
            "category": "selecting_category",
            "payment": "selecting_payment_kind",
            "amount": "awaiting_amount",
            "date": "review",
            "note": "awaiting_note",
        }[target]
        payload = dict(conversation.payload)
        if target in {"category", "payment"}:
            payload["return_to_review"] = True
        return await self.conversations.update(
            conversation,
            state=state,
            payload=payload,
            ttl_minutes=self.draft_ttl_minutes,
        )

    async def begin_edit(
        self, context: ScopeContext, entry_id: uuid.UUID
    ) -> ConversationSession:
        entry = await self._get_entry(context, entry_id)
        self._assert_can_edit(context.scope, context.user.id, entry)
        payload: dict[str, Any] = {
            "editing_entry_id": str(entry.id),
            "editing_entry_version": entry.version,
            "direction": entry.direction.value,
            "amount": entry.amount,
            "category_id": str(entry.category_id),
            "category_path": entry.category_path_snapshot,
            "category_system_path": entry.category_system_path,
            "category_changed": False,
            "payment_kind": entry.payment_kind.value,
            "payment_method_id": str(entry.payment_method_id) if entry.payment_method_id else None,
            "payment_name": entry.payment_name_snapshot,
            "payment_locked": entry.payer_user_id != context.user.id,
            "payment_changed": False,
            "transaction_date": entry.transaction_date.isoformat(),
            "note": entry.note,
        }
        return await self.conversations.set(
            context.scope.id,
            context.user.id,
            flow="entry",
            state="review",
            payload=payload,
            ttl_minutes=self.draft_ttl_minutes,
        )

    async def confirm(
        self,
        context: ScopeContext,
        *,
        webhook_event_id: str | None = None,
    ) -> LedgerEntry:
        conversation = await self.require_entry_session(context)
        payload = conversation.payload
        required = {
            "direction",
            "amount",
            "category_id",
            "category_path",
            "category_system_path",
            "payment_kind",
            "payment_name",
            "transaction_date",
        }
        if conversation.state != "review" or not required.issubset(payload):
            raise InvalidStateError("交易資料尚未完整")

        if not payload.get("editing_entry_id") or payload.get("category_changed"):
            category = await self.categories.get(
                context.ledger.id, uuid.UUID(payload["category_id"])
            )
            if not category.is_active:
                raise InvalidStateError("選擇的記帳分類已停用，請重新選擇")

        if payload.get("payment_method_id") and (
            not payload.get("editing_entry_id") or payload.get("payment_changed")
        ):
            method = await self.payments.get_for_user(
                context.user.id, uuid.UUID(payload["payment_method_id"])
            )
            if not method.is_active or method.kind is not PaymentKind(payload["payment_kind"]):
                raise InvalidStateError("選擇的付款工具已停用，請重新選擇")

        editing_id = payload.get("editing_entry_id")
        before_data: dict[str, Any] | None = None
        if editing_id:
            entry = await self._get_entry(context, uuid.UUID(editing_id), for_update=True)
            self._assert_can_edit(context.scope, context.user.id, entry)
            if entry.version != int(payload["editing_entry_version"]):
                raise InvalidStateError("此交易已被其他人修改，請重新開啟")
            if payload.get("payment_changed") and entry.payer_user_id != context.user.id:
                raise PermissionDeniedError("只有原付款人可以修改付款方式與付款工具")
            before_data = self.entry_snapshot(entry)
        else:
            entry = LedgerEntry(
                ledger_id=context.ledger.id,
                creator_user_id=context.user.id,
                payer_user_id=context.user.id,
                direction=Direction(payload["direction"]),
                amount=int(payload["amount"]),
                category_id=uuid.UUID(payload["category_id"]),
                category_path_snapshot=list(payload["category_path"]),
                category_system_path=list(payload["category_system_path"]),
                payment_kind=PaymentKind(payload["payment_kind"]),
                payment_method_id=(
                    uuid.UUID(payload["payment_method_id"])
                    if payload.get("payment_method_id")
                    else None
                ),
                payment_name_snapshot=str(payload["payment_name"]),
                transaction_date=date.fromisoformat(payload["transaction_date"]),
                note=payload.get("note"),
            )
            self.session.add(entry)
            await self.session.flush()

        if editing_id:
            entry.direction = Direction(payload["direction"])
            entry.amount = int(payload["amount"])
            entry.category_id = uuid.UUID(payload["category_id"])
            entry.category_path_snapshot = list(payload["category_path"])
            entry.category_system_path = list(payload["category_system_path"])
            if not payload.get("payment_locked"):
                entry.payment_kind = PaymentKind(payload["payment_kind"])
                entry.payment_method_id = (
                    uuid.UUID(payload["payment_method_id"])
                    if payload.get("payment_method_id")
                    else None
                )
                entry.payment_name_snapshot = str(payload["payment_name"])
            entry.transaction_date = date.fromisoformat(payload["transaction_date"])
            entry.note = payload.get("note")
            entry.version += 1
            await self.session.flush()

        self.session.add(
            AuditLog(
                scope_id=context.scope.id,
                entity_type="ledger_entry",
                entity_id=entry.id,
                action="update" if editing_id else "create",
                actor_user_id=context.user.id,
                before_data=before_data,
                after_data=self.entry_snapshot(entry),
                webhook_event_id=webhook_event_id,
            )
        )
        await self.conversations.clear(context.scope.id, context.user.id)
        await self.session.flush()
        return entry

    async def delete_entry(
        self,
        context: ScopeContext,
        entry_id: uuid.UUID,
        *,
        expected_version: int | None = None,
        webhook_event_id: str | None = None,
    ) -> LedgerEntry:
        entry = await self._get_entry(context, entry_id, for_update=True)
        self._assert_can_edit(context.scope, context.user.id, entry)
        if expected_version is not None and entry.version != expected_version:
            raise InvalidStateError("此交易已被其他人修改，請重新開啟")
        before = self.entry_snapshot(entry)
        entry.deleted_at = utc_now()
        entry.version += 1
        self.session.add(
            AuditLog(
                scope_id=context.scope.id,
                entity_type="ledger_entry",
                entity_id=entry.id,
                action="delete",
                actor_user_id=context.user.id,
                before_data=before,
                after_data=self.entry_snapshot(entry),
                webhook_event_id=webhook_event_id,
            )
        )
        await self.session.flush()
        return entry

    async def recent_entries(
        self,
        context: ScopeContext,
        *,
        page: int = 0,
        page_size: int = 10,
    ) -> tuple[list[LedgerEntry], bool]:
        query = (
            select(LedgerEntry)
            .where(
                LedgerEntry.ledger_id == context.ledger.id,
                LedgerEntry.deleted_at.is_(None),
            )
            .order_by(LedgerEntry.transaction_date.desc(), LedgerEntry.created_at.desc())
            .offset(max(page, 0) * page_size)
            .limit(page_size + 1)
        )
        rows = list((await self.session.scalars(query)).all())
        return rows[:page_size], len(rows) > page_size

    async def monthly_summary(
        self, context: ScopeContext, *, year: int, month: int
    ) -> MonthlySummary:
        first_day = date(year, month, 1)
        last_day = date(year, month, calendar.monthrange(year, month)[1])
        entries = list(
            (
                await self.session.scalars(
                    select(LedgerEntry).where(
                        LedgerEntry.ledger_id == context.ledger.id,
                        LedgerEntry.deleted_at.is_(None),
                        LedgerEntry.transaction_date >= first_day,
                        LedgerEntry.transaction_date <= last_day,
                    )
                )
            ).all()
        )
        income = sum(item.amount for item in entries if item.direction is Direction.INCOME)
        expense = sum(item.amount for item in entries if item.direction is Direction.EXPENSE)
        by_category: dict[str, int] = {}
        for item in entries:
            label = (
                item.category_path_snapshot[1]
                if len(item.category_path_snapshot) > 1
                else item.category_path_snapshot[0]
            )
            signed = item.amount if item.direction is Direction.INCOME else -item.amount
            by_category[label] = by_category.get(label, 0) + signed
        return MonthlySummary(
            income=income,
            expense=expense,
            balance=income - expense,
            by_category=by_category,
        )

    async def _get_entry(
        self,
        context: ScopeContext,
        entry_id: uuid.UUID,
        *,
        for_update: bool = False,
    ) -> LedgerEntry:
        query = select(LedgerEntry).where(
            LedgerEntry.id == entry_id,
            LedgerEntry.ledger_id == context.ledger.id,
            LedgerEntry.deleted_at.is_(None),
        )
        if for_update:
            query = query.with_for_update()
        entry = await self.session.scalar(query)
        if entry is None:
            raise NotFoundError("找不到指定交易")
        return entry

    @staticmethod
    def _assert_can_edit(
        scope: AssistantScope, actor_user_id: uuid.UUID, entry: LedgerEntry
    ) -> None:
        if scope.scope_type is ScopeType.PERSONAL and scope.owner_user_id != actor_user_id:
            raise PermissionDeniedError("只能修改自己的私人帳本")
        if scope.scope_type is ScopeType.PERSONAL and entry.creator_user_id != actor_user_id:
            raise PermissionDeniedError("只能修改自己的私人交易")

    @staticmethod
    def entry_snapshot(entry: LedgerEntry) -> dict[str, Any]:
        return {
            "id": str(entry.id),
            "direction": entry.direction.value,
            "amount": entry.amount,
            "category_path": list(entry.category_path_snapshot),
            "payment_kind": entry.payment_kind.value,
            "payment_name": entry.payment_name_snapshot,
            "transaction_date": entry.transaction_date.isoformat(),
            "note": entry.note,
            "version": entry.version,
            "deleted": entry.deleted_at is not None,
        }

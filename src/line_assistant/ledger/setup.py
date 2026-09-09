from __future__ import annotations

from typing import Any

from sqlalchemy import select

from line_assistant.core.errors import DomainError, InvalidStateError
from line_assistant.db.models import (
    AssistantScope,
    ConversationSession,
    PaymentKind,
    ScopeType,
)
from line_assistant.ledger.catalog import CategoryService, PaymentMethodService, load_ledger_seed
from line_assistant.ledger.context import ScopeContext
from line_assistant.ledger.conversation import ConversationService

_ROOT_BULLET = "•"
_CATEGORY_BULLET = "◦"
_ITEM_BULLET = "▪"


def _setup_steps(*, include_payments: bool) -> list[tuple[str, str, str]]:
    steps: list[tuple[str, str, str]] = []
    for root in load_ledger_seed()["categories"]:
        for child in root["children"]:
            steps.append((child["key"], child["name"], "category"))
    if include_payments:
        steps.extend(
            [
                (PaymentKind.CASH.value, "現金", "payment"),
                (PaymentKind.CREDIT_CARD.value, "信用卡", "payment"),
            ]
        )
    return steps


def parse_setup_template(text: str, *, include_payments: bool) -> dict[str, list[str]]:
    """解析完整設定範本，並保護固定的記帳分類與付款方式層級。"""

    roots: dict[str, list[str]] = {"收入": [], "支出": [], "現金": [], "信用卡": []}
    category_keys = {
        name: key
        for key, name, kind in _setup_steps(include_payments=False)
        if kind == "category"
    }
    fixed_steps = _setup_steps(include_payments=False)
    expected_income = [name for key, name, _ in fixed_steps if key.startswith("income.")]
    expected_expense = [name for key, name, _ in fixed_steps if key.startswith("expense.")]
    current_root: str | None = None
    current_category: str | None = None
    seen_roots: set[str] = set()
    answers: dict[str, list[str]] = {}
    seen_items: dict[str, set[str]] = {}

    def add_answer(key: str, name: str) -> None:
        normalized = name.casefold()
        if normalized not in seen_items.setdefault(key, set()):
            seen_items[key].add(normalized)
            answers.setdefault(key, []).append(name)

    for raw_line in text.splitlines():
        line = raw_line.strip().strip('"')
        if not line:
            continue
        if line.startswith(_ROOT_BULLET):
            name = line.removeprefix(_ROOT_BULLET).strip()
            if name not in roots:
                raise DomainError("格式有問題，科米蛙不理解，請重新上傳。")
            seen_roots.add(name)
            current_root = name
            current_category = None
            continue
        if line.startswith(_CATEGORY_BULLET):
            name = line.removeprefix(_CATEGORY_BULLET).strip()
            if current_root in {"現金", "信用卡"} and include_payments:
                key = (
                    PaymentKind.CASH.value
                    if current_root == "現金"
                    else PaymentKind.CREDIT_CARD.value
                )
                add_answer(key, name)
                continue
            if current_root not in {"收入", "支出"} or name not in category_keys:
                raise DomainError("格式有問題，科米蛙不理解，請重新上傳。")
            if name not in roots[current_root]:
                roots[current_root].append(name)
            current_category = name
            continue
        if line.startswith(_ITEM_BULLET):
            name = line.lstrip(_ITEM_BULLET).replace("️", "").strip()
            if not name or current_root is None:
                raise DomainError("格式有問題，科米蛙不理解，請重新上傳。")
            if current_root in {"收入", "支出"}:
                if current_category is None:
                    raise DomainError("格式有問題，科米蛙不理解，請重新上傳。")
                key = category_keys[current_category]
            elif include_payments:
                key = (
                    PaymentKind.CASH.value
                    if current_root == "現金"
                    else PaymentKind.CREDIT_CARD.value
                )
            else:
                raise DomainError("群組帳本不能設定付款工具，請重新上傳群組範本。")
            add_answer(key, name)
            continue
        raise DomainError("格式有問題，科米蛙不理解，請重新上傳。")

    if roots["收入"] != expected_income or roots["支出"] != expected_expense:
        raise DomainError("格式有問題，科米蛙不理解，請重新上傳。")
    if include_payments:
        if seen_roots != {"收入", "支出", "現金", "信用卡"}:
            raise DomainError("格式有問題，科米蛙不理解，請重新上傳。")
    elif seen_roots != {"收入", "支出"}:
        raise DomainError("群組帳本不能設定付款工具，請重新上傳群組範本。")

    return answers


class SetupService:
    def __init__(self, context: ScopeContext, conversation_service: ConversationService) -> None:
        self.context = context
        self.conversations = conversation_service

    async def start(self, *, restart: bool = False) -> ConversationSession:
        if not restart:
            existing = await self.conversations.get(
                self.context.scope.id, self.context.user.id
            )
            if existing is not None and existing.flow == "setup":
                return existing
        include_payments = self.context.scope.scope_type is ScopeType.PERSONAL
        return await self.conversations.set(
            self.context.scope.id,
            self.context.user.id,
            flow="setup",
            state="awaiting_setup_template",
            payload={"include_payments": include_payments, "answers": {}},
            ttl_minutes=7 * 24 * 60,
        )

    async def answer_template(
        self, conversation: ConversationSession, text: str
    ) -> ConversationSession:
        if conversation.flow != "setup" or conversation.state != "awaiting_setup_template":
            raise InvalidStateError("目前不在完整設定上傳流程")
        answers = parse_setup_template(
            text,
            include_payments=bool(conversation.payload["include_payments"]),
        )
        payload: dict[str, Any] = dict(conversation.payload)
        payload["answers"] = answers
        return await self.conversations.update(
            conversation,
            state="setup_review",
            payload=payload,
            ttl_minutes=7 * 24 * 60,
        )

    @staticmethod
    def review_items(conversation: ConversationSession) -> list[tuple[str, str]]:
        answers: dict[str, list[str]] = conversation.payload.get("answers", {})
        return [
            (
                f"「{label}」{'記帳子分類' if kind == 'category' else '付款工具'}：",
                ", ".join(answers.get(key, [])) or "未設定",
            )
            for key, label, kind in _setup_steps(
                include_payments=bool(conversation.payload["include_payments"])
            )
        ]

    @staticmethod
    def review_lines(conversation: ConversationSession) -> list[str]:
        return [f"{heading}{items}" for heading, items in SetupService.review_items(conversation)]

    async def confirm(self, conversation: ConversationSession) -> None:
        if conversation.flow != "setup" or conversation.state != "setup_review":
            raise InvalidStateError("設定內容尚未準備完成")
        await self.conversations.session.execute(
            select(AssistantScope)
            .where(AssistantScope.id == self.context.scope.id)
            .with_for_update()
        )
        answers: dict[str, list[str]] = conversation.payload.get("answers", {})
        category_service = CategoryService(self.conversations.session)
        payment_service = PaymentMethodService(self.conversations.session)
        for key, _, kind in _setup_steps(
            include_payments=bool(conversation.payload["include_payments"])
        ):
            names = answers.get(key, [])
            if kind == "category":
                parent = await category_service.get_by_system_key(self.context.ledger.id, key)
                await category_service.add_children(parent, names)
            else:
                await payment_service.add_many(
                    self.context.user.id,
                    PaymentKind(key),
                    names,
                )
        self.context.scope.setup_completed = True
        self.context.scope.category_version += 1
        await self.conversations.clear(self.context.scope.id, self.context.user.id)

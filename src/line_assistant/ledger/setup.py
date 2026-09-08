from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import select

from line_assistant.core.errors import InvalidStateError
from line_assistant.db.models import AssistantScope, ConversationSession, PaymentKind, ScopeType
from line_assistant.ledger.catalog import CategoryService, PaymentMethodService, load_ledger_seed
from line_assistant.ledger.context import ScopeContext
from line_assistant.ledger.conversation import ConversationService


@dataclass(frozen=True, slots=True)
class SetupStep:
    key: str
    label: str
    kind: str


def setup_steps(*, include_payments: bool) -> list[SetupStep]:
    steps: list[SetupStep] = []
    for root in load_ledger_seed()["categories"]:
        for child in root["children"]:
            steps.append(SetupStep(key=child["key"], label=child["name"], kind="category"))
    if include_payments:
        steps.extend(
            [
                SetupStep(key=PaymentKind.CASH.value, label="現金", kind="payment"),
                SetupStep(key=PaymentKind.CREDIT_CARD.value, label="信用卡", kind="payment"),
            ]
        )
    return steps


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
        steps = setup_steps(include_payments=include_payments)
        return await self.conversations.set(
            self.context.scope.id,
            self.context.user.id,
            flow="setup",
            state="awaiting_setup_items",
            payload={
                "step_index": 0,
                "steps": [asdict(step) for step in steps],
                "answers": {},
            },
            ttl_minutes=7 * 24 * 60,
        )

    @staticmethod
    def current_step(conversation: ConversationSession) -> SetupStep:
        payload = conversation.payload
        index = int(payload["step_index"])
        steps = payload["steps"]
        if index >= len(steps):
            raise InvalidStateError("設定問答已完成")
        return SetupStep(**steps[index])

    async def answer(
        self, conversation: ConversationSession, names: list[str]
    ) -> tuple[ConversationSession, bool]:
        if conversation.flow != "setup" or conversation.state != "awaiting_setup_items":
            raise InvalidStateError("目前不在設定問答流程")
        payload: dict[str, Any] = dict(conversation.payload)
        step = self.current_step(conversation)
        answers = dict(payload.get("answers", {}))
        answers[step.key] = list(names)
        payload["answers"] = answers
        payload["step_index"] = int(payload["step_index"]) + 1
        finished = int(payload["step_index"]) >= len(payload["steps"])
        state = "setup_review" if finished else "awaiting_setup_items"
        updated = await self.conversations.update(
            conversation,
            state=state,
            payload=payload,
            ttl_minutes=7 * 24 * 60,
        )
        return updated, finished

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
        for step_data in conversation.payload["steps"]:
            step = SetupStep(**step_data)
            names = answers.get(step.key, [])
            if step.kind == "category":
                parent = await category_service.get_by_system_key(self.context.ledger.id, step.key)
                await category_service.add_children(parent, names)
            else:
                await payment_service.add_many(
                    self.context.user.id,
                    PaymentKind(step.key),
                    names,
                )
        self.context.scope.setup_completed = True
        self.context.scope.category_version += 1
        await self.conversations.clear(self.context.scope.id, self.context.user.id)

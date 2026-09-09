from urllib.parse import urlencode

import pytest
from linebot.v3.messaging import FlexMessage
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from line_assistant.core.errors import InvalidStateError
from line_assistant.db.models import AuditLog, Direction, PaymentKind, ScopeType
from line_assistant.ledger.catalog import CategoryService, PaymentMethodService
from line_assistant.ledger.context import IdentityService
from line_assistant.line.dispatcher import EventDispatcher


async def test_completed_user_can_manage_category_and_payment_method(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        context = await IdentityService(session).ensure_context(
            ScopeType.PERSONAL, "U-settings", "U-settings", is_friend=True
        )
        context.scope.setup_completed = True
        categories = CategoryService(session)
        food = await categories.get_by_system_key(context.ledger.id, "expense.food")
        breakfast = (await categories.add_children(food, ["早餐"]))[0]
        snack = (await categories.add_children(food, ["點心"]))[0]
        method = (
            await PaymentMethodService(session).add_many(
                context.user.id, PaymentKind.CREDIT_CARD, ["舊卡片"]
            )
        )[0]
        dispatcher = EventDispatcher(
            session,
            timezone="Asia/Taipei",
            draft_ttl_minutes=30,
            max_amount=999_999_999,
        )

        menu = await dispatcher._handle_postback(context, "act=setup.start", {})
        FlexMessage.from_dict(menu[0])

        ledger_menu = await dispatcher._handle_postback(
            context, "act=service.open&service=ledger", {}
        )
        assert ledger_menu[0]["altText"] == "私人帳本功能選單"
        placeholder = await dispatcher._handle_postback(
            context, "act=service.open&service=placeholder-2", {}
        )
        assert placeholder[0]["text"] == "此服務中心正在準備中，敬請期待。"

        await dispatcher._handle_postback(
            context,
            urlencode(
                {"act": "settings.category.rename", "category_id": str(breakfast.id)}
            ),
            {},
        )
        await dispatcher._handle_text(context, "早午餐")
        assert breakfast.name == "早午餐"

        await dispatcher._handle_postback(
            context,
            urlencode(
                {
                    "act": "settings.category.add_child",
                    "category_id": str(breakfast.id),
                }
            ),
            {},
        )
        await dispatcher._handle_text(context, "咖啡店、早餐店")
        grandchildren = await categories.list_children(context.ledger.id, breakfast.id)
        assert [item.name for item in grandchildren] == ["咖啡店", "早餐店"]

        await dispatcher._handle_postback(
            context,
            urlencode({"act": "settings.payment.rename", "method_id": str(method.id)}),
            {},
        )
        await dispatcher._handle_text(context, "新卡片")
        assert method.name == "新卡片"

        await dispatcher._handle_postback(
            context,
            urlencode(
                {
                    "act": "settings.payment.disable.confirm",
                    "method_id": str(method.id),
                }
            ),
            {},
        )
        assert method.is_active is False

        await dispatcher._handle_postback(
            context,
            urlencode(
                {
                    "act": "settings.category.disable.confirm",
                    "category_id": str(snack.id),
                }
            ),
            {},
        )
        assert snack.is_active is False
        assert await session.scalar(select(func.count(AuditLog.id))) == 6


async def test_stale_entry_postback_is_rejected(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        context = await IdentityService(session).ensure_context(
            ScopeType.PERSONAL, "U-stale", "U-stale", is_friend=True
        )
        context.scope.setup_completed = True
        dispatcher = EventDispatcher(
            session,
            timezone="Asia/Taipei",
            draft_ttl_minutes=30,
            max_amount=999_999_999,
        )
        conversation = await dispatcher.accounting.start_entry(context, Direction.EXPENSE)
        food = await CategoryService(session).get_by_system_key(
            context.ledger.id, "expense.food"
        )
        stale_data = urlencode(
            {
                "act": "entry.category",
                "category_id": str(food.id),
                "flow_id": str(conversation.id),
                "revision": conversation.version + 1,
            }
        )
        with pytest.raises(InvalidStateError, match="已失效"):
            await dispatcher._handle_postback(context, stale_data, {})

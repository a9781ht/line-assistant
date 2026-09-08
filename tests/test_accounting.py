from datetime import date

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from line_assistant.core.errors import InvalidStateError, PermissionDeniedError
from line_assistant.db.models import Direction, PaymentKind, ScopeType
from line_assistant.ledger.accounting import AccountingService
from line_assistant.ledger.catalog import CategoryService, PaymentMethodService
from line_assistant.ledger.context import IdentityService
from line_assistant.ledger.conversation import ConversationService


async def test_create_entry_preserves_snapshots_and_summary(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        context = await IdentityService(session).ensure_context(
            ScopeType.PERSONAL,
            "U-a",
            "U-a",
            is_friend=True,
        )
        context.scope.setup_completed = True
        categories = CategoryService(session)
        food = await categories.get_by_system_key(context.ledger.id, "expense.food")
        breakfast = (await categories.add_children(food, ["早餐"]))[0]
        card = (
            await PaymentMethodService(session).add_many(
                context.user.id, PaymentKind.CREDIT_CARD, ["國泰Cube卡"]
            )
        )[0]
        accounting = AccountingService(session, ConversationService(session))

        await accounting.start_entry(context, Direction.EXPENSE)
        await accounting.select_category(context, breakfast.id)
        await accounting.select_payment_kind(context, PaymentKind.CREDIT_CARD)
        await accounting.select_payment_method(context, card.id)
        await accounting.set_amount(context, 120)
        await accounting.begin_note(context)
        await accounting.set_note(context, "早餐")
        entry = await accounting.confirm(context, webhook_event_id="evt-create")
        await session.commit()

        await categories.rename(breakfast, "早餐店")
        PaymentMethodService(session).disable(card)
        await session.commit()

        assert entry.category_path_snapshot == ["支出", "食", "早餐"]
        assert entry.payment_name_snapshot == "國泰Cube卡"
        summary = await accounting.monthly_summary(
            context,
            year=entry.transaction_date.year,
            month=entry.transaction_date.month,
        )
        assert summary.expense == 120
        assert summary.balance == -120
        assert summary.by_category == {"食": -120}


async def test_group_member_can_edit_except_payment_method(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        identities = IdentityService(session)
        owner_private = await identities.ensure_context(
            ScopeType.PERSONAL, "U-owner", "U-owner", is_friend=True
        )
        owner_private.scope.setup_completed = True
        group_owner = await identities.ensure_context(
            ScopeType.GROUP, "C-family", "U-owner"
        )
        categories = CategoryService(session)
        food = await categories.get_by_system_key(group_owner.ledger.id, "expense.food")
        breakfast = (await categories.add_children(food, ["早餐"]))[0]
        card = (
            await PaymentMethodService(session).add_many(
                group_owner.user.id, PaymentKind.CREDIT_CARD, ["國泰Cube卡"]
            )
        )[0]
        accounting = AccountingService(session, ConversationService(session))
        await accounting.start_entry(group_owner, Direction.EXPENSE)
        await accounting.select_category(group_owner, breakfast.id)
        await accounting.select_payment_kind(group_owner, PaymentKind.CREDIT_CARD)
        await accounting.select_payment_method(group_owner, card.id)
        await accounting.set_amount(group_owner, 100)
        entry = await accounting.confirm(group_owner)

        member_private = await identities.ensure_context(
            ScopeType.PERSONAL, "U-member", "U-member", is_friend=True
        )
        member_private.scope.setup_completed = True
        group_member = await identities.ensure_context(
            ScopeType.GROUP, "C-family", "U-member"
        )
        edit = await accounting.begin_edit(group_member, entry.id)
        assert edit.payload["payment_locked"] is True
        with pytest.raises(PermissionDeniedError):
            await accounting.begin_modify(group_member, "payment")

        await accounting.begin_modify(group_member, "amount")
        await accounting.set_amount(group_member, 250)
        updated = await accounting.confirm(group_member)
        await session.commit()
        assert updated.amount == 250
        assert updated.payment_name_snapshot == "國泰Cube卡"
        assert updated.version == 2

        await accounting.begin_edit(group_member, entry.id)
        await accounting.begin_modify(group_member, "category")
        category_edit = await accounting.select_category(group_member, food.id)
        assert category_edit.state == "review"
        category_updated = await accounting.confirm(group_member)
        assert category_updated.category_path_snapshot == ["支出", "食"]
        assert category_updated.payment_name_snapshot == "國泰Cube卡"
        assert category_updated.version == 3

        with pytest.raises(InvalidStateError, match="已被其他人修改"):
            await accounting.delete_entry(
                group_member,
                entry.id,
                expected_version=2,
            )


async def test_future_transaction_date_is_rejected(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        context = await IdentityService(session).ensure_context(
            ScopeType.PERSONAL, "U-date", "U-date", is_friend=True
        )
        accounting = AccountingService(session, ConversationService(session))
        await accounting.start_entry(context, Direction.INCOME)
        category = await CategoryService(session).get_by_system_key(
            context.ledger.id, "income.other"
        )
        await accounting.select_category(context, category.id)
        await accounting.select_payment_kind(context, PaymentKind.CASH)
        await accounting.select_payment_method(context, None)
        await accounting.set_amount(context, 1)
        with pytest.raises(Exception, match="未來日期"):
            await accounting.set_date(context, date(2999, 1, 1))


async def test_recent_entries_are_paginated(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        context = await IdentityService(session).ensure_context(
            ScopeType.PERSONAL, "U-pages", "U-pages", is_friend=True
        )
        context.scope.setup_completed = True
        categories = CategoryService(session)
        other = await categories.get_by_system_key(context.ledger.id, "expense.other")
        accounting = AccountingService(session, ConversationService(session))
        for amount in range(1, 13):
            await accounting.start_entry(context, Direction.EXPENSE)
            await accounting.select_category(context, other.id)
            await accounting.select_payment_kind(context, PaymentKind.CASH)
            await accounting.select_payment_method(context, None)
            await accounting.set_amount(context, amount)
            await accounting.confirm(context)

        first_page, has_next = await accounting.recent_entries(context, page=0)
        second_page, second_has_next = await accounting.recent_entries(context, page=1)
        assert len(first_page) == 10
        assert has_next is True
        assert len(second_page) == 2
        assert second_has_next is False


async def test_custom_category_with_children_must_be_disabled_bottom_up(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        context = await IdentityService(session).ensure_context(
            ScopeType.PERSONAL, "U-tree", "U-tree", is_friend=True
        )
        categories = CategoryService(session)
        food = await categories.get_by_system_key(context.ledger.id, "expense.food")
        breakfast = (await categories.add_children(food, ["早餐"]))[0]
        cafe = (await categories.add_children(breakfast, ["咖啡店"]))[0]
        with pytest.raises(Exception, match="記帳子分類"):
            await categories.disable(breakfast)
        await categories.disable(cafe)
        await categories.disable(breakfast)
        assert cafe.is_active is False
        assert breakfast.is_active is False

from __future__ import annotations

import asyncio
import os
import uuid

from line_assistant.db.models import Direction, PaymentKind, ScopeType
from line_assistant.db.session import create_engine, create_session_factory
from line_assistant.ledger.accounting import AccountingService
from line_assistant.ledger.catalog import CategoryService
from line_assistant.ledger.context import IdentityService
from line_assistant.ledger.conversation import ConversationService


async def smoke_test() -> None:
    engine = create_engine(os.environ["DATABASE_URL"])
    factory = create_session_factory(engine)
    suffix = uuid.uuid4().hex
    async with factory() as session:
        context = await IdentityService(session).ensure_context(
            ScopeType.PERSONAL,
            f"U-smoke-{suffix}",
            f"U-smoke-{suffix}",
            is_friend=True,
        )
        category = await CategoryService(session).get_by_system_key(
            context.ledger.id, "expense.other"
        )
        accounting = AccountingService(session, ConversationService(session))
        await accounting.start_entry(context, Direction.EXPENSE)
        await accounting.select_category(context, category.id)
        await accounting.select_payment_kind(context, PaymentKind.CASH)
        await accounting.select_payment_method(context, None)
        await accounting.set_amount(context, 321)
        entry = await accounting.confirm(context)
        entries, has_next = await accounting.recent_entries(context)
        assert entry.amount == 321
        assert entries[0].id == entry.id
        assert has_next is False
        await session.rollback()
    await engine.dispose()
    print("PostgreSQL accounting smoke test passed")


if __name__ == "__main__":
    asyncio.run(smoke_test())

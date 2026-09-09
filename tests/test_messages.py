import uuid
from datetime import UTC, date, datetime

from linebot.v3.messaging import FlexMessage, TextMessage

from line_assistant.db.models import (
    Category,
    ConversationSession,
    Direction,
    LedgerEntry,
    PaymentKind,
    PaymentMethod,
)
from line_assistant.ledger.accounting import MonthlySummary
from line_assistant.line.messages import (
    amount_prompt_message,
    category_options_message,
    datetime_button,
    delete_confirmation_message,
    entry_preview_message,
    entry_result_message,
    main_menu_message,
    modify_menu_message,
    payment_kind_message,
    payment_method_message,
    recent_entries_message,
    settings_category_list_message,
    settings_category_message,
    settings_menu_message,
    settings_payment_list_message,
    settings_payment_message,
    setup_introduction_message,
    setup_review_message,
    setup_template_message,
    summary_message,
    welcome_message,
)


def test_generated_messages_match_line_sdk_schema() -> None:
    menu = main_menu_message(is_group=False)
    amount = amount_prompt_message()
    introduction = setup_introduction_message(include_payments=True)
    FlexMessage.from_dict(menu)
    TextMessage.from_dict(amount)
    TextMessage.from_dict(introduction)


def test_setup_review_message_schema() -> None:
    conversation = ConversationSession(
        scope_id="11111111-1111-1111-1111-111111111111",
        actor_user_id="22222222-2222-2222-2222-222222222222",
        flow="setup",
        state="setup_review",
        payload={
            "answers": {"expense.food": ["早餐", "午餐"]},
            "include_payments": False,
        },
        expires_at=datetime.now(UTC),
    )
    FlexMessage.from_dict(setup_review_message(conversation))


def test_datetime_button_uses_non_future_maximum() -> None:
    button = datetime_button("日期", "entry.date", initial=date(2026, 1, 1))
    assert button["action"]["mode"] == "date"
    assert button["action"]["max"] == date.today().isoformat()


def test_all_accounting_flex_builders_match_line_schema() -> None:
    scope_id = uuid.uuid4()
    user_id = uuid.uuid4()
    ledger_id = uuid.uuid4()
    category = Category(
        id=uuid.uuid4(),
        ledger_id=ledger_id,
        parent_id=uuid.uuid4(),
        direction=Direction.EXPENSE,
        depth=3,
        name="非常長的早餐分類名稱" * 5,
        sort_order=0,
        is_system=False,
    )
    method = PaymentMethod(
        id=uuid.uuid4(),
        owner_user_id=user_id,
        kind=PaymentKind.CREDIT_CARD,
        name="非常長的信用卡付款方式名稱" * 5,
        sort_order=0,
    )
    conversation = ConversationSession(
        id=uuid.uuid4(),
        scope_id=scope_id,
        actor_user_id=user_id,
        flow="entry",
        state="review",
        payload={
            "direction": "expense",
            "category_path": ["支出", "食", "早餐"],
            "payment_kind": "credit_card",
            "payment_name": "國泰Cube卡",
            "amount": 120,
            "transaction_date": "2026-09-07",
            "note": "測試",
        },
        version=7,
        expires_at=datetime.now(UTC),
    )
    entry = LedgerEntry(
        id=uuid.uuid4(),
        ledger_id=ledger_id,
        creator_user_id=user_id,
        payer_user_id=user_id,
        direction=Direction.EXPENSE,
        amount=120,
        category_id=category.id,
        category_path_snapshot=["支出", "食", "早餐"],
        category_system_path=["expense", "expense.food"],
        payment_kind=PaymentKind.CREDIT_CARD,
        payment_method_id=method.id,
        payment_name_snapshot="國泰Cube卡",
        transaction_date=date(2026, 9, 7),
    )
    messages = [
        category_options_message(
            title="食",
            categories=[category],
            parent=None,
            conversation=conversation,
            page=0,
        ),
        payment_kind_message(conversation),
        payment_method_message(
            PaymentKind.CREDIT_CARD,
            [method],
            conversation=conversation,
        ),
        entry_preview_message(conversation),
        modify_menu_message(conversation),
        entry_result_message(entry, updated=False),
        recent_entries_message([entry], is_group=True, page=0, has_next=True),
        delete_confirmation_message(entry.id, entry.version),
        summary_message(
            MonthlySummary(income=1000, expense=120, balance=880, by_category={"食": -120}),
            year=2026,
            month=9,
            previous=(2026, 8),
            next_month=None,
        ),
        settings_menu_message(is_group=False),
        settings_category_list_message([(category, "支出 › 食 › 早餐")], page=0, has_next=False),
        settings_category_message(category, "支出 › 食 › 早餐"),
        settings_payment_list_message([method], page=0, has_next=False),
        settings_payment_message(method),
        welcome_message(),
    ]
    for message in messages:
        parsed = FlexMessage.from_dict(message)
        assert parsed is not None
    TextMessage.from_dict(setup_template_message(include_payments=True))

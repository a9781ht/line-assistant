import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from line_assistant.db.models import Category, PaymentKind, PaymentMethod, ScopeType
from line_assistant.ledger.context import IdentityService
from line_assistant.ledger.conversation import ConversationService
from line_assistant.ledger.setup import SetupService, parse_setup_template

PERSONAL_TEMPLATE: dict[str, list[str]] = {
    "income.general": [
        "薪資",
        "上半年績效",
        "下半年績效",
        "年終獎金",
        "Q1 季獎金",
        "Q2 季獎金",
        "Q3 季獎金",
        "Q4 季獎金",
        "勞動節",
        "端午節",
        "中秋節",
        "生日",
        "開工",
        "尾牙",
        "旅遊補助",
        "差旅補助",
        "中獎禮券",
    ],
    "income.investment": ["銀行利息", "股利所得", "股票買賣"],
    "expense.food": [
        "早餐",
        "早午餐",
        "午餐",
        "下午茶",
        "晚餐",
        "宵夜",
        "節日餐",
        "請客餐",
        "點心零嘴",
        "食材",
    ],
    "expense.clothing": ["服裝", "鞋子", "配件", "剪髮理容"],
    "expense.housing": [
        "房租",
        "電費",
        "水費",
        "網路費",
        "電話費",
        "綜所稅",
        "家電傢俱用品",
        "生活必需用品",
        "串流訂閱",
        "雜支",
    ],
    "expense.transportation": [
        "油錢",
        "捷運",
        "公車",
        "客運",
        "台鐵",
        "區間",
        "高鐵",
        "飛機",
        "停車費",
        "過路費",
        "租車",
        "計程車",
        "維修保養",
        "美容洗車",
        "牌照稅",
        "燃料費",
        "汽機車保險",
        "驗車費",
        "材料費",
        "罰單",
    ],
    "expense.education": ["書籍", "課程", "AI 訂閱", "考試"],
    "expense.entertainment": [
        "旅行住宿",
        "旅行遊玩",
        "旅行購物",
        "旅行吃飯",
        "旅行保險",
        "旅行雜支",
        "運動健身",
        "社交",
        "生活奢侈用品",
        "送禮物",
        "婚喪喜慶",
    ],
    "expense.medical": ["診所就醫", "購買藥物", "保費"],
    "expense.finance": ["儲蓄", "ETF 股票", "個股股票"],
    PaymentKind.CASH.value: ["實體", "轉帳", "約當"],
    PaymentKind.CREDIT_CARD.value: [
        "永豐DAWHO卡",
        "台新Richart卡",
        "國泰Cube卡",
        "富邦J卡",
        "玉山Unicard",
    ],
}


def render_personal_template() -> str:
    lines = ["• 收入"]
    for key, name in (
        ("income.general", "一般收入"),
        ("income.investment", "投資收入"),
        ("income.other", "其他"),
    ):
        lines.append(f"  ◦ {name}")
        lines.extend(f"    ▪ {item}" for item in PERSONAL_TEMPLATE.get(key, []))
    lines.append("• 支出")
    for key, name in (
        ("expense.food", "食"),
        ("expense.clothing", "衣"),
        ("expense.housing", "住"),
        ("expense.transportation", "行"),
        ("expense.education", "育"),
        ("expense.entertainment", "樂"),
        ("expense.medical", "醫療"),
        ("expense.finance", "理財"),
        ("expense.other", "其他"),
    ):
        lines.append(f"  ◦ {name}")
        lines.extend(f"    ▪ {item}" for item in PERSONAL_TEMPLATE.get(key, []))
    for key, name in ((PaymentKind.CASH.value, "現金"), (PaymentKind.CREDIT_CARD.value, "信用卡")):
        lines.append(f"• {name}")
        lines.extend(f"  ◦ {item}" for item in PERSONAL_TEMPLATE[key])
    return "\n".join(lines)


async def test_full_personal_setup_template(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        context = await IdentityService(session).ensure_context(
            ScopeType.PERSONAL,
            "U-owner",
            "U-owner",
            is_friend=True,
        )
        conversations = ConversationService(session)
        setup = SetupService(context, conversations)
        conversation = await setup.start()

        conversation = await setup.answer_template(conversation, render_personal_template())

        assert conversation.state == "setup_review"
        await setup.confirm(conversation)
        await session.commit()

        expected_custom_categories = sum(
            len(names)
            for key, names in PERSONAL_TEMPLATE.items()
            if key not in {PaymentKind.CASH.value, PaymentKind.CREDIT_CARD.value}
        )
        custom_categories = await session.scalar(
            select(func.count(Category.id)).where(Category.is_system.is_(False))
        )
        payment_count = await session.scalar(select(func.count(PaymentMethod.id)))
        assert custom_categories == expected_custom_categories
        assert payment_count == 8
        assert context.scope.setup_completed is True
        assert await conversations.get(context.scope.id, context.user.id) is None


async def test_setup_resumes_unless_restart_is_explicit(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        context = await IdentityService(session).ensure_context(
            ScopeType.PERSONAL,
            "U-resume",
            "U-resume",
            is_friend=True,
        )
        setup = SetupService(context, ConversationService(session))
        first = await setup.start()
        progressed = await setup.answer_template(first, render_personal_template())
        resumed = await setup.start()
        assert resumed.id == progressed.id
        assert resumed.state == "setup_review"

        restarted = await setup.start(restart=True)
        assert restarted.id == progressed.id
        assert restarted.state == "awaiting_setup_template"


def test_setup_template_rejects_changed_fixed_categories() -> None:
    invalid = render_personal_template().replace("  ◦ 食", "  ◦ 餐飲", 1)
    with pytest.raises(Exception, match="格式有問題，科米蛙不理解"):
        parse_setup_template(invalid, include_payments=True)

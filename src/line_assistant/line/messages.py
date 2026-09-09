from __future__ import annotations

import uuid
from datetime import date
from functools import cache
from importlib.resources import files
from typing import Any
from urllib.parse import urlencode

from line_assistant.db.models import (
    Category,
    ConversationSession,
    Direction,
    LedgerEntry,
    PaymentKind,
    PaymentMethod,
)
from line_assistant.ledger.accounting import MonthlySummary
from line_assistant.ledger.catalog import payment_kind_label
from line_assistant.ledger.setup import SetupService

BotMessage = dict[str, Any]
FlexAction = dict[str, Any]

_CATEGORY_CARD_STYLES: dict[str, tuple[str, str]] = {
    "食": ("🍜", "#F97316"),
    "衣": ("👕", "#EC4899"),
    "住": ("🏠", "#0EA5E9"),
    "行": ("🚗", "#6366F1"),
    "育": ("📚", "#8B5CF6"),
    "樂": ("🎮", "#EAB308"),
    "醫療": ("💊", "#0F766E"),
    "理財": ("💰", "#10B981"),
    "其他": ("✨", "#64748B"),
    "一般收入": ("💼", "#16A34A"),
    "投資收入": ("📈", "#059669"),
}


@cache
def load_setup_template(filename: str) -> str:
    return files("line_assistant").joinpath("templates", filename).read_text(encoding="utf-8")


@cache
def load_setup_sections(filename: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current_section: str | None = None
    for line in load_setup_template(filename).splitlines():
        if line.startswith("• "):
            current_section = line.removeprefix("• ")
            sections[current_section] = []
        elif current_section is not None and line.startswith("  ◦ "):
            sections[current_section].append(line.removeprefix("  ◦ "))
    return sections


def short_button_label(value: str, *, maximum: int = 40) -> str:
    return value if len(value) <= maximum else f"{value[: maximum - 1]}…"


def postback_data(action: str, **params: str | int | uuid.UUID) -> str:
    values = {"act": action, **{key: str(value) for key, value in params.items()}}
    return urlencode(values)


def postback_button(
    label: str,
    action: str,
    *,
    style: str = "secondary",
    display_text: str | None = None,
    **params: str | int | uuid.UUID,
) -> FlexAction:
    action_data: FlexAction = {
        "type": "postback",
        "label": label,
        "data": postback_data(action, **params),
    }
    if display_text:
        action_data["displayText"] = display_text
    return {
        "type": "button",
        "style": style,
        "height": "sm",
        "margin": "sm",
        "action": action_data,
    }


def datetime_button(
    label: str,
    action: str,
    *,
    initial: date,
    conversation: ConversationSession | None = None,
) -> FlexAction:
    params: dict[str, str | int | uuid.UUID] = {}
    if conversation is not None:
        params = {"flow_id": conversation.id, "revision": conversation.version}
    return {
        "type": "button",
        "style": "secondary",
        "height": "sm",
        "margin": "sm",
        "action": {
            "type": "datetimepicker",
            "label": label,
            "data": postback_data(action, **params),
            "mode": "date",
            "initial": initial.isoformat(),
            "max": date.today().isoformat(),
        },
    }


def text_message(text: str, *, quick_actions: list[dict[str, Any]] | None = None) -> BotMessage:
    result: BotMessage = {"type": "text", "text": text}
    if quick_actions:
        result["quickReply"] = {
            "items": [{"type": "action", "action": action} for action in quick_actions]
        }
    return result


def flex_card(
    *,
    alt_text: str,
    title: str,
    lines: list[str],
    actions: list[FlexAction] | None = None,
    color: str = "#2563EB",
    hero_image_url: str | None = None,
    rich_lines: list[list[dict[str, Any]]] | None = None,
) -> BotMessage:
    body_contents: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": title,
            "weight": "bold",
            "size": "xl",
            "color": color,
            "wrap": True,
        }
    ]
    if rich_lines is None:
        body_contents.extend(
            {
                "type": "text",
                "text": line,
                "size": "sm",
                "color": "#374151",
                "wrap": True,
                "margin": "md",
            }
            for line in lines
        )
    else:
        body_contents.extend(
            {
                "type": "text",
                "contents": line,
                "size": "sm",
                "color": "#374151",
                "wrap": True,
                "margin": "md",
            }
            for line in rich_lines
        )
    bubble: dict[str, Any] = {
        "type": "bubble",
        "body": {"type": "box", "layout": "vertical", "contents": body_contents},
    }
    if hero_image_url:
        bubble["hero"] = {
            "type": "image",
            "url": hero_image_url,
            "size": "full",
            "aspectRatio": "20:13",
            "aspectMode": "cover",
        }
    if actions:
        bubble["footer"] = {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": actions,
        }
    return {"type": "flex", "altText": alt_text, "contents": bubble}


def main_menu_message(*, is_group: bool) -> BotMessage:
    scope_label = "群組帳本" if is_group else "私人帳本"
    return flex_card(
        alt_text=f"{scope_label}功能選單",
        title=f"📒 {scope_label}",
        lines=["請選擇要使用的功能。"],
        actions=[
            postback_button("記支出", "entry.start", direction="expense", style="primary"),
            postback_button("記收入", "entry.start", direction="income", style="primary"),
            postback_button("最近紀錄", "entry.recent"),
            postback_button("本月統計", "entry.summary"),
            postback_button("記帳分類與付款工具設定", "setup.start"),
            postback_button("使用說明", "help"),
        ],
    )


def welcome_message() -> BotMessage:
    return flex_card(
        alt_text="歡迎使用 LINE 助理",
        title="🐸 歡迎使用 LINE 助理",
        lines=[
            "我是您的小幫手－科米蛙。",
            "可以幫您解決各種疑難雜症。",
            "目前提供的服務有：",
            "  1. 記帳中心",
            "請選擇您有興趣的服務，讓科米蛙協助完成初始化設定。",
        ],
        actions=[postback_button("記帳中心", "service.ledger", style="primary")],
        color="#16A34A",
        hero_image_url=(
            "https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcQub2d-8RmsEoaYJJpEzVNAK4LjkRkfs"
            "YX1rpnDUo8SiLI81fS_9QLcI5c&s=10"
        ),
    )


def setup_introduction_message(*, include_payments: bool) -> BotMessage:
    categories = load_setup_sections("setup_categories.txt")
    expense_text = "\n".join(f"• {name}" for name in categories["支出"])
    income_text = "\n".join(f"• {name}" for name in categories["收入"])
    payment_text = ""
    if include_payments:
        payment_names = load_setup_sections("setup_payment_methods.txt")
        payment_lines = "\n".join(f"• {name}" for name in payment_names)
        payment_text = (
            "💳 另外，科米蛙也需要幫您在記帳中心設定專屬的付款工具。"
            "請依照您日常習慣，填入適當的付款子項目。\n\n"
            "「付款方式」包含：\n"
            f"{payment_lines}\n\n"
        )
    return text_message(
        "==== 記帳中心初始化設定 ====\n\n"
        "📝 首先，科米蛙需要幫您在記帳中心設定專屬的記帳項目。"
        "請依照日常收支，填入適當的記帳子分類。\n\n"
        "「支出」的記帳分類包含：\n"
        f"{expense_text}\n\n"
        "「收入」的記帳分類包含：\n"
        f"{income_text}\n\n"
        f"{payment_text}"
        "🔎 科米蛙會提供可複製修改的範本，直接完整回傳即可。\n\n"
        "⚠️ 注意：上述的母分類是固定不可變動的，但您可以在其底下填入自己的子分類。"
    )


def setup_template_message(*, include_payments: bool) -> BotMessage:
    template = load_setup_template("setup_categories.txt").rstrip()
    if include_payments:
        template = f"{template}\n\n{load_setup_template('setup_payment_methods.txt').rstrip()}"
    return text_message(template)


def setup_review_message(conversation: ConversationSession) -> BotMessage:
    review_items = SetupService.review_items(conversation)
    rich_lines = [
        [
            {"type": "span", "text": heading, "weight": "bold", "color": "#16A34A"},
            {"type": "span", "text": items},
        ]
        for heading, items in review_items
    ]
    return flex_card(
        alt_text="設定內容確認",
        title="設定內容確認",
        lines=[],
        actions=[
            postback_button("確認儲存", "setup.confirm", style="primary"),
            postback_button("重新設定", "setup.restart"),
        ],
        rich_lines=rich_lines,
    )


def settings_menu_message(*, is_group: bool) -> BotMessage:
    actions = [
        postback_button("新增自訂記帳分類", "settings.add", style="primary"),
        postback_button("管理既有記帳分類", "settings.categories"),
    ]
    if not is_group:
        actions.append(postback_button("管理付款工具", "settings.payments"))
    actions.append(postback_button("回到選單", "menu"))
    return flex_card(
        alt_text="記帳設定中心",
        title="記帳設定中心",
        lines=[
            "可用完整範本一次新增記帳子分類與付款工具。",
            "已使用的項目停用後仍會保留在歷史交易中。",
        ],
        actions=actions,
    )


def settings_category_list_message(
    categories: list[tuple[Category, str]], *, page: int, has_next: bool
) -> BotMessage:
    if not categories and page == 0:
        return flex_card(
            alt_text="自訂記帳分類",
            title="自訂記帳分類",
            lines=["目前沒有可管理的自訂記帳分類。"],
            actions=[
                postback_button("新增自訂記帳分類", "settings.add", style="primary"),
                postback_button("返回設定", "setup.start"),
            ],
        )
    actions = [
        postback_button(short_button_label(label), "settings.category", category_id=category.id)
        for category, label in categories
    ]
    if page > 0:
        actions.append(postback_button("上一頁", "settings.categories", page=page - 1))
    if has_next:
        actions.append(postback_button("下一頁", "settings.categories", page=page + 1))
    actions.append(postback_button("返回設定", "setup.start"))
    return flex_card(
        alt_text="管理自訂記帳分類",
        title=f"自訂記帳分類｜第 {page + 1} 頁",
        lines=["選擇要改名或停用的記帳分類。"],
        actions=actions,
    )


def settings_category_message(category: Category, path: str) -> BotMessage:
    return flex_card(
        alt_text=f"管理記帳分類 {category.name}",
        title="管理記帳分類",
        lines=[f"路徑：{path}", f"版本：{category.version}"],
        actions=[
            postback_button(
                "新增記帳子分類", "settings.category.add_child", category_id=category.id
            ),
            postback_button("改名", "settings.category.rename", category_id=category.id),
            postback_button("停用", "settings.category.disable", category_id=category.id),
            postback_button("返回列表", "settings.categories"),
        ],
    )


def settings_payment_list_message(
    methods: list[PaymentMethod], *, page: int, has_next: bool
) -> BotMessage:
    if not methods and page == 0:
        return flex_card(
            alt_text="付款工具",
            title="付款工具",
            lines=["目前沒有自訂付款工具；可用新增問答建立。"],
            actions=[
                postback_button("新增付款工具", "settings.add", style="primary"),
                postback_button("返回設定", "setup.start"),
            ],
        )
    actions = [
        postback_button(
            short_button_label(f"{payment_kind_label(method.kind)} › {method.name}"),
            "settings.payment",
            method_id=method.id,
        )
        for method in methods
    ]
    if page > 0:
        actions.append(postback_button("上一頁", "settings.payments", page=page - 1))
    if has_next:
        actions.append(postback_button("下一頁", "settings.payments", page=page + 1))
    actions.append(postback_button("返回設定", "setup.start"))
    return flex_card(
        alt_text="管理付款工具",
        title=f"付款工具｜第 {page + 1} 頁",
        lines=["選擇要改名或停用的付款工具。"],
        actions=actions,
    )


def settings_payment_message(method: PaymentMethod) -> BotMessage:
    return flex_card(
        alt_text=f"管理付款工具 {method.name}",
        title="管理付款工具",
        lines=[
            f"付款方式：{payment_kind_label(method.kind)}",
            f"付款工具：{method.name}",
            f"版本：{method.version}",
        ],
        actions=[
            postback_button("改名", "settings.payment.rename", method_id=method.id),
            postback_button("停用", "settings.payment.disable", method_id=method.id),
            postback_button("返回列表", "settings.payments"),
        ],
    )


def category_options_message(
    *,
    title: str,
    categories: list[Category],
    parent: Category | None,
    conversation: ConversationSession,
    page: int,
) -> BotMessage:
    if parent is None:
        return category_carousel_message(title, categories, conversation)
    actions = [
        *[
            postback_button(
                short_button_label(category.name, maximum=20),
                "entry.category",
                category_id=category.id,
                style="primary",
                flow_id=conversation.id,
                revision=conversation.version,
            )
            for category in categories
        ],
    ]
    columns = [
        {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "contents": actions[index::2],
        }
        for index in range(2)
    ]
    return {
        "type": "flex",
        "altText": f"選擇記帳子分類：{title}",
        "contents": {
            "type": "bubble",
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {
                        "type": "text",
                        "text": f"選擇記帳子分類｜{title}",
                        "weight": "bold",
                        "size": "xl",
                        "color": "#2563EB",
                        "wrap": True,
                    },
                    {
                        "type": "box",
                        "layout": "horizontal",
                        "spacing": "sm",
                        "contents": columns,
                        "margin": "md",
                    },
                ],
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "contents": [postback_button("取消", "cancel")],
            },
        },
    }


def category_carousel_message(
    title: str, categories: list[Category], conversation: ConversationSession
) -> BotMessage:
    bubbles: list[dict[str, Any]] = []
    for category in categories:
        icon, color = _CATEGORY_CARD_STYLES.get(category.name, ("📒", "#2563EB"))
        bubbles.append(
            {
                "type": "bubble",
                "body": {
                    "type": "box",
                    "layout": "vertical",
                    "alignItems": "center",
                    "justifyContent": "center",
                    "backgroundColor": color,
                    "contents": [
                        {"type": "text", "text": icon, "size": "5xl", "align": "center"},
                        {
                            "type": "text",
                            "text": category.name,
                            "weight": "bold",
                            "size": "xxl",
                            "color": "#FFFFFF",
                            "align": "center",
                            "margin": "md",
                        },
                    ],
                },
                "footer": {
                    "type": "box",
                    "layout": "vertical",
                    "contents": [
                        postback_button(
                            f"選擇{category.name}",
                            "entry.category",
                            category_id=category.id,
                            style="primary",
                            flow_id=conversation.id,
                            revision=conversation.version,
                        )
                    ],
                },
            }
        )
    return {
        "type": "flex",
        "altText": f"選擇記帳分類：{title}",
        "contents": {"type": "carousel", "contents": bubbles},
    }


def payment_kind_message(conversation: ConversationSession) -> BotMessage:
    return flex_card(
        alt_text="選擇付款方式",
        title="選擇付款方式",
        lines=["請選擇付款方式。"],
        actions=[
            postback_button(
                "現金",
                "entry.payment.kind",
                kind="cash",
                style="primary",
                flow_id=conversation.id,
                revision=conversation.version,
            ),
            postback_button(
                "信用卡",
                "entry.payment.kind",
                kind="credit_card",
                style="primary",
                flow_id=conversation.id,
                revision=conversation.version,
            ),
            postback_button("取消", "cancel"),
        ],
    )


def payment_method_message(
    kind: PaymentKind,
    methods: list[PaymentMethod],
    *,
    conversation: ConversationSession,
    page: int = 0,
    page_size: int = 8,
) -> BotMessage:
    label = payment_kind_label(kind)
    start = max(page, 0) * page_size
    page_methods = methods[start : start + page_size]
    actions = [
        postback_button(
            short_button_label(method.name),
            "entry.payment.method",
            method_id=method.id,
            style="primary",
            flow_id=conversation.id,
            revision=conversation.version,
        )
        for method in page_methods
    ]
    if page > 0:
        actions.append(
            postback_button(
                "上一頁",
                "entry.payment.page",
                kind=kind.value,
                page=page - 1,
                style="primary",
                flow_id=conversation.id,
                revision=conversation.version,
            )
        )
    if start + page_size < len(methods):
        actions.append(
            postback_button(
                "下一頁",
                "entry.payment.page",
                kind=kind.value,
                page=page + 1,
                style="primary",
                flow_id=conversation.id,
                revision=conversation.version,
            )
        )
    actions.append(postback_button("取消", "cancel"))
    return flex_card(
        alt_text=f"選擇{label}付款工具",
        title=f"選擇{label}付款工具｜第 {page + 1} 頁",
        lines=["未找到合適付款工具時，可以直接使用付款方式。"],
        actions=actions,
    )


def amount_prompt_message() -> BotMessage:
    return text_message(
        "請輸入金額（TWD 正整數），例如：120 或 1,200。",
        quick_actions=[{"type": "message", "label": "取消", "text": "取消"}],
    )


def entry_preview_message(conversation: ConversationSession) -> BotMessage:
    payload = conversation.payload
    direction = "收入" if payload["direction"] == Direction.INCOME.value else "支出"
    note = payload.get("note") or "無"
    lines = [
        f"類型：{direction}",
        f"記帳分類：{' › '.join(payload['category_path'])}",
        f"付款方式：{payment_kind_label(PaymentKind(payload['payment_kind']))}",
        f"付款工具：{payload['payment_name']}",
        f"金額：NT$ {int(payload['amount']):,}",
        f"日期：{payload['transaction_date']}",
        f"備註：{note}",
    ]
    return flex_card(
        alt_text="交易內容確認",
        title="交易內容確認",
        lines=lines,
        actions=[
            postback_button(
                "確認",
                "entry.confirm",
                style="primary",
                flow_id=conversation.id,
                revision=conversation.version,
            ),
            postback_button(
                "修改",
                "entry.modify",
                flow_id=conversation.id,
                revision=conversation.version,
            ),
            postback_button(
                "新增備註",
                "entry.note",
                flow_id=conversation.id,
                revision=conversation.version,
            ),
        ],
    )


def modify_menu_message(conversation: ConversationSession) -> BotMessage:
    transaction_date = date.fromisoformat(conversation.payload["transaction_date"])
    actions = [
        postback_button(
            "記帳分類",
            "entry.modify.field",
            field="category",
            flow_id=conversation.id,
            revision=conversation.version,
        ),
        postback_button(
            "付款方式與工具",
            "entry.modify.field",
            field="payment",
            flow_id=conversation.id,
            revision=conversation.version,
        ),
        postback_button(
            "金額",
            "entry.modify.field",
            field="amount",
            flow_id=conversation.id,
            revision=conversation.version,
        ),
        datetime_button(
            "日期", "entry.date", initial=transaction_date, conversation=conversation
        ),
        postback_button(
            "備註",
            "entry.modify.field",
            field="note",
            flow_id=conversation.id,
            revision=conversation.version,
        ),
        postback_button(
            "返回預覽",
            "entry.preview",
            flow_id=conversation.id,
            revision=conversation.version,
        ),
    ]
    return flex_card(
        alt_text="選擇要修改的欄位",
        title="修改交易",
        lines=["請選擇要修改的欄位。"],
        actions=actions,
    )


def entry_result_message(entry: LedgerEntry, *, updated: bool) -> BotMessage:
    action = "已更新" if updated else "已新增"
    return flex_card(
        alt_text=f"交易{action}",
        title=f"✅ 交易{action}",
        lines=[
            f"記帳分類：{' › '.join(entry.category_path_snapshot)}",
            f"付款方式：{payment_kind_label(entry.payment_kind)}",
            f"付款工具：{entry.payment_name_snapshot}",
            f"金額：NT$ {entry.amount:,}",
            f"日期：{entry.transaction_date.isoformat()}",
            f"備註：{entry.note or '無'}",
        ],
        actions=[postback_button("回到選單", "menu", style="primary")],
        color="#059669",
    )


def recent_entries_message(
    entries: list[LedgerEntry],
    *,
    is_group: bool,
    page: int,
    has_next: bool,
) -> BotMessage:
    if not entries:
        return flex_card(
            alt_text="最近紀錄",
            title="最近紀錄",
            lines=["這一頁沒有已確認的交易。"],
            actions=[
                *(
                    [postback_button("上一頁", "entry.recent", page=page - 1)]
                    if page > 0
                    else []
                ),
                postback_button("回到選單", "menu", style="primary"),
            ],
        )

    bubbles: list[dict[str, Any]] = []
    for entry in entries:
        sign = "+" if entry.direction is Direction.INCOME else "-"
        lines = [
            f"{entry.transaction_date.isoformat()}  {sign}NT$ {entry.amount:,}",
            f"記帳分類：{' › '.join(entry.category_path_snapshot)}",
            f"付款方式：{payment_kind_label(entry.payment_kind)}",
            f"付款工具：{entry.payment_name_snapshot}",
        ]
        if is_group:
            lines.append(f"記帳者：{str(entry.creator_user_id)[:8]}")
        bubble = flex_card(
            alt_text="交易紀錄",
            title="收入" if entry.direction is Direction.INCOME else "支出",
            lines=lines,
            actions=[
                postback_button("修改", "entry.edit", entry_id=entry.id, style="primary"),
                postback_button(
                    "刪除",
                    "entry.delete",
                    entry_id=entry.id,
                    entry_version=entry.version,
                ),
            ],
            color="#059669" if entry.direction is Direction.INCOME else "#DC2626",
        )["contents"]
        bubbles.append(bubble)
    if page > 0 or has_next:
        navigation_actions: list[FlexAction] = []
        if page > 0:
            navigation_actions.append(
                postback_button("上一頁", "entry.recent", page=page - 1)
            )
        if has_next:
            navigation_actions.append(
                postback_button("下一頁", "entry.recent", page=page + 1, style="primary")
            )
        bubbles.append(
            flex_card(
                alt_text="紀錄分頁",
                title=f"第 {page + 1} 頁",
                lines=["繼續瀏覽其他交易紀錄。"],
                actions=navigation_actions,
            )["contents"]
        )
    return {
        "type": "flex",
        "altText": "最近交易紀錄",
        "contents": {"type": "carousel", "contents": bubbles},
    }


def delete_confirmation_message(entry_id: uuid.UUID, entry_version: int) -> BotMessage:
    return flex_card(
        alt_text="確認刪除交易",
        title="刪除交易",
        lines=["刪除後不會出現在帳本與統計中，但系統仍會保留審計紀錄。"],
        actions=[
            postback_button(
                "確認刪除",
                "entry.delete.confirm",
                entry_id=entry_id,
                entry_version=entry_version,
                style="primary",
            ),
            postback_button("取消", "entry.recent"),
        ],
        color="#DC2626",
    )


def summary_message(
    summary: MonthlySummary,
    *,
    year: int,
    month: int,
    previous: tuple[int, int],
    next_month: tuple[int, int] | None,
) -> BotMessage:
    category_lines = [
        f"{name}：{'+' if amount >= 0 else '-'}NT$ {abs(amount):,}"
        for name, amount in sorted(summary.by_category.items())
    ]
    actions = [
        postback_button(
            "上個月", "entry.summary", year=previous[0], month=previous[1]
        )
    ]
    if next_month is not None:
        actions.append(
            postback_button(
                "下個月", "entry.summary", year=next_month[0], month=next_month[1]
            )
        )
    actions.append(postback_button("回到選單", "menu", style="primary"))
    return flex_card(
        alt_text=f"{year} 年 {month} 月統計",
        title=f"{year} 年 {month} 月",
        lines=[
            f"收入：NT$ {summary.income:,}",
            f"支出：NT$ {summary.expense:,}",
            f"結餘：NT$ {summary.balance:,}",
            *category_lines,
        ],
        actions=actions,
        color="#059669" if summary.balance >= 0 else "#DC2626",
    )


def help_message() -> BotMessage:
    return flex_card(
        alt_text="記帳中心使用說明",
        title="使用說明",
        lines=[
            "使用下方選單或輸入「選單」開啟功能。",
            "群組中可輸入「記帳」或提及 Bot。",
            "進行中隨時輸入「取消」即可中止。",
            "記帳分類前兩層固定，第三層之後可自行設定。",
            "群組交易共用；付款工具由每位成員在私聊設定。",
        ],
        actions=[postback_button("回到選單", "menu", style="primary")],
    )

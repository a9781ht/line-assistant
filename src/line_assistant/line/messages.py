from __future__ import annotations

import uuid
from datetime import date
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
    bubble: dict[str, Any] = {
        "type": "bubble",
        "body": {"type": "box", "layout": "vertical", "contents": body_contents},
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
            "目前提供的服務：",
            "1. 記帳中心",
            "請選擇您有興趣的服務，讓科米蛙協助完成初始化設定。",
        ],
        actions=[postback_button("記帳中心", "service.ledger", style="primary")],
        color="#16A34A",
    )


def setup_introduction_message(*, include_payments: bool) -> BotMessage:
    payment_text = (
        "另外，也請設定付款方式底下的付款工具：現金、信用卡。\n\n"
        if include_payments
        else ""
    )
    return text_message(
        "首先，科米蛙需要幫您在記帳中心設定專屬的記帳項目。\n\n"
        "「支出」的記帳分類包含：\n"
        "• 食　• 衣　• 住　• 行　• 育\n"
        "• 樂　• 醫療　• 理財　• 其他\n\n"
        "「收入」的記帳分類包含：\n"
        "• 一般收入　• 投資收入　• 其他\n\n"
        "請依照日常收支，填入適當的記帳子分類。\n"
        f"{payment_text}"
        "科米蛙會提供可複製修改的範本，直接完整回傳即可。\n"
        "注意：記帳分類的第一、二層，以及付款方式皆為固定項目。"
    )


def setup_template_message(*, include_payments: bool) -> BotMessage:
    payment_template = (
        "\n\n• 現金\n"
        "  ◦ 實體\n"
        "  ◦ 轉帳\n"
        "  ◦ 約當\n\n"
        "• 信用卡\n"
        "  ◦ 永豐DAWHO卡\n"
        "  ◦ 台新Richart卡\n"
        "  ◦ 國泰Cube卡\n"
        "  ◦ 富邦J卡\n"
        "  ◦ 玉山Unicard"
        if include_payments
        else ""
    )
    return text_message(
        "請複製下方範本後，依需求增刪「▪」開頭的記帳子分類，\n"
        "或增刪現金、信用卡下以「◦」開頭的付款工具，再一次完整回傳。\n"
        "請保留收入／支出下的「•」與「◦」，以及付款方式的「•」固定項目。\n\n"
        "• 收入\n"
        "  ◦ 一般收入\n"
        "    ▪ 薪資\n"
        "    ▪ 上半年績效\n"
        "    ▪ 下半年績效\n"
        "    ▪ 年終獎金\n"
        "    ▪ Q1 季獎金\n"
        "    ▪ Q2 季獎金\n"
        "    ▪ Q3 季獎金\n"
        "    ▪ Q4 季獎金\n"
        "    ▪ 勞動節\n"
        "    ▪ 端午節\n"
        "    ▪ 中秋節\n"
        "    ▪ 生日\n"
        "    ▪ 開工\n"
        "    ▪ 尾牙\n"
        "    ▪ 旅遊補助\n"
        "    ▪ 差旅補助\n"
        "    ▪ 中獎禮券\n"
        "  ◦ 投資收入\n"
        "    ▪ 銀行利息\n"
        "    ▪ 股利所得\n"
        "    ▪ 股票買賣\n"
        "  ◦ 其他\n\n"
        "• 支出\n"
        "  ◦ 食\n"
        "    ▪ 早餐\n"
        "    ▪ 早午餐\n"
        "    ▪ 午餐\n"
        "    ▪ 下午茶\n"
        "    ▪ 晚餐\n"
        "    ▪ 宵夜\n"
        "    ▪ 節日餐\n"
        "    ▪ 點心零嘴\n"
        "    ▪ 食材\n"
        "  ◦ 衣\n"
        "    ▪ 服裝\n"
        "    ▪ 鞋子\n"
        "    ▪ 配件\n"
        "    ▪ 剪髮理容\n"
        "  ◦ 住\n"
        "    ▪ 房租\n"
        "    ▪ 電費\n"
        "    ▪ 水費\n"
        "    ▪ 網路費\n"
        "    ▪ 電話費\n"
        "    ▪ 綜所稅\n"
        "    ▪ 家電傢俱用品\n"
        "    ▪ 生活必需用品\n"
        "    ▪ 串流訂閱\n"
        "    ▪ 雜支\n"
        "  ◦ 行\n"
        "    ▪ 油錢\n"
        "    ▪ 捷運\n"
        "    ▪ 公車\n"
        "    ▪ 客運\n"
        "    ▪ 台鐵\n"
        "    ▪ 區間\n"
        "    ▪ 高鐵\n"
        "    ▪ 飛機\n"
        "    ▪ 停車費\n"
        "    ▪ 過路費\n"
        "    ▪ 租車\n"
        "    ▪ 計程車\n"
        "    ▪ 維修保養\n"
        "    ▪ 美容洗車\n"
        "    ▪ 牌照稅\n"
        "    ▪ 燃料費\n"
        "    ▪ 汽機車保險\n"
        "    ▪ 驗車費\n"
        "    ▪ 材料費\n"
        "    ▪ 罰單\n"
        "  ◦ 育\n"
        "    ▪ 書籍\n"
        "    ▪ 課程\n"
        "    ▪ AI 訂閱\n"
        "    ▪ 考試\n"
        "  ◦ 樂\n"
        "    ▪ 旅行住宿\n"
        "    ▪ 旅行遊玩\n"
        "    ▪ 旅行購物\n"
        "    ▪ 旅行吃飯\n"
        "    ▪ 旅行保險\n"
        "    ▪ 旅行雜支\n"
        "    ▪ 運動健身\n"
        "    ▪ 社交\n"
        "    ▪ 生活奢侈用品\n"
        "    ▪ 送禮物\n"
        "    ▪ 婚喪喜慶\n"
        "  ◦ 醫療\n"
        "    ▪ 診所就醫\n"
        "    ▪ 購買藥物\n"
        "    ▪ 保費\n"
        "  ◦ 理財\n"
        "    ▪ 儲蓄\n"
        "    ▪ ETF 股票\n"
        "    ▪ 個股股票\n"
        "  ◦ 其他"
        f"{payment_template}"
    )


def setup_review_message(conversation: ConversationSession) -> BotMessage:
    lines = SetupService.review_lines(conversation)
    return flex_card(
        alt_text="設定內容確認",
        title="設定內容確認",
        lines=lines,
        actions=[
            postback_button("確認儲存", "setup.confirm", style="primary"),
            postback_button("重新設定", "setup.restart"),
        ],
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
    page_size: int = 6,
) -> BotMessage:
    start = page * page_size
    page_items = categories[start : start + page_size]
    actions = [
        postback_button(
            short_button_label(category.name),
            "entry.category",
            category_id=category.id,
            flow_id=conversation.id,
            revision=conversation.version,
        )
        for category in page_items
    ]
    if parent is not None:
        actions.insert(
            0,
            postback_button(
                f"使用「{parent.name}」",
                "entry.category.use",
                category_id=parent.id,
                style="primary",
                flow_id=conversation.id,
                revision=conversation.version,
            ),
        )
    if page > 0:
        actions.append(
            postback_button(
                "上一頁",
                "entry.category.page",
                parent_id=parent.id if parent else "root",
                page=page - 1,
                flow_id=conversation.id,
                revision=conversation.version,
            )
        )
    if start + page_size < len(categories):
        actions.append(
            postback_button(
                "下一頁",
                "entry.category.page",
                parent_id=parent.id if parent else "root",
                page=page + 1,
                flow_id=conversation.id,
                revision=conversation.version,
            )
        )
    actions.append(postback_button("取消", "cancel"))
    return flex_card(
        alt_text=f"選擇記帳分類：{title}",
        title=f"選擇記帳分類｜{title}",
        lines=[f"目前共有 {len(categories)} 個選項。"],
        actions=actions,
    )


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
                flow_id=conversation.id,
                revision=conversation.version,
            )
        )
    actions.append(
        postback_button(
            f"直接使用「{label}」",
            "entry.payment.method",
            method_id="none",
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

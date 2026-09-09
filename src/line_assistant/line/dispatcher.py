from __future__ import annotations

import uuid
from datetime import date, datetime
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

from linebot.v3.webhooks import (
    Event,
    FollowEvent,
    GroupSource,
    JoinEvent,
    LeaveEvent,
    MemberJoinedEvent,
    MemberLeftEvent,
    MessageEvent,
    PostbackEvent,
    RoomSource,
    TextMessageContent,
    UnfollowEvent,
    UserMentionee,
    UserSource,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from line_assistant.core.errors import DomainError, InvalidStateError
from line_assistant.db.base import utc_now
from line_assistant.db.models import (
    AssistantScope,
    AuditLog,
    Category,
    ConversationSession,
    Direction,
    PaymentKind,
    ScopeMembership,
    ScopeType,
)
from line_assistant.ledger.accounting import AccountingService
from line_assistant.ledger.catalog import CategoryService, PaymentMethodService
from line_assistant.ledger.context import IdentityService, ScopeContext
from line_assistant.ledger.conversation import ConversationService
from line_assistant.ledger.parsing import parse_batch_names, parse_twd_amount
from line_assistant.ledger.setup import SetupService
from line_assistant.line.messages import (
    BotMessage,
    amount_prompt_message,
    category_options_message,
    delete_confirmation_message,
    entry_preview_message,
    entry_result_message,
    flex_card,
    help_message,
    main_menu_message,
    modify_menu_message,
    payment_kind_message,
    payment_method_message,
    postback_button,
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
    text_message,
    welcome_message,
)


class EventDispatcher:
    # 列出所有「正在進行中的記帳草稿」專用按鈕事件
    # 它們會先比對目前對話的 flow_id 與 revision，確認按鈕來自最新草稿，才允許操作
    # 這能避免用戶點到舊的 LINE 訊息，意外修改或送出另一筆新草稿
    _STATEFUL_ENTRY_ACTIONS = frozenset(
        {
            "entry.category",       # 開啟記帳分類或繼續往下一層記帳子分類
            "entry.category.use",   # 確認使用目前選取的記帳分類
            "entry.category.page",  # 切換記帳分類清單的分頁
            "entry.payment.kind",   # 選擇付款方式（現金、信用卡）
            "entry.payment.page",   # 切換付款工具清單的分頁
            "entry.payment.method", # 選擇具體付款工具或直接使用付款方式
            "entry.preview",        # 重新顯示目前交易草稿的預覽
            "entry.note",           # 進入備註輸入流程
            "entry.modify",         # 開啟交易草稿的修改選單
            "entry.modify.field",   # 選擇要修改的交易欄位
            "entry.date",           # 套用日期選擇器回傳的交易日期
            "entry.confirm",        # 確認並新增或更新交易
        }
    )

    def __init__(
        self,
        session: AsyncSession,
        *,
        timezone: str,
        draft_ttl_minutes: int,
        max_amount: int,
    ) -> None:
        self.session = session
        self.identities = IdentityService(session, timezone=timezone)
        self.conversations = ConversationService(session)
        self.accounting = AccountingService(
            session,
            self.conversations,
            draft_ttl_minutes=draft_ttl_minutes,
            max_amount=max_amount,
        )
        self.categories = CategoryService(session)
        self.payments = PaymentMethodService(session)
        self.timezone = timezone
        self.max_amount = max_amount

    async def dispatch(self, event: Event) -> list[BotMessage]:
        """根據 LINE 事件型別進行分流。"""

        # 用戶加 Bot 好友
        #  1. 建立私人帳本 context；
        #  2. 顯示歡迎卡片；
        #  3. 若尚未完成設定，自動開始首次設定；
        #  4. 若已設定完成，回傳主選單。
        if isinstance(event, FollowEvent):
            context = await self._context(event, is_friend=True)
            return [welcome_message()]
        
        # 用戶解除 Bot 好友
        if isinstance(event, UnfollowEvent):
            await self._mark_unfollowed(event)
            return []
        
        # Bot 被加入群組或多人聊天室
        #  1. 建立群組 scope 與群組帳本；
        #  2. 建立固定記帳分類；
        #  3. 回覆說明：
        #     - 輸入「選單」可開功能；
        #     - 成員須先私訊完成付款工具設定；
        #  4. 顯示群組選單。
        if isinstance(event, JoinEvent):
            await self._ensure_joined_scope(event)
            return [
                flex_card(
                    alt_text="記帳助理已加入群組",
                    title="記帳助理已加入",
                    lines=[
                        "群組帳本已建立。輸入「選單」即可開啟功能。",
                        "每位成員請先加 Bot 好友並在私聊完成付款工具設定。",
                    ],
                ),
                main_menu_message(is_group=True),
            ]
        
        # Bot 被移出群組或多人聊天室
        if isinstance(event, LeaveEvent):
            await self._deactivate_scope(event)
            return []
        
        # 群組或多人聊天室有新成員加入
        if isinstance(event, MemberJoinedEvent):
            await self._members_joined(event)
            return []
        
        # 群組或多人聊天室有成員離開
        if isinstance(event, MemberLeftEvent):
            await self._members_left(event)
            return []
        
        # 用戶傳送文字訊息
        if isinstance(event, MessageEvent) and isinstance(event.message, TextMessageContent):
            context = await self._context(event)
            mention = event.message.mention
            bot_mentioned = bool(
                mention
                and any(
                    isinstance(mentionee, UserMentionee) and mentionee.is_self
                    for mentionee in mention.mentionees
                )
            )
            return await self._handle_text(
                context, event.message.text, bot_mentioned=bot_mentioned
            )
        
        # 用戶點選單按鈕觸發 Postback 事件
        if isinstance(event, PostbackEvent):
            context = await self._context(event)
            params = event.postback.params or {}
            try:
                return await self._handle_postback(context, event.postback.data, params)
            except (KeyError, TypeError, ValueError) as error:
                raise DomainError("操作資料已失效，請重新開啟選單") from error
        return []

    async def _handle_text(
        self,
        context: ScopeContext,
        raw_text: str,
        *,
        bot_mentioned: bool = False,
    ) -> list[BotMessage]:
        text = raw_text.strip()
        if text == "取消":
            await self.conversations.clear(context.scope.id, context.user.id)
            return [text_message("已取消目前流程。"), self._menu(context)]
        if text in {"選單", "記帳", "@Bot", "@bot"}:
            return [self._menu(context)]
        if text == "收入":
            return await self._start_entry(context, Direction.INCOME)
        if text == "支出":
            return await self._start_entry(context, Direction.EXPENSE)
        if bot_mentioned:
            if "支出" in text:
                return await self._start_entry(context, Direction.EXPENSE)
            if "收入" in text:
                return await self._start_entry(context, Direction.INCOME)
            return [self._menu(context)]

        conversation = await self.conversations.get(context.scope.id, context.user.id)
        if conversation is None:
            if context.scope.scope_type is not ScopeType.PERSONAL:
                return []
            return [self._menu(context)]

        if conversation.flow == "setup" and conversation.state == "awaiting_setup_template":
            updated = await SetupService(context, self.conversations).answer_template(
                conversation, text
            )
            return [text_message("格式正確，科米蛙理解。"), setup_review_message(updated)]

        if conversation.flow == "entry" and conversation.state == "awaiting_amount":
            amount = parse_twd_amount(text, maximum=self.max_amount)
            updated = await self.accounting.set_amount(context, amount)
            return [entry_preview_message(updated)]

        if conversation.flow == "entry" and conversation.state == "awaiting_note":
            updated = await self.accounting.set_note(context, text)
            return [entry_preview_message(updated)]

        if conversation.flow == "settings" and conversation.state == "awaiting_category_name":
            await self._lock_scope(context)
            names = parse_batch_names(text, max_items=1)
            if not names:
                raise DomainError("請輸入新的記帳分類名稱")
            category = await self.categories.get(
                context.ledger.id, uuid.UUID(conversation.payload["category_id"])
            )
            old_name = category.name
            await self.categories.rename(category, names[0])
            self._audit_setting(
                context,
                entity_type="category",
                entity_id=category.id,
                action="rename",
                before={"name": old_name},
                after={"name": category.name},
            )
            await self.conversations.clear(context.scope.id, context.user.id)
            return [
                text_message("記帳分類名稱已更新。"),
                await self._settings_categories(context, 0),
            ]

        if conversation.flow == "settings" and conversation.state == "awaiting_category_children":
            await self._lock_scope(context)
            names = parse_batch_names(text)
            if not names:
                raise DomainError("請至少輸入一個記帳子分類名稱")
            parent = await self.categories.get(
                context.ledger.id, uuid.UUID(conversation.payload["category_id"])
            )
            children = await self.categories.add_children(parent, names)
            for child in children:
                self._audit_setting(
                    context,
                    entity_type="category",
                    entity_id=child.id,
                    action="create",
                    before={},
                    after={"name": child.name, "parent_id": str(parent.id)},
                )
            await self.conversations.clear(context.scope.id, context.user.id)
            return [
                text_message(f"已在「{parent.name}」下新增 {len(children)} 個記帳子分類。"),
                await self._settings_categories(context, 0),
            ]

        if conversation.flow == "settings" and conversation.state == "awaiting_payment_name":
            self._require_personal_scope(context)
            await self._lock_scope(context)
            names = parse_batch_names(text, max_items=1)
            if not names:
                raise DomainError("請輸入新的付款工具名稱")
            method = await self.payments.get_for_user(
                context.user.id, uuid.UUID(conversation.payload["method_id"])
            )
            old_name = method.name
            await self.payments.rename(method, names[0])
            self._audit_setting(
                context,
                entity_type="payment_method",
                entity_id=method.id,
                action="rename",
                before={"name": old_name},
                after={"name": method.name},
            )
            await self.conversations.clear(context.scope.id, context.user.id)
            return [text_message("付款工具名稱已更新。"), await self._settings_payments(context, 0)]

        return [text_message("目前無法套用這段文字；可輸入「取消」後重新開始。")]

    async def _handle_postback(
        self,
        context: ScopeContext,
        data: str,
        postback_params: dict[str, str],
    ) -> list[BotMessage]:
        if len(data) > 2_000:
            raise DomainError("操作資料過長，請重新開啟選單")
        try:
            values = parse_qs(data, keep_blank_values=True, max_num_fields=20)
        except ValueError as error:
            raise DomainError("操作資料格式錯誤，請重新開啟選單") from error
        action = values.get("act", [""])[0]

        def value(key: str, default: str = "") -> str:
            return values.get(key, [default])[0]

        if action in self._STATEFUL_ENTRY_ACTIONS:
            await self._validate_entry_action(
                context,
                flow_id=value("flow_id"),
                revision=value("revision"),
            )

        if action == "menu":
            return [self._menu(context)]
        if action == "help":
            return [help_message()]
        if action == "service.ledger":
            if context.scope.setup_completed:
                return [self._menu(context)]
            setup_conversation = await SetupService(context, self.conversations).start()
            return self._setup_messages(setup_conversation)
        if action == "cancel":
            await self.conversations.clear(context.scope.id, context.user.id)
            return [text_message("已取消目前流程。"), self._menu(context)]
        if action == "setup.start":
            if context.scope.setup_completed:
                return [
                    settings_menu_message(
                        is_group=context.scope.scope_type is not ScopeType.PERSONAL
                    )
                ]
            setup_conversation = await SetupService(context, self.conversations).start()
            return self._setup_messages(setup_conversation)
        if action == "settings.add":
            setup_conversation = await SetupService(context, self.conversations).start(restart=True)
            return self._setup_messages(setup_conversation)
        if action == "setup.restart":
            setup_conversation = await SetupService(context, self.conversations).start(restart=True)
            return self._setup_messages(setup_conversation)
        if action == "settings.categories":
            return [await self._settings_categories(context, int(value("page", "0")))]
        if action == "settings.category":
            category = await self.categories.get(
                context.ledger.id, uuid.UUID(value("category_id"))
            )
            names, _ = await self.categories.path(category)
            return [settings_category_message(category, " › ".join(names))]
        if action == "settings.category.rename":
            category = await self.categories.get(
                context.ledger.id, uuid.UUID(value("category_id"))
            )
            if category.is_system:
                raise DomainError("固定記帳分類不能改名")
            await self.conversations.set(
                context.scope.id,
                context.user.id,
                flow="settings",
                state="awaiting_category_name",
                payload={"category_id": str(category.id)},
                ttl_minutes=30,
            )
            return [text_message(f"請輸入「{category.name}」的新名稱，或輸入「取消」。")]
        if action == "settings.category.add_child":
            category = await self.categories.get(
                context.ledger.id, uuid.UUID(value("category_id"))
            )
            if category.is_system:
                raise DomainError("請用新增問答在固定第二層建立第一個自訂記帳分類")
            await self.conversations.set(
                context.scope.id,
                context.user.id,
                flow="settings",
                state="awaiting_category_children",
                payload={"category_id": str(category.id)},
                ttl_minutes=30,
            )
            return [
                text_message(
                    f"請輸入要加在「{category.name}」下的記帳子分類；可用頓號、逗號或換行分隔。"
                )
            ]
        if action == "settings.category.disable":
            category = await self.categories.get(
                context.ledger.id, uuid.UUID(value("category_id"))
            )
            return [
                flex_card(
                    alt_text="確認停用記帳分類",
                    title="確認停用記帳分類",
                    lines=[f"停用「{category.name}」後，新交易將不能再選擇此記帳分類。"],
                    actions=[
                        postback_button(
                            "確認停用",
                            "settings.category.disable.confirm",
                            category_id=category.id,
                            style="primary",
                        ),
                        postback_button("取消", "settings.categories"),
                    ],
                    color="#DC2626",
                )
            ]
        if action == "settings.category.disable.confirm":
            await self._lock_scope(context)
            category = await self.categories.get(
                context.ledger.id, uuid.UUID(value("category_id"))
            )
            await self.categories.disable(category)
            self._audit_setting(
                context,
                entity_type="category",
                entity_id=category.id,
                action="disable",
                before={"active": True},
                after={"active": False},
            )
            return [text_message("記帳分類已停用。"), await self._settings_categories(context, 0)]
        if action == "settings.payments":
            self._require_personal_scope(context)
            return [await self._settings_payments(context, int(value("page", "0")))]
        if action == "settings.payment":
            self._require_personal_scope(context)
            method = await self.payments.get_for_user(
                context.user.id, uuid.UUID(value("method_id"))
            )
            return [settings_payment_message(method)]
        if action == "settings.payment.rename":
            self._require_personal_scope(context)
            method = await self.payments.get_for_user(
                context.user.id, uuid.UUID(value("method_id"))
            )
            await self.conversations.set(
                context.scope.id,
                context.user.id,
                flow="settings",
                state="awaiting_payment_name",
                payload={"method_id": str(method.id)},
                ttl_minutes=30,
            )
            return [text_message(f"請輸入「{method.name}」的新名稱，或輸入「取消」。")]
        if action == "settings.payment.disable":
            self._require_personal_scope(context)
            method = await self.payments.get_for_user(
                context.user.id, uuid.UUID(value("method_id"))
            )
            return [
                flex_card(
                    alt_text="確認停用付款工具",
                    title="確認停用付款工具",
                    lines=[f"停用「{method.name}」後，新交易將不能再選擇此付款工具。"],
                    actions=[
                        postback_button(
                            "確認停用",
                            "settings.payment.disable.confirm",
                            method_id=method.id,
                            style="primary",
                        ),
                        postback_button("取消", "settings.payments"),
                    ],
                    color="#DC2626",
                )
            ]
        if action == "settings.payment.disable.confirm":
            self._require_personal_scope(context)
            await self._lock_scope(context)
            method = await self.payments.get_for_user(
                context.user.id, uuid.UUID(value("method_id"))
            )
            self.payments.disable(method)
            self._audit_setting(
                context,
                entity_type="payment_method",
                entity_id=method.id,
                action="disable",
                before={"active": True},
                after={"active": False},
            )
            return [text_message("付款工具已停用。"), await self._settings_payments(context, 0)]
        if action == "setup.confirm":
            setup_conversation_or_none = await self.conversations.get(
                context.scope.id, context.user.id
            )
            if setup_conversation_or_none is None:
                raise InvalidStateError("設定流程已過期，請重新開始")
            await SetupService(context, self.conversations).confirm(setup_conversation_or_none)
            return [text_message("設定已儲存。"), self._menu(context)]
        if action == "entry.start":
            return await self._start_entry(context, Direction(value("direction")))
        if action == "entry.category":
            return await self._open_category(context, uuid.UUID(value("category_id")))
        if action == "entry.category.use":
            entry_conversation = await self.accounting.select_category(
                context, uuid.UUID(value("category_id"))
            )
            if entry_conversation.state == "review":
                return [entry_preview_message(entry_conversation)]
            return [payment_kind_message(entry_conversation)]
        if action == "entry.category.page":
            parent_value = value("parent_id")
            parent_id = None if parent_value == "root" else uuid.UUID(parent_value)
            return [await self._render_category_page(context, parent_id, int(value("page", "0")))]
        if action == "entry.payment.kind":
            kind = PaymentKind(value("kind"))
            entry_conversation = await self.accounting.select_payment_kind(context, kind)
            methods = await self.payments.list_for_user(context.user.id, kind)
            return [
                payment_method_message(
                    kind,
                    methods,
                    conversation=entry_conversation,
                )
            ]
        if action == "entry.payment.page":
            entry_conversation = await self.accounting.require_entry_session(context)
            if entry_conversation.state != "selecting_payment_method":
                raise InvalidStateError("目前不是選擇付款工具的步驟")
            kind = PaymentKind(value("kind"))
            if kind is not PaymentKind(entry_conversation.payload["payment_kind"]):
                raise InvalidStateError("付款方式已變更，請重新選擇")
            methods = await self.payments.list_for_user(context.user.id, kind)
            return [
                payment_method_message(
                    kind,
                    methods,
                    conversation=entry_conversation,
                    page=max(int(value("page", "0")), 0),
                )
            ]
        if action == "entry.payment.method":
            method_value = value("method_id")
            method_id = None if method_value == "none" else uuid.UUID(method_value)
            entry_conversation = await self.accounting.select_payment_method(context, method_id)
            if entry_conversation.state == "review":
                return [entry_preview_message(entry_conversation)]
            return [amount_prompt_message()]
        if action == "entry.preview":
            entry_conversation = await self.accounting.require_entry_session(context)
            return [entry_preview_message(entry_conversation)]
        if action == "entry.note":
            await self.accounting.begin_note(context)
            return [text_message("請輸入備註內容；若不需要可輸入「取消」。")]
        if action == "entry.modify":
            entry_conversation = await self.accounting.require_entry_session(context)
            return [modify_menu_message(entry_conversation)]
        if action == "entry.modify.field":
            field = value("field")
            entry_conversation = await self.accounting.begin_modify(context, field)
            if field == "category":
                return [await self._render_category_page(context, None, 0)]
            if field == "payment":
                return [payment_kind_message(entry_conversation)]
            if field == "amount":
                return [amount_prompt_message()]
            return [text_message("請輸入新的備註。")]
        if action == "entry.date":
            date_value = postback_params.get("date")
            if not date_value:
                raise InvalidStateError("沒有收到日期")
            updated = await self.accounting.set_date(context, date.fromisoformat(date_value))
            return [entry_preview_message(updated)]
        if action == "entry.confirm":
            entry_conversation = await self.accounting.require_entry_session(context)
            is_updated = "editing_entry_id" in entry_conversation.payload
            entry = await self.accounting.confirm(context)
            return [entry_result_message(entry, updated=is_updated)]
        if action == "entry.recent":
            page = max(int(value("page", "0")), 0)
            entries, has_next = await self.accounting.recent_entries(context, page=page)
            return [
                recent_entries_message(
                    entries,
                    is_group=context.scope.scope_type is not ScopeType.PERSONAL,
                    page=page,
                    has_next=has_next,
                )
            ]
        if action == "entry.summary":
            today = datetime.now(ZoneInfo(context.scope.timezone)).date()
            year = int(value("year", str(today.year)))
            month = int(value("month", str(today.month)))
            try:
                requested = date(year, month, 1)
            except ValueError as error:
                raise DomainError("查詢月份格式錯誤") from error
            current = date(today.year, today.month, 1)
            if requested > current or not 2000 <= year <= 2100:
                raise DomainError("查詢月份超出允許範圍")
            summary = await self.accounting.monthly_summary(
                context, year=year, month=month
            )
            previous = self._shift_month(year, month, -1)
            next_month = self._shift_month(year, month, 1) if requested < current else None
            return [
                summary_message(
                    summary,
                    year=year,
                    month=month,
                    previous=previous,
                    next_month=next_month,
                )
            ]
        if action == "entry.edit":
            conversation = await self.accounting.begin_edit(
                context, uuid.UUID(value("entry_id"))
            )
            return [entry_preview_message(conversation)]
        if action == "entry.delete":
            return [
                delete_confirmation_message(
                    uuid.UUID(value("entry_id")), int(value("entry_version"))
                )
            ]
        if action == "entry.delete.confirm":
            await self.accounting.delete_entry(
                context,
                uuid.UUID(value("entry_id")),
                expected_version=int(value("entry_version")),
            )
            return [text_message("交易已刪除。"), self._menu(context)]
        raise DomainError("無法辨識這個操作，請重新開啟選單")

    async def _start_entry(
        self, context: ScopeContext, direction: Direction
    ) -> list[BotMessage]:
        await self._ensure_ready_for_entry(context)
        await self.accounting.start_entry(context, direction)
        return [await self._render_category_page(context, None, 0)]

    async def _ensure_ready_for_entry(self, context: ScopeContext) -> None:
        """
        確保用戶已完成私人記帳分類與付款工具設定，才能進行記帳。
        
          - 私人帳本：本人必須先完成初始化；
          - 群組帳本：本人必須先加 Bot 好友，並在一對一聊天完成私人付款工具設定。
                     因付款工具資料不應直接由群組共用。
        """
        if context.scope.scope_type is ScopeType.PERSONAL:
            if not context.scope.setup_completed:
                raise DomainError("請先從「設定」完成私人記帳分類與付款工具初始化")
            return
        if not context.user.is_friend:
            raise DomainError("請先加 Bot 好友，並在一對一聊天完成付款工具設定")
        personal_scope = await self.session.scalar(
            select(AssistantScope).where(
                AssistantScope.scope_type == ScopeType.PERSONAL,
                AssistantScope.owner_user_id == context.user.id,
                AssistantScope.setup_completed.is_(True),
            )
        )
        if personal_scope is None:
            raise DomainError("請先到一對一聊天完成私人付款工具設定")

    async def _open_category(
        self, context: ScopeContext, category_id: uuid.UUID
    ) -> list[BotMessage]:
        category = await self.categories.get(context.ledger.id, category_id)
        children = await self.categories.list_children(context.ledger.id, category.id)
        if not children:
            entry_conversation = await self.accounting.select_category(context, category.id)
            if entry_conversation.state == "review":
                return [entry_preview_message(entry_conversation)]
            return [payment_kind_message(entry_conversation)]
        return [await self._render_category_page(context, category.id, 0)]

    async def _render_category_page(
        self, context: ScopeContext, parent_id: uuid.UUID | None, page: int
    ) -> BotMessage:
        conversation = await self.accounting.require_entry_session(context)
        direction = Direction(conversation.payload["direction"])
        if parent_id is None:
            root = await self.categories.get_by_system_key(context.ledger.id, direction.value)
            children = await self.categories.list_children(context.ledger.id, root.id)
            return category_options_message(
                title=root.name,
                categories=children,
                parent=None,
                conversation=conversation,
                page=max(page, 0),
            )
        parent = await self.categories.get(context.ledger.id, parent_id)
        children = await self.categories.list_children(context.ledger.id, parent.id)
        return category_options_message(
            title=parent.name,
            categories=children,
            parent=parent,
            conversation=conversation,
            page=max(page, 0),
        )

    async def _validate_entry_action(
        self,
        context: ScopeContext,
        *,
        flow_id: str,
        revision: str,
    ) -> None:
        conversation = await self.accounting.require_entry_session(context)
        try:
            expected_id = uuid.UUID(flow_id)
            expected_revision = int(revision)
        except (TypeError, ValueError) as error:
            raise InvalidStateError("這個操作已失效，請使用最新訊息") from error
        if conversation.id != expected_id or conversation.version != expected_revision:
            raise InvalidStateError("這個操作已失效，請使用最新訊息")

    def _menu(self, context: ScopeContext) -> BotMessage:
        return main_menu_message(is_group=context.scope.scope_type is not ScopeType.PERSONAL)

    @staticmethod
    def _setup_messages(conversation: ConversationSession) -> list[BotMessage]:
        if conversation.state == "setup_review":
            return [setup_review_message(conversation)]
        include_payments = bool(conversation.payload["include_payments"])
        return [
            setup_introduction_message(include_payments=include_payments),
            setup_template_message(include_payments=include_payments),
        ]

    async def _settings_categories(self, context: ScopeContext, page: int) -> BotMessage:
        categories, has_next = await self.categories.list_custom(
            context.ledger.id, page=max(page, 0)
        )
        typed_labels: list[tuple[Category, str]] = []
        for category in categories:
            names, _ = await self.categories.path(category)
            typed_labels.append((category, " › ".join(names)))
        return settings_category_list_message(
            typed_labels,
            page=max(page, 0),
            has_next=has_next,
        )

    async def _settings_payments(self, context: ScopeContext, page: int) -> BotMessage:
        self._require_personal_scope(context)
        methods, has_next = await self.payments.list_all_for_user(
            context.user.id, page=max(page, 0)
        )
        return settings_payment_list_message(
            methods,
            page=max(page, 0),
            has_next=has_next,
        )

    @staticmethod
    def _require_personal_scope(context: ScopeContext) -> None:
        if context.scope.scope_type is not ScopeType.PERSONAL:
            raise DomainError("付款工具只能在與 Bot 的一對一聊天中管理")

    def _audit_setting(
        self,
        context: ScopeContext,
        *,
        entity_type: str,
        entity_id: uuid.UUID,
        action: str,
        before: dict[str, object],
        after: dict[str, object],
    ) -> None:
        if entity_type == "category":
            context.scope.category_version += 1
        self.session.add(
            AuditLog(
                scope_id=context.scope.id,
                entity_type=entity_type,
                entity_id=entity_id,
                action=action,
                actor_user_id=context.user.id,
                before_data=before,
                after_data=after,
            )
        )

    async def _lock_scope(self, context: ScopeContext) -> None:
        await self.session.execute(
            select(AssistantScope)
            .where(AssistantScope.id == context.scope.id)
            .with_for_update()
        )

    @staticmethod
    def _shift_month(year: int, month: int, offset: int) -> tuple[int, int]:
        zero_based = year * 12 + month - 1 + offset
        return zero_based // 12, zero_based % 12 + 1

    async def _context(self, event: Event, *, is_friend: bool | None = None) -> ScopeContext:
        source = event.source
        if isinstance(source, UserSource) and source.user_id:
            return await self.identities.ensure_context(
                ScopeType.PERSONAL,
                source.user_id,
                source.user_id,
                is_friend=True if is_friend is None else is_friend,
            )
        if isinstance(source, GroupSource) and source.user_id:
            return await self.identities.ensure_context(
                ScopeType.GROUP,
                source.group_id,
                source.user_id,
            )
        if isinstance(source, RoomSource) and source.user_id:
            return await self.identities.ensure_context(
                ScopeType.ROOM,
                source.room_id,
                source.user_id,
            )
        raise DomainError("此事件沒有可識別的用戶")

    async def _ensure_joined_scope(self, event: JoinEvent) -> None:
        source = event.source
        if isinstance(source, GroupSource):
            await self.identities.ensure_scope(ScopeType.GROUP, source.group_id)
        elif isinstance(source, RoomSource):
            await self.identities.ensure_scope(ScopeType.ROOM, source.room_id)

    async def _mark_unfollowed(self, event: UnfollowEvent) -> None:
        source = event.source
        if isinstance(source, UserSource) and source.user_id:
            await self.identities.ensure_user(source.user_id, is_friend=False)

    async def _deactivate_scope(self, event: LeaveEvent) -> None:
        source = event.source
        scope_type: ScopeType | None = None
        source_id: str | None = None
        if isinstance(source, GroupSource):
            scope_type, source_id = ScopeType.GROUP, source.group_id
        elif isinstance(source, RoomSource):
            scope_type, source_id = ScopeType.ROOM, source.room_id
        if scope_type is not None and source_id is not None:
            scope = await self.session.scalar(
                select(AssistantScope).where(
                    AssistantScope.scope_type == scope_type,
                    AssistantScope.line_source_id == source_id,
                )
            )
            if scope is not None:
                scope.is_active = False

    async def _members_joined(self, event: MemberJoinedEvent) -> None:
        descriptor = self._multi_user_scope_descriptor(event.source)
        if descriptor is None:
            return
        scope_type, source_id = descriptor
        scope, _ = await self.identities.ensure_scope(scope_type, source_id)
        for member in event.joined.members:
            if isinstance(member, UserSource) and member.user_id:
                user = await self.identities.ensure_user(member.user_id)
                await self.identities.ensure_membership(scope, user)

    async def _members_left(self, event: MemberLeftEvent) -> None:
        descriptor = self._multi_user_scope_descriptor(event.source)
        if descriptor is None:
            return
        scope_type, source_id = descriptor
        scope = await self.session.scalar(
            select(AssistantScope).where(
                AssistantScope.scope_type == scope_type,
                AssistantScope.line_source_id == source_id,
            )
        )
        if scope is None:
            return
        for member in event.left.members:
            if not isinstance(member, UserSource) or not member.user_id:
                continue
            user = await self.identities.ensure_user(member.user_id)
            membership = await self.session.get(ScopeMembership, (scope.id, user.id))
            if membership is not None:
                membership.is_active = False
                membership.left_at = utc_now()

    @staticmethod
    def _multi_user_scope_descriptor(
        source: object,
    ) -> tuple[ScopeType, str] | None:
        if isinstance(source, GroupSource):
            return ScopeType.GROUP, source.group_id
        if isinstance(source, RoomSource):
            return ScopeType.ROOM, source.room_id
        return None

from __future__ import annotations

from typing import Any, Protocol, cast

from linebot.v3.messaging import (
    AsyncApiClient,
    AsyncMessagingApi,
    Configuration,
    FlexMessage,
    ReplyMessageRequest,
    TextMessage,
)

from line_assistant.line.messages import BotMessage


class ReplyClient(Protocol):
    async def reply(self, reply_token: str, messages: list[BotMessage]) -> None: ...

    async def close(self) -> None: ...


class LineReplyClient:
    def __init__(self, access_token: str) -> None:
        self._access_token = access_token
        self._api_client: AsyncApiClient | None = None
        self._messaging_api: AsyncMessagingApi | None = None

    def _ensure_client(self) -> AsyncMessagingApi:
        if self._messaging_api is None:
            configuration = Configuration(access_token=self._access_token)
            self._api_client = AsyncApiClient(configuration)
            self._messaging_api = AsyncMessagingApi(self._api_client)
        return self._messaging_api

    async def reply(self, reply_token: str, messages: list[BotMessage]) -> None:
        messaging_api = self._ensure_client()
        sdk_messages: list[Any] = []
        for message in messages:
            if message.get("type") == "flex":
                sdk_messages.append(FlexMessage.from_dict(message))
            else:
                sdk_messages.append(TextMessage.from_dict(message))
        await cast(
            Any,
            messaging_api.reply_message(
                ReplyMessageRequest(reply_token=reply_token, messages=sdk_messages)
            ),
        )

    async def close(self) -> None:
        if self._api_client is not None:
            await self._api_client.close()


class NullReplyClient:
    """未設定 LINE token 時，保留健康檢查但拒絕實際回覆。"""

    async def reply(self, reply_token: str, messages: list[BotMessage]) -> None:
        del reply_token, messages
        raise RuntimeError("尚未設定 LINE_CHANNEL_ACCESS_TOKEN")

    async def close(self) -> None:
        return None

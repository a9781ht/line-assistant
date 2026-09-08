from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from linebot.v3.webhook import WebhookParser
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from line_assistant.core.config import Settings, get_settings
from line_assistant.core.logging import configure_logging
from line_assistant.db.models import LineUser
from line_assistant.db.session import create_engine, create_session_factory
from line_assistant.line.client import LineReplyClient, NullReplyClient, ReplyClient
from line_assistant.line.webhook import router as line_router


def create_app(
    settings: Settings | None = None,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    reply_client: ReplyClient | None = None,
    webhook_parser: WebhookParser | None = None,
) -> FastAPI:
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.log_level)
    engine = None
    if session_factory is None:
        engine = create_engine(
            resolved_settings.database_url,
            echo=resolved_settings.app_env == "development",
        )
        session_factory = create_session_factory(engine)

    if webhook_parser is None and resolved_settings.line_channel_secret:
        webhook_parser = WebhookParser(resolved_settings.line_channel_secret)
    if reply_client is None:
        reply_client = (
            LineReplyClient(resolved_settings.line_channel_access_token)
            if resolved_settings.line_channel_access_token
            else NullReplyClient()
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        del app
        try:
            yield
        finally:
            await reply_client.close()
            if engine is not None:
                await engine.dispose()

    app = FastAPI(
        title="LINE Assistant",
        version="0.1.0",
        openapi_url=None if resolved_settings.app_env == "production" else "/openapi.json",
        docs_url=None if resolved_settings.app_env == "production" else "/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.state.session_factory = session_factory
    app.state.reply_client = reply_client
    app.state.webhook_parser = webhook_parser
    app.include_router(line_router)

    @app.get("/health/live", tags=["Health"])
    async def health_live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", tags=["Health"])
    async def health_ready(request: Request) -> dict[str, str]:
        factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
        async with factory() as session:
            await session.execute(select(LineUser.id).limit(1))
        return {"status": "ready"}

    return app


app = create_app()

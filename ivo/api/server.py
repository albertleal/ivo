"""FastAPI surface — outbound endpoint other services call.

Endpoints (live OpenAPI/Swagger at `/docs`, raw schema at `/openapi.json`):
  - GET  /health     → liveness + adapter list
  - GET  /models     → flat list of discovered models
  - POST /send       → send a Telegram message via the bot's token

Access control: by default only loopback addresses (127.0.0.1, ::1) may
call the API. Override via `api.allowed_ips` in config (empty list = open).
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from telegram import Bot

from ..bot.handlers import BotContext

log = logging.getLogger("api.server")


class SendRequest(BaseModel):
    text: str = Field(..., description="UTF-8 message body. Telegram limit: 4096 chars.")
    chat_id: int | None = Field(
        None,
        description="Target chat. Defaults to `telegram.admin_chat_id` from config.",
    )
    parse_mode: str | None = Field(
        None,
        description="Optional Telegram parse_mode: 'HTML', 'Markdown', or 'MarkdownV2'.",
    )


class SendResponse(BaseModel):
    ok: bool
    message_id: int


class ModelEntry(BaseModel):
    alias: str
    id: str
    provider: str
    display_name: str


class HealthResponse(BaseModel):
    status: str
    adapters: list[str]


def build_app(ctx: BotContext, bot: Bot | None = None) -> FastAPI:
    """Construct the FastAPI app. `bot` is injectable for tests."""
    app = FastAPI(
        title="ivo API",
        description=(
            "ivo — Intelligent Virtual Operator. Outbound HTTP surface. Other "
            "processes call this to push messages through the bot or inspect "
            "discovered models. By default the API only accepts loopback "
            "connections; widen via `api.allowed_ips`."
        ),
        version="0.1.0",
    )

    allowed_ips = list(ctx.config.api.allowed_ips or [])

    @app.middleware("http")
    async def _ip_gate(request: Request, call_next):
        if allowed_ips:
            client = request.client.host if request.client else ""
            if client not in allowed_ips:
                log.warning("api: rejecting client %s (not in allowed_ips)", client)
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={"detail": f"client {client!r} not allowed"},
                )
        return await call_next(request)

    _bot = bot or Bot(token=ctx.config.telegram.token)

    @app.get("/health", response_model=HealthResponse, tags=["meta"])
    async def health():
        """Liveness probe; reports which adapters are configured."""
        return {"status": "ok", "adapters": list(ctx.adapters)}

    @app.get("/models", response_model=list[ModelEntry], tags=["meta"])
    async def models():
        """List every discovered (alias → model) command."""
        return [
            {
                "alias": cmd.alias,
                "id": cmd.model_id,
                "provider": cmd.provider,
                "display_name": cmd.display_name,
            }
            for cmd in ctx.catalog.values()
        ]

    @app.post("/send", response_model=SendResponse, tags=["telegram"])
    async def send(req: SendRequest):
        """Send a Telegram message via the bot's token."""
        chat_id = req.chat_id or ctx.config.telegram.admin_chat_id
        if chat_id is None:
            raise HTTPException(400, "chat_id required (no admin_chat_id configured)")
        try:
            msg = await _bot.send_message(
                chat_id=chat_id,
                text=req.text,
                parse_mode=req.parse_mode,
            )
            return SendResponse(ok=True, message_id=msg.message_id)
        except Exception as e:
            log.error("send failed: %s", e)
            raise HTTPException(500, str(e)) from e

    return app

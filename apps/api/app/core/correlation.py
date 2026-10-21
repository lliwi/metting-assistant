import re
import uuid
from contextvars import ContextVar

import structlog
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

HEADER = "X-Correlation-ID"
_VALID_ID = re.compile(r"^[A-Za-z0-9._-]{8,128}$")

correlation_id_var: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def get_correlation_id() -> str | None:
    return correlation_id_var.get()


class CorrelationIdMiddleware:
    """Propaga un correlation_id por solicitud: lo acepta del cliente si es válido o lo genera."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        incoming = None
        for key, value in scope["headers"]:
            if key.decode("latin-1").lower() == HEADER.lower():
                incoming = value.decode("latin-1")
                break
        correlation_id = (
            incoming if incoming and _VALID_ID.fullmatch(incoming) else uuid.uuid4().hex
        )

        token = correlation_id_var.set(correlation_id)
        structlog.contextvars.bind_contextvars(correlation_id=correlation_id)

        async def send_with_header(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[HEADER] = correlation_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_header)
        finally:
            structlog.contextvars.unbind_contextvars("correlation_id")
            correlation_id_var.reset(token)

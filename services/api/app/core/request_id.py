"""Request-ID middleware.

Honors an inbound ``X-Request-ID`` header (so clients / load balancers can
propagate their own trace) and otherwise generates a fresh UUID4. The id is
attached to ``request.state.request_id`` for exception handlers, and bound to
``structlog.contextvars`` so every log line emitted while handling the request
carries it.
"""
from __future__ import annotations

import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

_HEADER = "x-request-id"


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Read or generate a per-request id; expose it to handlers and logs."""

    def __init__(self, app: ASGIApp, header_name: str = _HEADER) -> None:
        super().__init__(app)
        self._header = header_name.lower().encode("latin-1")

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        inbound = request.headers.get(self._header.decode("latin-1"))
        request_id = inbound if inbound else str(uuid.uuid4())
        request.state.request_id = request_id

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        response: Response = await call_next(request)
        response.headers[self._header.decode("latin-1")] = request_id
        return response


__all__ = ["RequestIdMiddleware"]

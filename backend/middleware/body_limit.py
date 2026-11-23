"""Refuse an oversized or unsized request body before anything reads it.

Starlette parses (and spools to disk) a whole multipart body before any
dependency runs, so a route's own limits, and even authentication, come too
late to stop a huge upload. For the paths listed here, this middleware looks
only at the headers: no Content-Length (a chunked body) is 411, and a
Content-Length over the path's limit is 413. The body is never read.
"""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass

from starlette.types import ASGIApp, Receive, Scope, Send


@dataclass(frozen=True)
class BodyLimit:
    method: str
    path: re.Pattern[str]
    # Read on every request, so the limit follows the owning module's value.
    max_bytes: Callable[[], int]


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp, limits: list[BodyLimit]) -> None:
        self.app = app
        self.limits = limits

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            limit = next((lim for lim in self.limits if lim.method == scope["method"]
                          and lim.path.fullmatch(scope["path"])), None)
            if limit is not None:
                length = dict(scope["headers"]).get(b"content-length")
                if length is None or not length.isdigit():
                    await _refuse(send, 411, "length_required",
                                  "Send the upload with a Content-Length.")
                    return
                if int(length) > limit.max_bytes():
                    await _refuse(send, 413, "file_too_large", "The upload is too large.")
                    return
        await self.app(scope, receive, send)


async def _refuse(send: Send, status: int, code: str, message: str) -> None:
    body = json.dumps({"detail": {"code": code, "message": message}}).encode()
    await send({"type": "http.response.start", "status": status, "headers": [
        (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
        (b"connection", b"close")]})
    await send({"type": "http.response.body", "body": body})

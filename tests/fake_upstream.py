"""A fake 7shifts/Deputy upstream for end-to-end importer tests (#126).

The importers build `httpx.AsyncClient(timeout=30.0)` inside each handler
from a module-level `import httpx`. Replacing that module reference with a
namespace whose `AsyncClient` mounts an `httpx.MockTransport` sends every
upstream call to `routes` instead of the network, while the real router,
database and response model all run.
"""

import types
from typing import Any

import httpx


def install_fake_upstream(monkeypatch, routes: dict[str, Any]) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        answer = routes.get(request.url.path)
        if answer is None:
            return httpx.Response(404, json={"error": f"no fake for {request.url.path}"})
        if isinstance(answer, httpx.Response):
            return answer
        if callable(answer):
            return answer(request)
        return httpx.Response(200, json=answer)

    real_client = httpx.AsyncClient
    fake = types.SimpleNamespace(
        AsyncClient=lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
        RequestError=httpx.RequestError,
    )
    monkeypatch.setattr("backend.routers.import_7shifts.httpx", fake)
    monkeypatch.setattr("backend.routers.import_deputy.httpx", fake)
    return seen

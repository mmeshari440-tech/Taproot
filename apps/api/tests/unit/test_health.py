from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from taproot import __version__
from taproot.main import create_app


async def test_health_returns_status_and_version() -> None:
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"status": "ok", "version": __version__}

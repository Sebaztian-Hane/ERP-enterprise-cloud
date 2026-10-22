import asyncio
from threading import Event

from app import main


class _ThreadProbe:
    def __init__(self) -> None:
        self.joined_with: float | None = None

    def join(self, timeout: float | None = None) -> None:
        self.joined_with = timeout


def test_lifespan_starts_and_stops_embedded_outbox_when_rag_is_configured(monkeypatch):
    stop_event = Event()
    thread = _ThreadProbe()
    calls = []

    monkeypatch.setattr(main.settings, "rag_sync_webhook_url", "https://n8n.example.test/webhook")
    monkeypatch.setattr(main.settings, "rag_sync_webhook_secret", "test-secret")
    monkeypatch.setattr(main.settings, "outbox_embedded_worker_enabled", True)
    monkeypatch.setattr(
        "app.workers.outbox.start_embedded_worker",
        lambda: (calls.append(True) or (stop_event, thread)),
    )

    async def exercise() -> None:
        async with main.lifespan(main.app):
            assert calls == [True]
            assert not stop_event.is_set()

    asyncio.run(exercise())

    assert stop_event.is_set()
    assert thread.joined_with == 5

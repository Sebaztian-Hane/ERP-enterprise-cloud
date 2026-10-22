from threading import Event

from app.workers.outbox import retry_delay_seconds, run_until_stopped


def test_outbox_retry_delay_is_exponential_and_bounded():
    assert retry_delay_seconds(1) == 2
    assert retry_delay_seconds(2) == 4
    assert retry_delay_seconds(20) == 300


def test_embedded_loop_stops_without_polling_when_already_stopped(monkeypatch):
    stop_event = Event()
    stop_event.set()
    monkeypatch.setattr("app.workers.outbox.process_batch", lambda: (_ for _ in ()).throw(AssertionError()))

    run_until_stopped(stop_event, worker_name="test")

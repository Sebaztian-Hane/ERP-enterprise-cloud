from app.workers.outbox import retry_delay_seconds


def test_outbox_retry_delay_is_exponential_and_bounded():
    assert retry_delay_seconds(1) == 2
    assert retry_delay_seconds(2) == 4
    assert retry_delay_seconds(20) == 300

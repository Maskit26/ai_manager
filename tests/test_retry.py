import pytest

from ai_seller_core.jobs.retry import retry_delay_seconds


def test_retry_delay_is_exponential_and_capped() -> None:
    assert 0.8 <= retry_delay_seconds(1, base=1) <= 1.2
    assert 7.2 <= retry_delay_seconds(4, base=1) <= 10.8
    assert retry_delay_seconds(20, base=1, maximum=10) <= 10


def test_retry_delay_rejects_non_positive_attempt() -> None:
    with pytest.raises(ValueError):
        retry_delay_seconds(0)

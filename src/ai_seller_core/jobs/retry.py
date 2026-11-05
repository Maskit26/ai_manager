import random


def retry_delay_seconds(attempt: int, *, base: float = 1.0, maximum: float = 300.0) -> float:
    if attempt < 1:
        raise ValueError("attempt must be greater than zero")
    delay = base * (2 ** min(attempt - 1, 16))
    return min(maximum, delay * random.uniform(0.8, 1.2))

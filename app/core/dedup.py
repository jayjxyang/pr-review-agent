from functools import lru_cache
import redis
from app.core.config import get_settings

_DEDUP_TTL = 3600  # seconds — matches GitHub's webhook re-delivery window


@lru_cache
def _get_redis() -> redis.Redis:
    return redis.from_url(get_settings().redis_url, decode_responses=True)


def is_duplicate_delivery(delivery_id: str) -> bool:
    """Return True if this X-GitHub-Delivery ID was already processed.

    Uses SET NX (atomic) so concurrent workers cannot both process the same event.
    """
    key = f"webhook:delivery:{delivery_id}"
    # set() with nx=True returns True on success (key created), None if key existed
    was_new = _get_redis().set(key, "1", ex=_DEDUP_TTL, nx=True)
    return was_new is None

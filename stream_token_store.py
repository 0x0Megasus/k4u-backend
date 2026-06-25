import secrets
import time
import threading
from dataclasses import dataclass


@dataclass
class StreamInfo:
    url: str
    user_agent: str | None
    referer: str | None
    headers: dict | None
    created_at: float


class StreamTokenStore:
    """In-memory token store for stream URLs.

    Maps short-lived random tokens to full stream source info (URL, headers, etc.)
    so the frontend never sees the actual upstream CDN URL.
    Tokens expire after TTL seconds (default 2 hours).
    """

    def __init__(self, ttl_seconds: int = 7200) -> None:
        self._store: dict[str, StreamInfo] = {}
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._cleanup_active = True
        self._start_cleanup()

    def create_token(
        self,
        url: str,
        user_agent: str | None = None,
        referer: str | None = None,
        headers: dict | None = None,
    ) -> str:
        token = secrets.token_urlsafe(16)
        with self._lock:
            self._store[token] = StreamInfo(
                url=url,
                user_agent=user_agent,
                referer=referer,
                headers=headers,
                created_at=time.time(),
            )
        return token

    def get(self, token: str) -> StreamInfo | None:
        with self._lock:
            info = self._store.get(token)
            if info is None:
                return None
            if (time.time() - info.created_at) < self._ttl:
                return info
            # Expired — remove and return None
            del self._store[token]
            return None

    def _cleanup_loop(self) -> None:
        """Remove expired tokens every 5 minutes."""
        while self._cleanup_active:
            now = time.time()
            with self._lock:
                expired = [
                    k
                    for k, v in self._store.items()
                    if now - v.created_at >= self._ttl
                ]
                for k in expired:
                    del self._store[k]
            time.sleep(300)

    def _start_cleanup(self) -> None:
        t = threading.Thread(target=self._cleanup_loop, daemon=True)
        t.start()

    def stop(self) -> None:
        self._cleanup_active = False


# Singleton — shared across routes and proxy
stream_tokens = StreamTokenStore()

import threading
import time
from duck_client import DuckClient


class TokenPool:
    """Background token harvester + pool."""

    def __init__(self, target: int = 70, ttl: int = 1200, interval: int = 10):
        self.target = target
        self.ttl = ttl
        self.interval = interval
        self._tokens = []           # [(vqd, harvested_at)]
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.stats = {"harvested": 0, "failed": 0}
        self._thread = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.is_set():
            self._trim()
            with self._lock:
                need = self.target - len(self._tokens)
            if need > 0:
                for _ in range(min(need, 5)):
                    try:
                        c = DuckClient()
                        vqd = c.fetch_token()
                        with self._lock:
                            self._tokens.append((vqd, time.time()))
                            self.stats["harvested"] += 1
                    except Exception:
                        self.stats["failed"] += 1
            time.sleep(self.interval)

    def _trim(self):
        now = time.time()
        with self._lock:
            self._tokens = [
                (t, ts) for (t, ts) in self._tokens if now - ts < self.ttl
            ]

    def acquire(self) -> str:
        self._trim()
        with self._lock:
            if self._tokens:
                t, _ = self._tokens.pop(0)
                return t
        # fallback: direct fetch
        return DuckClient().fetch_token()

    def status(self):
        self._trim()
        with self._lock:
            return {
                "size": len(self._tokens),
                "ttl": self.ttl,
                "target": self.target,
                "stats": dict(self.stats),
            }


pool = TokenPool()

import threading
import time
from collections import deque

# 連続リクエストの制限（#35）。AIの料金の使いすぎと、Gemini の利用上限への到達を防ぐ
# 教室では全員が同じIPアドレスになりやすく、デプロイ先ではプロキシ経由で正しいIPが取れないこともあるため、
# IPアドレスではなく「会話ごと」と「アプリ全体」の2つで数える


class RateLimiter:
    # window 秒の間に limit 回まで。キー（会話IDなど）ごとに数える
    def __init__(self, limit: int, window: float, clock=time.monotonic):
        self.limit = limit
        self.window = window
        self.clock = clock
        self._hits = {}
        self._lock = threading.Lock()

    def allow(self, key="all") -> bool:
        # 上限内なら数えて True。上限を超えていたら数えずに False
        now = self.clock()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= now - self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            # 古いキーがたまり続けないように、ときどき空のキーを消す
            if len(self._hits) > 1000:
                for k in [k for k, v in self._hits.items() if not v or v[-1] <= now - self.window]:
                    del self._hits[k]
            return True

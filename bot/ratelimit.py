"""限流：滑动窗口计数，按用户 + 全局双重限制。"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Deque, Dict, Tuple


class RateLimiter:
    def __init__(
        self,
        per_key: int = 5,
        window: float = 60.0,
        global_limit: int = 60,
        enabled: bool = True,
    ) -> None:
        self.per_key = max(1, per_key)
        self.window = max(1.0, window)
        self.global_limit = max(1, global_limit)
        self.enabled = enabled
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)
        self._global: Deque[float] = deque()
        self._notify_at: Dict[str, float] = {}

    def _prune(self, now: float) -> None:
        deadline = now - self.window
        for key in list(self._hits.keys()):
            dq = self._hits[key]
            while dq and dq[0] < deadline:
                dq.popleft()
            if not dq:
                self._hits.pop(key, None)
        while self._global and self._global[0] < deadline:
            self._global.popleft()

    def check(self, key: str) -> Tuple[bool, float]:
        """返回 (是否放行, 建议等待秒数)。放行时会记一次计数。"""
        if not self.enabled:
            return True, 0.0

        now = time.time()
        self._prune(now)

        dq = self._hits[key]
        if len(dq) >= self.per_key:
            return False, max(0.0, self.window - (now - dq[0]))
        if len(self._global) >= self.global_limit:
            return False, max(0.0, self.window - (now - self._global[0]))

        dq.append(now)
        self._global.append(now)
        return True, 0.0

    def should_notify(self, key: str, cooldown: float = 30.0) -> bool:
        """限流提示本身也要限流，避免刷屏。"""
        now = time.time()
        last = self._notify_at.get(key, 0.0)
        if now - last < cooldown:
            return False
        self._notify_at[key] = now
        return True

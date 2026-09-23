"""会话记忆：短期多轮上下文。

* 按 session_key 隔离（群 / 群内单人 / 私聊）；
* 按轮数与存活时间自动裁剪，避免 token 膨胀；
* 每个会话一把 asyncio.Lock，保证同一会话内消息按顺序处理；
* LRU 淘汰长时间不活跃的会话，防止内存泄漏。
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import Deque, Dict, List, Optional


@dataclass
class Turn:
    role: str  # user | assistant
    content: str
    ts: float
    message_id: Optional[str] = None


class ConversationStore:
    def __init__(
        self,
        max_turns: int = 12,
        ttl: float = 3600.0,
        max_sessions: int = 500,
    ) -> None:
        self.max_turns = max(1, max_turns)
        self.ttl = max(0.0, ttl)
        self.max_sessions = max(1, max_sessions)
        self._data: "OrderedDict[str, Deque[Turn]]" = OrderedDict()
        self._locks: Dict[str, asyncio.Lock] = {}
        self._index: Dict[str, str] = {}  # message_id -> session_key
        self._guard = asyncio.Lock()

    # ------------------------------------------------------------------
    def lock(self, key: str) -> asyncio.Lock:
        """取得（或创建）某个会话的互斥锁，保证串行处理。"""
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    def _touch(self, key: str) -> Deque[Turn]:
        turns = self._data.get(key)
        if turns is None:
            turns = deque()
            self._data[key] = turns
        else:
            self._data.move_to_end(key)
        while len(self._data) > self.max_sessions:
            old_key, _ = self._data.popitem(last=False)
            self._locks.pop(old_key, None)
        return turns

    # ------------------------------------------------------------------
    def append(
        self, key: str, role: str, content: str, message_id: Optional[str] = None
    ) -> None:
        turns = self._touch(key)
        turns.append(Turn(role=role, content=content, ts=time.time(), message_id=message_id))
        if message_id:
            self._index[str(message_id)] = key
        self._trim(turns)

    def _trim(self, turns: Deque[Turn]) -> None:
        # 1) 按轮数裁剪：只保留最近 max_turns 轮问答
        max_items = self.max_turns * 2
        while len(turns) > max_items:
            dropped = turns.popleft()
            if dropped.message_id:
                self._index.pop(str(dropped.message_id), None)
        # 2) 按 TTL 裁剪：丢掉过期的历史
        if self.ttl > 0 and turns:
            deadline = time.time() - self.ttl
            while turns and turns[0].ts < deadline:
                dropped = turns.popleft()
                if dropped.message_id:
                    self._index.pop(str(dropped.message_id), None)
        # 3) 历史必须以 user 开头（部分接口不接受 assistant 打头）
        while turns and turns[0].role != "user":
            dropped = turns.popleft()
            if dropped.message_id:
                self._index.pop(str(dropped.message_id), None)

    # ------------------------------------------------------------------
    def messages(self, key: str) -> List[Dict[str, str]]:
        """返回可直接喂给 Chat API 的历史消息（不含 system）。"""
        turns = self._data.get(key)
        if not turns:
            return []
        self._trim(turns)
        return [{"role": t.role, "content": t.content} for t in turns]

    def history_len(self, key: str) -> int:
        return len(self._data.get(key) or ())

    def reset(self, key: str) -> int:
        removed = len(self._data.pop(key, ()) or ())
        for mid, session in list(self._index.items()):
            if session == key:
                self._index.pop(mid, None)
        return removed

    def index_message(self, message_id: object, key: str) -> None:
        """登记一条消息所属的会话，供撤回时定位。"""
        if message_id is None:
            return
        self._index[str(message_id)] = key

    def forget_message(self, message_id: object) -> bool:
        """撤回消息时把对应的一轮对话从记忆里删掉。"""
        key = self._index.pop(str(message_id), None)
        if key is None:
            return False
        turns = self._data.get(key)
        if not turns:
            return False
        kept = deque(t for t in turns if str(t.message_id) != str(message_id))
        self._data[key] = kept
        return len(kept) != len(turns)

    def stats(self) -> Dict[str, int]:
        return {
            "sessions": len(self._data),
            "turns": sum(len(v) for v in self._data.values()),
            "locks": len(self._locks),
        }

    def purge_expired(self) -> int:
        """清理所有已过期会话，返回清掉的会话数。"""
        if self.ttl <= 0:
            return 0
        deadline = time.time() - self.ttl
        removed = 0
        for key in list(self._data.keys()):
            turns = self._data[key]
            if not turns or turns[-1].ts < deadline:
                self.reset(key)
                self._locks.pop(key, None)
                removed += 1
        return removed

# -*- coding: utf-8 -*-
"""GraphQL N+1 的数量模型 + DataLoader 的两条硬约束（Python 侧，同步可断言）。

口径来源（实读 raw.githubusercontent.com/graphql/dataloader/main/README.md）：
  * "Batching is not an advanced feature, it's DataLoader's primary feature."
  * 默认把"单个执行帧（one tick）"内的 load 合并；手动调度器（batchScheduleFn: schedule/dispatch）
    把合并窗口显式化 —— 本 demo 用手动 dispatch，保证同步、可断言。
  * batchLoadFn 两条硬约束：
      - "The Array of values must be the same length as the Array of keys."
      - "Each index in the Array of values must correspond to the same index in Array of keys."
  * 缓存是 per-request memoization，"does not replace Redis, Memcache"；
    典型用法是 "DataLoader instances are created when a Request begins"。
  * cache: false 时 keys 会含重复；maxBatchSize 默认 Infinity。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Sequence

# --------------------------------------------------------------------------
# 假后端：记录每一次批量调用，故意乱序返回、并缺一些 key
# --------------------------------------------------------------------------
USERS = {1: "Alice", 2: "Bob", 3: "Carol", 4: "Dan", 5: "Eve"}
COMPANIES = {"acme": "Acme Corp", "globex": "Globex", "initech": "Initech"}
POST_AUTHORS = [1, 2, 3, 4, 5, 1, 2, 3, 4, 5]
AUTHOR_COMPANY = {1: "acme", 2: "globex", 3: "acme", 4: "initech", 5: "globex"}


class FakeDB:
    """记录后端往返次数：calls 的每一项是一次真实的批量查询。"""

    def __init__(self) -> None:
        self.calls: List[List[Any]] = []

    # -- 用户表：后端按自己的顺序返回，且缺 key 时不返回对应行 -----------------
    def fetch_users(self, keys: Sequence[int]) -> List[Optional[str]]:
        self.calls.append(list(keys))
        found = {k: USERS[k] for k in sorted(USERS) if k in keys}
        # 官方 README 的例子：后端顺序与请求顺序不同，缺值要补 null/Error
        return [found.get(k) for k in keys]

    def fetch_companies(self, keys: Sequence[str]) -> List[Optional[str]]:
        self.calls.append(list(keys))
        found = {k: COMPANIES[k] for k in sorted(COMPANIES) if k in keys}
        return [found.get(k) for k in keys]

    def fetch_posts(self, n: int) -> List[Dict[str, Any]]:
        self.calls.append(["posts:%d" % n])
        return [{"id": i, "author_id": POST_AUTHORS[i % len(POST_AUTHORS)]}
                for i in range(n)]

    @property
    def round_trips(self) -> int:
        return len(self.calls)


# --------------------------------------------------------------------------
# 手动 dispatch 的批处理 + memoization
# --------------------------------------------------------------------------
class Pending:
    """load() 的返回值占位，dispatch() 之后才有值。"""

    __slots__ = ("key", "value", "resolved")

    def __init__(self, key: Any) -> None:
        self.key = key
        self.value: Any = None
        self.resolved = False


class BatchLoader:
    """对应官方 README 里 "manually dispatched batch scheduler" 的写法。"""

    def __init__(self, batch_fn: Callable[[List[Any]], List[Any]],
                 max_batch_size: Optional[int] = None) -> None:
        self._fn = batch_fn
        self.max_batch_size = max_batch_size
        self.queue: List[Pending] = []
        self.cache: Dict[Any, Any] = {}
        # 与官方一致：load() 时就登记，因此**同一批次窗口内**的重复 key 也只进队列一次
        self.inflight: Dict[Any, Pending] = {}
        self.batches: List[List[Any]] = []

    def load(self, key: Any) -> Any:
        if key in self.cache:
            # 命中 memoization：不进批次，也不再产生后端调用
            return self.cache[key]
        if key in self.inflight:
            return self.inflight[key]
        p = Pending(key)
        self.queue.append(p)
        self.inflight[key] = p
        return p

    def dispatch(self) -> None:
        while self.queue:
            size = len(self.queue) if self.max_batch_size is None \
                else max(1, self.max_batch_size)
            batch, self.queue = self.queue[:size], self.queue[size:]
            keys = [p.key for p in batch]
            values = self._fn(keys)
            if not isinstance(values, list) or len(values) != len(keys):
                raise ValueError(
                    "batchLoadFn must return an Array of the same length "
                    "as the Array of keys: got %r for %r" % (values, keys))
            for p, v in zip(batch, values):
                p.value = v
                p.resolved = True
                self.cache[p.key] = v
                self.inflight.pop(p.key, None)
            self.batches.append(keys)


def value(x: Any) -> Any:
    """Pending 与已缓存值统一取值。"""
    return x.value if isinstance(x, Pending) else x


# --------------------------------------------------------------------------
# 两种解析方式：朴素 resolver vs DataLoader
# --------------------------------------------------------------------------
def naive_posts_authors(db: FakeDB, n: int) -> List[Dict[str, Any]]:
    """朴素 resolver：每篇 post 各自查一次作者 → 1 + N。"""
    out = []
    for post in db.fetch_posts(n):
        author = db.fetch_users([post["author_id"]])[0]
        out.append({"post": post["id"], "author": author})
    return out


def loader_posts_authors(db: FakeDB, n: int,
                         max_batch_size: Optional[int] = None) -> List[Dict[str, Any]]:
    """DataLoader：同一层的 load 合并成一次批量查询 → 1 + 1（或按 max_batch_size 分片）。"""
    loader = BatchLoader(db.fetch_users, max_batch_size=max_batch_size)
    posts = db.fetch_posts(n)
    pending = [(p, loader.load(p["author_id"])) for p in posts]
    loader.dispatch()
    return [{"post": p["id"], "author": value(a)} for p, a in pending]


def naive_three_level(db: FakeDB, n: int) -> List[Dict[str, Any]]:
    """三层：posts → authors → companies，朴素写法 1 + N + N。"""
    out = []
    for post in db.fetch_posts(n):
        author = db.fetch_users([post["author_id"]])[0]
        company = db.fetch_companies([AUTHOR_COMPANY[post["author_id"]]])[0]
        out.append({"post": post["id"], "author": author, "company": company})
    return out


def loader_three_level(db: FakeDB, n: int) -> List[Dict[str, Any]]:
    """三层 + DataLoader：每层一次批量 → 1 + 1 + 1。"""
    uloader = BatchLoader(db.fetch_users)
    cloader = BatchLoader(db.fetch_companies)
    posts = db.fetch_posts(n)
    pend_u = [(p, uloader.load(p["author_id"])) for p in posts]
    uloader.dispatch()
    pend_c = [(p, cloader.load(AUTHOR_COMPANY[p["author_id"]])) for p, _ in pend_u]
    cloader.dispatch()
    return [{"post": p["id"], "author": value(a), "company": value(c)}
            for (p, a), (_, c) in zip(pend_u, pend_c)]

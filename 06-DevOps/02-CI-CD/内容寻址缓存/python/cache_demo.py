"""内容寻址缓存 — hashFiles 键 + restore-keys 回退 + LRU/TTL 淘汰.

模拟 actions/cache 的核心语义:
  demo 1 精确命中跳过构建      demo 2 lockfile 变更 + restore-keys 部分回退
  demo 3 容量 LRU 淘汰 + 一周 TTL
"""
from __future__ import annotations

import hashlib

CAPACITY = 3          # GitHub 上限是单仓库 5 GB,demo 用条目数近似
TTL_MINUTES = 10080    # 一周 = 7 * 24 * 60 分钟,一周未访问即淘汰


class Entry:
    __slots__ = ("data", "last_access")

    def __init__(self, data: str, last_access: int):
        self.data = data
        self.last_access = last_access


class CICache:
    """最小 CI 缓存:条目表 + 逻辑时钟(last_access 近似"最近访问")。"""

    def __init__(self, capacity: int):
        self.capacity = capacity
        self.entries: dict[str, Entry] = {}
        self.clock = 0          # 逻辑时钟:每次 lookup/save 前进 1 分钟

    def _tick(self) -> int:
        self.clock += 1
        return self.clock

    def key_for(self, namespace: str, lockfile: str) -> str:
        """hashFiles 语义:锁文件内容 SHA-256 => key(内容寻址)。"""
        digest = hashlib.sha256(lockfile.encode()).hexdigest()
        return f"{namespace}-{digest}"

    def lookup(self, key: str,
               restore_keys: list[str]) -> tuple[str, str | None]:
        """官方查找次序:精确命中 -> restore-keys 前缀(最近访问)-> miss。"""
        self._evict_expired()
        if key in self.entries:                          # 1. 精确匹配
            e = self.entries[key]
            e.last_access = self._tick()
            return "exact", e.data
        for prefix in restore_keys:                       # 3. 前缀回退
            cands = [(e.last_access, k) for k, e in self.entries.items()
                     if k.startswith(prefix)]
            if cands:
                best = max(cands)[1]                       # 最近访问
                e = self.entries[best]
                e.last_access = self._tick()
                return "partial", e.data
        return "miss", None                               # 4. 全部未命中

    def save(self, key: str, data: str) -> None:
        """job 成功后保存;超过容量按 LRU 淘汰(最近最少访问先走)。"""
        self._evict_expired()
        while len(self.entries) >= self.capacity:
            lru = min(self.entries,
                      key=lambda k: self.entries[k].last_access)
            evicted = self.entries.pop(lru)
            print(f"    [evict] LRU: {lru[:24]}... "
                  f"(data='{evicted.data[:20]}...')")
        self.entries[key] = Entry(data, self._tick())

    def _evict_expired(self) -> None:
        """一周未访问的条目直接淘汰(TTL)。"""
        for k in [k for k, e in self.entries.items()
                  if self.clock - e.last_access > TTL_MINUTES]:
            print(f"    [evict] TTL(>7d unused): {k[:24]}...")
            del self.entries[k]

    def keys(self) -> list[str]:
        return sorted(self.entries)


def fake_npm_install(lockfile: str) -> str:
    """模拟 npm install:产物内容取决于锁文件列出的依赖。"""
    pkgs = []
    if "express" in lockfile:
        pkgs.append("express@4.18.0")
    if "lodash" in lockfile:
        pkgs.append("lodash@4.17.21")
    return "node_modules[" + ", ".join(pkgs) + "]"


def demo_hit_miss() -> None:
    print("== demo 1: 精确命中 -> 跳过构建 ==")
    cache = CICache(CAPACITY)
    lock_v1 = '{"packages":{"node_modules/express":{"version":"4.18.0"}}}'
    key = cache.key_for("npm", lock_v1)
    print(f"  lockfile v1 -> key {key[:28]}...")

    kind, data = cache.lookup(key, ["npm-"])
    print(f"  run #1: {kind.upper()} -> run npm install (120 s)")
    cache.save(key, fake_npm_install(lock_v1))
    print(f"         saved '{cache.entries[key].data}'")

    kind, data = cache.lookup(key, ["npm-"])
    print(f"  run #2: {kind.upper()} -> skip install, restore"
          f" '{data}' (5 s)")


def demo_restore_keys() -> None:
    print("== demo 2: lockfile 变更 + restore-keys 部分回退 ==")
    cache = CICache(CAPACITY)
    lock_v1 = '{"packages":{"node_modules/express":{"version":"4.18.0"}}}'
    lock_v2 = ('{"packages":{"node_modules/express":{"version":"4.18.0"},'
               '"node_modules/lodash":{"version":"4.17.21"}}}')
    k1, k2 = cache.key_for("npm", lock_v1), cache.key_for("npm", lock_v2)
    cache.save(k1, fake_npm_install(lock_v1))
    print(f"  cache has v1 entry; v2 lockfile -> new key {k2[:24]}...")

    kind, data = cache.lookup(k2, ["npm-"])    # 前缀 "npm-" 命中 v1
    print(f"  run: {kind.upper()} -> restore old '{data}' as base,"
          " npm install (60 s, 增量)")
    cache.save(k2, fake_npm_install(lock_v2))
    print(f"       saved new '{cache.entries[k2].data}'")


def demo_eviction() -> None:
    print("== demo 3: 容量 LRU 淘汰 + 一周 TTL ==")
    cache = CICache(CAPACITY)
    locks = [f'lock#{i}' for i in range(4)]
    keys = [cache.key_for("npm", l) for l in locks]
    for i, k in enumerate(keys[:3]):
        cache.save(k, f"deps-v{i}")
    print(f"  saved 3 entries: {[k[:14] for k in cache.keys()]}")
    cache.lookup(keys[0], [])          # 访问 v0 => v1 成为 LRU

    print("  save v3 (capacity=3):")
    cache.save(keys[3], "deps-v3")     # 淘汰 v1(最久未访问)
    kind, _ = cache.lookup(keys[1], [])
    print(f"  lookup v1: {kind.upper()} (已被 LRU 淘汰)")
    kind, _ = cache.lookup(keys[0], [])
    print(f"  lookup v0: {kind.upper()} (刚访问过,保留)")

    # TTL: 逻辑时钟推进 8 天,再查 v3
    cache.clock += TTL_MINUTES + 1
    kind, _ = cache.lookup(keys[3], [])
    print(f"  8 天后 lookup v3: {kind.upper()} (一周未访问被 TTL 淘汰)")


if __name__ == "__main__":
    demo_hit_miss()
    print()
    demo_restore_keys()
    print()
    demo_eviction()

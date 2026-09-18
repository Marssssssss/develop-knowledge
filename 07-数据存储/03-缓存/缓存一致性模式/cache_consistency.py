"""缓存一致性模式：写策略的竞态枚举 + Facebook lease 机制。

权威依据：
  - Microsoft Learn《Cache-Aside pattern》—— 明确要求「先更新数据存储、再删缓存」，
    并给出反例（先删缓存会让并发读把旧值回填）。
  - NSDI'13《Scaling Memcache at Facebook》§3.2.1 —— lease（64-bit token、
    每 key 每 10 秒只发一个）、stale set / thundering herd、17K/s → 1.3K/s。
"""
import itertools

# ------------------------------------------------------------ 竞态枚举

READER = ["R_DBGET", "R_CACHESET"]   # cache-aside 读（未命中）：读库 → 回填

# 四种写策略，每种是一个有序步骤序列
STRATEGIES = {
    # 先删缓存、再更新库（Azure 文档明确反对的顺序）
    "del_then_db": ["W_DEL", "W_DBSET"],
    # 先更新库、再删缓存（Azure 文档推荐）
    "db_then_del": ["W_DBSET", "W_DEL"],
    # 延迟双删：更新库 → 删 → 再删一次
    "delayed_double_del": ["W_DBSET", "W_DEL", "W_DEL"],
    # 写穿透：库与缓存在「同一个写操作」内更新（建模为单个原子步）
    "write_through": ["W_ATOMIC_BOTH"],
}


def interleave(a, b):
    """枚举所有保持各自内部顺序的合并序列（交错执行的所有可能）。"""
    out = []
    def rec(i, j, acc):
        if i == len(a) and j == len(b):
            out.append(tuple(acc))
            return
        if i < len(a):
            rec(i + 1, j, acc + [a[i]])
        if j < len(b):
            rec(i, j + 1, acc + [b[j]])
    rec(0, 0, [])
    return out


def simulate(seq):
    """按给定交错序执行，返回 (db, cache, consistent)。

    一致 = 缓存里要么没有该 key，要么值等于库里的值。
    """
    db = {"k": "v1"}
    cache = {"k": "v1"}
    read = {"v": None}
    for st in seq:
        if st == "W_DEL":
            cache.pop("k", None)
        elif st == "W_DBSET":
            db["k"] = "v2"
        elif st == "W_ATOMIC_BOTH":
            db["k"] = "v2"
            cache["k"] = "v2"
        elif st == "R_DBGET":
            read["v"] = db["k"]
        elif st == "R_CACHESET":
            cache["k"] = read["v"]
    consistent = ("k" not in cache) or cache["k"] == db["k"]
    return db["k"], cache.get("k"), consistent


def stale_fraction(strategy):
    """返回 (交错总数, 产生不一致的交错数)。"""
    seqs = interleave(STRATEGIES[strategy], READER)
    bad = sum(1 for s in seqs if not simulate(s)[2])
    return len(seqs), bad


# ------------------------------------------------------------ Lease

LEASE_TTL_SECONDS = 10      # 论文：默认每 key 每 10 秒只发一个 token
STALE_VALUE_FLUSH_SECONDS = 10


class Lease:
    """论文：64-bit token，绑定到「客户端最初请求的那个 key」上。"""

    def __init__(self, key, token, epoch, issued_at):
        self.key = key
        self.token = token
        self.epoch = epoch
        self.issued_at = issued_at


class LeaseServer:
    """memcached 侧的 lease 仲裁。"""

    def __init__(self, token_source=None):
        self.data = {}          # key -> value
        self.epochs = {}        # key -> epoch（每次 delete 自增，作废全部在途 token）
        self.last_token_at = {}  # key -> 上次发 token 的时间
        self.dead = {}          # key -> 已删除但仍可作废 token 的旧值（stale value）
        self._seq = 0x9E3779B97F4A7C15  # 64-bit 起始值（仅用于产生可复现的 token）
        self._src = token_source
        self.db_reads = 0

    def _new_token(self, key, epoch, now):
        self._seq = (self._seq * 6364136223846793005 + 1442695040888963407) & 0xFFFFFFFFFFFFFFFF
        return Lease(key, self._seq, epoch, now)

    def get(self, key, now, accept_stale=False):
        """返回 (状态, 值或 token 或 None)。状态 ∈ hit/miss/wait/stale。

        accept_stale=True 表示「这个应用能用稍旧的数据继续推进」——
        论文：这类应用拿走 stale 值，完全不必等最新值从库里取回来。
        注意 stale 分支不消耗 token，所以不受 10 秒限流影响。
        """
        if key in self.data:
            return ("hit", self.data[key])
        if accept_stale and key in self.dead:
            # 论文：删除后值转入一个「最近删除项」结构，可返回标记为 stale 的值
            return ("stale", self.dead[key])
        last = self.last_token_at.get(key)
        if last is not None and now - last < LEASE_TTL_SECONDS:
            # 论文：10 秒内的请求收到「稍等」通知，而不是再发一个 token
            return ("wait", None)
        epoch = self.epochs.get(key, 0)
        self.last_token_at[key] = now
        self.db_reads += 1
        return ("miss", self._new_token(key, epoch, now))

    def set_with_lease(self, key, value, lease, now):
        """带 token 回写。token 已被 delete 作废则拒绝 —— 这就是防 stale set。"""
        if lease.key != key:
            return False
        return self._accept(key, value, lease.epoch, now, require=True)

    def set_without_lease(self, key, value, now):
        """不带 token 的普通 set（无仲裁，会产生 stale set）。"""
        return self._accept(key, value, self.epochs.get(key, 0), now, require=False)

    def _accept(self, key, value, epoch, now, require):
        if require and epoch != self.epochs.get(key, 0):
            return False
        self.data[key] = value
        self.dead.pop(key, None)
        return True

    def delete(self, key, now):
        """删除会让该 key 上所有在途 token 失效（epoch 自增）。"""
        if key in self.data:
            self.dead[key] = self.data.pop(key)  # 论文：转存为 stale value
        self.epochs[key] = self.epochs.get(key, 0) + 1


def thundering_herd(with_lease, n_clients=100, t0=1000):
    """模拟 n 个客户端同时读一个冷 key。

    返回 (打到数据库的次数, 收到「稍等」的客户端数, 重试后命中的客户端数)。
    """
    srv = LeaseServer()              # key "hot" 从未写入 → 冷启动
    db_reads = 0
    waits = 0
    token = None
    for _ in range(n_clients):
        st, payload = srv.get("hot", t0)
        if st == "miss":
            db_reads += 1
            token = payload
        elif st == "wait":
            waits += 1
            if not with_lease:
                db_reads += 1       # 没有 lease 就没有「等」，每个客户端都去查库
        elif st == "hit":
            pass
    # 持有 token 的客户端回写（无 lease 时每个客户端都无仲裁地写）
    if with_lease:
        if token is not None:
            srv.set_with_lease("hot", "v2", token, t0 + 1)
    else:
        for _ in range(n_clients):
            srv.set_without_lease("hot", "v2", t0 + 1)
    # 稍等的客户端重试
    hits = 0
    for _ in range(n_clients):
        st, _p = srv.get("hot", t0 + 2)
        if st == "hit":
            hits += 1
    return db_reads, waits, hits


# ------------------------------------------------------------ 论文实测数字

PAPER_PEAK_DB_QPS_NO_LEASE = 17000
PAPER_PEAK_DB_QPS_WITH_LEASE = 1300


def paper_reduction_factor():
    return PAPER_PEAK_DB_QPS_NO_LEASE / PAPER_PEAK_DB_QPS_WITH_LEASE

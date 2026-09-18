"""缓存一致性模式自检。运行： python cache_consistency_selftest.py"""
import sys
from cache_consistency import (
    STRATEGIES, interleave, simulate, stale_fraction,
    LeaseServer, thundering_herd, paper_reduction_factor,
    LEASE_TTL_SECONDS, PAPER_PEAK_DB_QPS_NO_LEASE, PAPER_PEAK_DB_QPS_WITH_LEASE,
)

FAILS = []


def check(label, cond, detail=""):
    if cond:
        print("  ok   %s" % label)
    else:
        FAILS.append("%s %s" % (label, detail))
        print("  FAIL %s %s" % (label, detail))


print("[1] 写顺序决定不一致窗口（Azure Cache-Aside 的明确要求）")
tot, bad = stale_fraction("del_then_db")
check("先删缓存再更新库：6 种交错里 4 种留下脏缓存", (tot, bad) == (6, 4), (tot, bad))
tot, bad = stale_fraction("db_then_del")
check("先更新库再删缓存：只剩 1 种", (tot, bad) == (6, 1), (tot, bad))
tot, bad = stale_fraction("delayed_double_del")
check("延迟双删：10 种交错里 1 种（比例更低但非 0）", (tot, bad) == (10, 1), (tot, bad))
tot, bad = stale_fraction("write_through")
check("写穿透(单原子步)仍非 0：读路径不是原子的", (tot, bad) == (3, 1), (tot, bad))
check("先删缓存的风险是先更新库的 4 倍",
      stale_fraction("del_then_db")[1] / stale_fraction("del_then_db")[0]
      == 4 * stale_fraction("db_then_del")[1] / stale_fraction("db_then_del")[0])

print("[2] 具体那一条坏交错：删 → 读旧值 → 回填 → 库才更新")
db, cached, ok = simulate(("W_DEL", "R_DBGET", "R_CACHESET", "W_DBSET"))
check("缓存留下旧值 v1", cached == "v1" and db == "v2", (cached, db))
check("判定为不一致", ok is False)
db, cached, ok = simulate(("W_DEL", "W_DBSET", "R_DBGET", "R_CACHESET"))
check("写完整后读 → 一致", ok is True and cached == "v2", (cached, ok))

print("[3] Lease：64-bit token 绑定到 key")
srv = LeaseServer()
st, tok = srv.get("k", 1000)
check("冷 key 返回 miss + token", st == "miss" and tok is not None)
check("token 是 64 位", 0 <= tok.token <= 0xFFFFFFFFFFFFFFFF, tok.token)
check("token 绑定了 key", tok.key == "k")
check("回写成功", srv.set_with_lease("k", "v1", tok, 1001) is True)
check("后续读命中", srv.get("k", 1001)[0] == "hit")
check("token 绑错 key 直接拒绝",
      srv.set_with_lease("other", "x", tok, 1002) is False)

print("[4] Lease：delete 作废在途 token（防 stale set）")
srv.delete("k", 1002)
check("删除后无 lease 直读会被限流为 wait", srv.get("k", 1002)[0] == "wait")
check("带作废 token 的回写被拒绝", srv.set_with_lease("k", "OLD", tok, 1003) is False)
check("不使用 lease 的回写被接受 —— 这就是 stale set",
      srv.set_without_lease("k", "OLD", 1003) is True)
check("stale set 已污染缓存", srv.data["k"] == "OLD", srv.data)

print("[5] Stale value：能容忍旧数据的应用不必等")
srv2 = LeaseServer()
st2, tok2 = srv2.get("k", 2000)
srv2.set_with_lease("k", "v1", tok2, 2000)
srv2.delete("k", 2001)
st3, val = srv2.get("k", 2001, accept_stale=True)
check("删除后可读到 stale 值", (st3, val) == ("stale", "v1"), (st3, val))
check("stale 分支不消耗 token（不触发限流）",
      srv2.get("k", 2002, accept_stale=True)[0] == "stale")
check("限流窗口长度 = 10 秒", LEASE_TTL_SECONDS == 10)
srv3 = LeaseServer()
srv3.get("k", 3000)
check("窗口内 wait", srv3.get("k", 3009)[0] == "wait")
check("窗口外可再发 token", srv3.get("k", 3010)[0] == "miss")

print("[6] Thundering herd：惊群")
n, waits, hits = thundering_herd(with_lease=False)
check("无 lease：100 个客户端全部打到库", n == 100, n)
check("无 lease 时也有 99 个客户端本可以「稍等」", waits == 99, waits)
n2, waits2, hits2 = thundering_herd(with_lease=True)
check("有 lease：只有 1 次打到库", n2 == 1, n2)
check("有 lease：99 个客户端收到稍等", waits2 == 99, waits2)
check("重试后全部命中", hits == 100 and hits2 == 100, (hits, hits2))
check("理想情况下降低 100 倍", n / n2 == 100, n / n2)

print("[7] 论文实测数字（NSDI'13 §3.2.1）")
check("无 lease 峰值 17K/s", PAPER_PEAK_DB_QPS_NO_LEASE == 17000)
check("有 lease 峰值 1.3K/s", PAPER_PEAK_DB_QPS_WITH_LEASE == 1300)
check("降低 13.08 倍", abs(paper_reduction_factor() - 13.076923) < 1e-5,
      paper_reduction_factor())
check("实测降幅远小于理想的 100 倍（并非所有请求同时到达）",
      paper_reduction_factor() < 100)

print()
if FAILS:
    print("FAILED %d:" % len(FAILS))
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("ALL PASS")

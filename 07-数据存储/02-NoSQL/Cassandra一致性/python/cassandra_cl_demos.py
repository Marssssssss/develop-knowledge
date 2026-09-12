"""
cassandra_cl_demos.py — Cassandra 一致性级别 demo 入口

调用:
    cd ../python && python3 cassandra_cl_demos.py

权威来源:
  - DataStax archived docs Consistency / About Client Requests (链接见 cassandra_cl.py 头部)
  - let's build solutions 'How Cassandra Works'(Merkle tree + LOCAL_QUORUM 推荐)
"""
from __future__ import annotations
from cassandra_cl import (
    CL_ANY, CL_ONE, CL_QUORUM, CL_ALL,
    is_strongly_consistent, WRITE_CLS, required_acks, Cluster,
)


def explain_table():
    print("=== Cassandra 一致性级别详解 ===\n")
    print(f"{'CL':15s} {'语义':50s} {'需 ack (RF=3)':15s}")
    print("-" * 80)
    rows = [
        (CL_ANY, "任何 1 个副本(包括 hint-only)", "1"),
        (CL_ONE, "1 个 replica 即可", "1"),
        ("TWO", "2 个 replica", "2"),
        ("THREE", "3 个 replica", "3"),
        (CL_QUORUM, "多数派 = RF/2+1", "2"),
        ("LOCAL_QUORUM", "本 DC 多数派(避免跨 DC RTT)", "2"),
        ("EACH_QUORUM", "所有 DC 都达到 quorum", "(2 × dc)"),
        (CL_ALL, "全部 RF 个 replica", "3"),
    ]
    for cl, sem, need in rows:
        print(f"{cl:15s} {sem:50s} {need:15s}")


def demo_cl_decision():
    print("\n=== DEMO 1: CL 决策表(RF=3)+ 强一致性公式 ===\n")
    rf = 3
    print(f"RF={rf}, 各 CL 需 ack 数: "
          f"{', '.join(f'{c}={required_acks(c, rf)}' for c in WRITE_CLS if c not in ('LOCAL_QUORUM','EACH_QUORUM'))}")
    combos = [
        ("QUORUM/QUORUM", 2, 2, 3),
        ("ALL/ONE",       3, 1, 3),
        ("ONE/ONE",       1, 1, 3),
        ("QUORUM/ONE",    2, 1, 3),
    ]
    print(f"\n{'组合':15s} {'W':3s} {'R':3s} {'RF':3s} {'W+R':5s} {'一致性':10s}")
    print("-" * 50)
    for name, W, R, rf_ in combos:
        strong = is_strongly_consistent(W, R, rf_)
        print(f"{name:15s} {W:3d} {R:3d} {rf_:3d} {W+R:5d} {'STRONG' if strong else 'eventual':10s}")
    print("\n启示:本地独立看读写 CL 不够,必须看组合。QUORUM+QUORUM 在 RF=3 上 strongest;")


def demo_write_read_with_cl():
    print("\n=== DEMO 2: 不同 CL 在 1 副本 down 时表现 ===\n")
    cluster = Cluster(n_replicas=6, rf=3)
    cluster.replicas[1].alive = False  # n-1 down
    print(f"初始:n-1 = DOWN (其他 5 节点健康)")
    for cl in [CL_ONE, CL_QUORUM, CL_ALL, CL_ANY]:
        ok, msg, hint_n = cluster.write("user:1", f"v@CL={cl}", cl)
        print(f"  CL={cl:13s} put(user:1) → {'OK' if ok else 'FAIL'} | {msg}")
    print("\n--- 读 + read repair ---")
    values, rr, stale = cluster.read("user:1", CL_QUORUM)
    print(f"  read(CL=QUORUM) 返回 {len(values)} 个版本, 触发 {rr} 次 read-repair 补写")
    cluster.replicas[1].alive = True
    print(f"\n--- n-1 上线,replay hints ---")
    cluster.replicas[1].replay_hints()


def demo_lwt():
    print("\n=== DEMO 3: 轻量事务 LWT(Paxos 简化版,INSERT ... IF NOT EXISTS) ---")
    cluster = Cluster(n_replicas=6, rf=3)
    ok, msg = cluster.lwt_insert_if_not_exists("username:alice", "alice@x.com")
    print(f"  第 1 次尝试 → {'OK' if ok else 'FAIL'}: {msg}")
    ok, msg = cluster.lwt_insert_if_not_exists("username:alice", "alice@x.com")
    print(f"  第 2 次尝试 → {'OK' if ok else 'FAIL'}: {msg}")


def demo_anti_entropy():
    print("\n=== DEMO 4: Anti-entropy repair (Merkle 树简化版) ===")
    cluster = Cluster(n_replicas=6, rf=3)
    cluster.write("k1", "v1-old", CL_ONE)
    cluster.write("k2", "v2-old", CL_ONE)
    cluster.write("k3", "v3", CL_ONE)
    cluster.replicas[2].alive = False
    cluster.write("k1", "v1-new", CL_ONE)
    cluster.write("k2", "v2-new", CL_ONE)
    cluster.replicas[2].alive = True
    print(f"  3 节点不一致 — 比对 n-0 vs n-2 的 keys [k1,k2,k3]:")
    cluster.anti_entropy_repair(0, 2, ["k1", "k2", "k3"])


def main():
    explain_table()
    demo_cl_decision()
    demo_write_read_with_cl()
    demo_lwt()
    demo_anti_entropy()
    print("\n" + "=" * 70)
    print("Cassandra 调优 tips(节选)")
    print("=" * 70)
    print("  1. 生产写读都用 LOCAL_QUORUM 起步(多 DC 部署)")
    print("  2. 监控 read_repair_activity / write_repair_activity 计数")
    print("  3. 周期性跑 nodetool repair -pr -par <keyspace>")
    print("  4. gc_grace_seconds (默认 10d) 内必须 repair 完一次,否则 tombstone purge")
    print("  5. 增量修复(4.0+)只扫未修复过的 SSTable")
    print("  6. LWT 4-5x 慢于普通写,只在唯一性约束/库存超卖时使用")


if __name__ == "__main__":
    main()

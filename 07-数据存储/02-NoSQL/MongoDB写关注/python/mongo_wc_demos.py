"""
mongo_wc_demos.py — MongoDB 副本集 demo 入口

权威来源见 mongo_wc.py 头部。

运行: cd ../python && python3 mongo_wc_demos.py
"""
from __future__ import annotations
from mongo_wc import ReplicaSet, Member, OpLogEntry


def demo_default_wc_formula():
    print("\n=== DEMO 1: 隐式默认 writeConcern 公式 (5.0+) ===")
    cases = [(3, 0, "P-S-S"), (3, 1, "P-S-A"), (5, 1, "P-S-S-S-A"), (2, 0, "P-S")]
    for v, a, label in cases:
        print(f"  {label}: voting={v} arbiters={a} \u2192 {ReplicaSet.compute_default_wc(v, a)}")


def demo_basic_write_with_wc():
    print("\n=== DEMO 2: 写 {w:1, j:true} vs {w:majority, j:true} ===")
    rs = ReplicaSet([
        Member("n-0", "PRIMARY"),
        Member("n-1", "SECONDARY"),
        Member("n-2", "SECONDARY"),
    ])
    ok, note, ts = rs.write("db.coll", {"_id": "doc-1", "value": "hello"}, {"w": 1, "j": True})
    print(f"  write({{w:1, j:true}}) \u2192 {'OK' if ok else 'FAIL'} | {note}")
    ok, note, ts = rs.write("db.coll", {"_id": "doc-2", "value": "world"}, {"w": "majority", "j": True})
    print(f"  write({{w:'majority', j:true}}) \u2192 {'OK' if ok else 'FAIL'} | {note}")
    ok, note, ts = rs.write("db.coll", {"_id": "doc-3", "value": "v"}, {"w": 3, "wtimeout": 100})
    print(f"  write({{w:3, wtimeout:100ms}}) \u2192 {'OK' if ok else 'FAIL'} | {note}")


def demo_read_concern_5_levels():
    print("\n=== DEMO 3: readConcern 5 级语义对比 ===")
    rs = ReplicaSet([
        Member("n-0", "PRIMARY"),
        Member("n-1", "SECONDARY"),
    ])
    rs.write("db.coll", {"_id": "x", "value": "before-failover"}, {"w": "majority"})
    for rc in ["local", "available", "majority", "linearizable", "snapshot"]:
        d, msg = rs.read("db.coll", {"_id": "x"}, rc)
        print(f"  readConcern={rc:13s} \u2192 doc={d} | {msg}")


def demo_rollback():
    print("\n=== DEMO 4: failover + 回滚 (旧 primary 未复制写) ===")
    rs = ReplicaSet([
        Member("n-0", "PRIMARY"),
        Member("n-1", "SECONDARY"),
        Member("n-2", "SECONDARY"),
    ])
    lost = [{"_id": "u-1", "value": "ghost-write-1"}, {"_id": "u-2", "value": "ghost-write-2"}]
    rs.simulate_failover_and_rollback(lost)
    print("  \u2192 应用层使用 {w:majority} 可避免大部分回滚(只有 w:1 才可能)")


def demo_causal_session():
    print("\n=== DEMO 5: 因果一致性(Causal Consistency Session) ===")
    print("  客户端在同一个 session 内:")
    print("    wc = {w:'majority'}, rc = 'majority'")
    print("    驱动会自动维护 operationTime 并设置 afterClusterTime")
    print("  \u2192 读己之写(read-your-own-writes)自动满足")
    print("  \u2192 跨节点的 happens-before 关系被保留")


def main():
    demo_default_wc_formula()
    demo_basic_write_with_wc()
    demo_read_concern_5_levels()
    demo_rollback()
    demo_causal_session()
    print("\n" + "=" * 70)
    print("MongoDB 推荐配置")
    print("=" * 70)
    print("  \u2022 默认 wc={w:'majority', j:true} \u2014 防回滚")
    print("  \u2022 默认 rc='majority' \u2014 强一致读")
    print("  \u2022 应用层在 causal session 包装写读")
    print("  \u2022 避免 P-S-A 三节点 + majority 组合")
    print("  \u2022 监控 oplog window: db.getReplicationInfo()")


if __name__ == "__main__":
    main()

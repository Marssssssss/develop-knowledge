"""
dynamo_demos.py — Dynamo 风格 4 大机制的 demo 演示

调用方式:
    cd python && python3 -m dynamo_demos

或:
    python3 dynamo_demos.py
"""
from __future__ import annotations
from dynamo import (
    ConsistentHashRing, VectorClock, DynamoNode, StoredValue
)


def demo_basic():
    print("\n=== DEMO 1: 一致性哈希 + 偏好列表 ===")
    nodes = {f"node-{chr(65+i)}": 16 for i in range(6)}
    ring = ConsistentHashRing(nodes)
    for k in ["user:42:cart", "product:sku-1", "session:abc", "order:o-100"]:
        pl = ring.preference_list(k, N=3, alive=set(nodes))
        print(f"  key={k:18s} → preference_list = {pl}")


def demo_sloppy_quorum_and_hinted_handoff():
    print("\n=== DEMO 2: sloppy quorum + hinted handoff ===")
    node_names = [f"node-{chr(65+i)}" for i in range(6)]
    nodes_v = {n: 16 for n in node_names}
    ring = ConsistentHashRing(nodes_v)
    alive = set(node_names) - {"node-D"}
    pl = ring.preference_list("user:42:cart", N=3, alive=alive)
    print(f"  user:42:cart preference_list(alive⊆{alive}) = {pl}")
    print("  → Dynamo 写 W=2: 发前 2 个健康节点;若前 2 里也有 down,则发下一个健康+hint")


def demo_vector_clock_and_reconcile():
    print("\n=== DEMO 3: 向量时钟 + 读时协调 ===")
    vc_a1 = VectorClock().increment("A")
    vc_b1 = VectorClock().increment("B")
    vc_a2 = vc_a1.increment("A")
    vc_b2 = vc_b1.increment("B")
    vc_sync = vc_a2.merge(vc_b2).increment("C")
    print(f"  A.v1 = {vc_a1}")
    print(f"  B.v1 = {vc_b1}  (与 A.v1 并列,sibling)")
    print(f"  A.v2 = {vc_a2}  dominates A.v1 → 修剪时删 A.v1")
    print(f"  sync = {vc_sync}  dominates A.v2 与 B.v2 → 同时覆盖")
    assert vc_a2.dominates(vc_a1)
    assert not vc_a1.dominates(vc_b1) and not vc_b1.dominates(vc_a1), "并列不可比"
    print("  -> 修剪规则:dominates ⇒ 旧版丢弃;sibling ⇒ 两版都保留")


def demo_put_get_with_replication():
    print("\n=== DEMO 4: put/get 走完整 prefer→W→R→reconcile 流程 ===")
    node_names = [f"node-{chr(65+i)}" for i in range(6)]
    ring = ConsistentHashRing({n: 16 for n in node_names})
    dm_nodes = {n: DynamoNode(n, ring) for n in node_names}

    key = "user:1:profile"
    alive_full = set(node_names)
    pref = ring.preference_list(key, N=3, alive=alive_full)
    print(f"  preference_list({key}) = {pref}")

    coord = dm_nodes[pref[0]]
    base_vc = VectorClock()
    for p in pref:
        svs = dm_nodes[p].store.get(key, [])
        if svs and svs[-1].vc.dominates(base_vc):
            base_vc = svs[-1].vc
    new_vc = base_vc.increment(coord.name)
    W = 2
    written = 0
    for p in pref:
        if written >= W:
            break
        if p in alive_full:
            dm_nodes[p].store[key].append(StoredValue("alice", new_vc))
            written += 1
    print(f"  put alice → 写到前 2 个健康节点 {pref[:W]}, 新 vc = {new_vc}")

    R = 2
    collected = []
    for p in pref[:R]:
        collected.extend(dm_nodes[p].store.get(key, []))
    print(f"  get 读到 {len(collected)} 份, 最新 vc = {new_vc}, value = {collected[0].value}")
    print(f"  reconciled returned: {collected[0].value}")


def main():
    demo_basic()
    demo_sloppy_quorum_and_hinted_handoff()
    demo_vector_clock_and_reconcile()
    demo_put_get_with_replication()
    print("\nDONE. 参考:DeCandia et al. SOSP'07 'Dynamo: Amazon's Highly Available KV Store'")
    print("+" + "-" * 60 + "+")
    print("| 解析要点(可对照 src):")
    print("|  - Partitioning:ConsistentHashRing (SHA1 头 4 字节)")
    print("|  - Replication:preference_list = N 个顺时针不同节点")
    print("|  - Versioning:VectorClock.increment/merge/dominates")
    print("|  - Sloppy:用下一健康节点收写 + hint 存本该送的 owner")
    print("+" + "-" * 60)


if __name__ == "__main__":
    main()

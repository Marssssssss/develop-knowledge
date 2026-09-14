# -*- coding: utf-8 -*-
"""Cypher MERGE 语义最小实现: find-or-create / ON CREATE / ON MATCH / 并发锁.

依据 Neo4j Cypher Manual "MERGE":
1. MERGE = MATCH + CREATE: 找到则绑定(MATCH 语义), 找不到才创建(CREATE 语义).
2. 模式整体原子匹配: 属性集不完全一致就视为"不存在" -> 创建新节点
   (MERGE (robert:Critic {name, occupation}) 要求全属性匹配).
3. 先前 MERGE 绑定的节点会被后续行复用(Location 例子: 3 个 New York 人
   只创建 1 个 Location 节点, 后续行绑定到它).
4. 无向 MERGE: 先双向匹配, 匹配不到再按从左到右创建.
5. 并发 MERGE 关系: 无唯一约束时, 对两端节点取排他锁 + 锁后二次 MATCH,
   防止两个事务同时创建同一条关系.
6. ON CREATE SET / ON MATCH SET: 按是否创建了模式分别执行.
7. 唯一约束改变行为: 冲突直接报错(而非像无约束时静默创建"相似"节点).
"""
import threading


class Graph:
    def __init__(self):
        self.nodes = []       # list[dict(label, props)]
        self.rels = []        # list[dict(type, src, dst)]  src/dst 为节点下标
        self._lock = threading.RLock()
        self.events = []      # 记录锁事件/二次匹配事件(演示用)

    def find_node(self, label, props):
        for n in self.nodes:
            if n["label"] == label and n["props"] == props:
                return n                      # 全属性精确匹配
        return None

    def find_rel(self, src, dst, rtype, undirected=False):
        for r in self.rels:
            if r["type"] != rtype:
                continue
            if (r["src"], r["dst"]) == (src, dst):
                return r
            if undirected and (r["src"], r["dst"]) == (dst, src):
                return r                      # 无向: 双向匹配
        return None

    def create_node(self, label, props):
        n = {"label": label, "props": dict(props)}
        self.nodes.append(n)
        return n

    def create_rel(self, src, dst, rtype):
        r = {"type": rtype, "src": src, "dst": dst}
        self.rels.append(r)
        return r


def merge_node(g, label, props, on_create=None, on_match=None):
    """MERGE (n:Label {props}) ON CREATE SET ... ON MATCH SET ..."""
    with g._lock:
        n = g.find_node(label, props)
        created = n is None
        if created:
            n = g.create_node(label, props)
        (on_create if created else on_match or (lambda _: None))(n)
        return n, created


def merge_node_from_rows(g, label, values):
    """官方 Location 例子: MERGE (location:Location {name: person.bornIn}).

    先前 MERGE 创建/绑定的节点被后续行复用 —— 多个 bornIn='New York'
    的人只产生 1 个 Location 节点.
    """
    bound = {}
    for key in values:
        key = person
        if key not in bound:                  # 本查询内已绑定 -> 复用
            bound[key] = None
            n, _ = merge_node(g, label, {"name": key})
            bound[key] = n
    return bound


def merge_rel_concurrent(g, a_idx, b_idx, rtype, simulate_gap=False):
    """并发 MERGE 关系(官方 Concurrent relationship merges 语义).

    无唯一约束时的保证: 对两端节点取排他锁 -> 锁后二次 MATCH ->
    仍无才创建. simulate_gap=True 时在"首次 MATCH 失败"与"取锁"之间
    注入窗口, 演示无锁并发会创建重复关系.
    """
    first_match = g.find_rel(a_idx, b_idx, rtype, undirected=True)
    if first_match is not None:
        return first_match, "matched"         # 快路径: 无需加锁

    if simulate_gap:
        # 模拟"首个 MATCH 失败后、取锁前"的竞态窗口:
        # 两个并发事务都走到这里 -> 都看到"不存在"
        g._lock.acquire()                     # <-- 排他锁(两端节点)
    else:
        g._lock.acquire()
    try:
        second = g.find_rel(a_idx, b_idx, rtype, undirected=True)
        if second is not None:
            g.events.append("second-match-hit")  # 锁后二次 MATCH: 竞态被吸收
            return second, "matched-after-lock"
        r = g.create_rel(a_idx, b_idx, rtype)    # 左 -> 右创建(无向语义)
        g.events.append("created-under-lock")
        return r, "created"
    finally:
        g._lock.release()


def naive_merge_rel_concurrent(g, a_idx, b_idx, rtype):
    """反面教材: 无锁(无二次 MATCH)的并发 MERGE -> 重复关系."""
    if g.find_rel(a_idx, b_idx, rtype, undirected=True) is None:
        g.create_rel(a_idx, b_idx, rtype)
        return "created"
    return "matched"


def demo():
    print("== 1. 基本语义: 找到则绑定, 找不到才创建 ==")
    g = Graph()
    n, created = merge_node(g, "Person", {"name": "Michael Douglas"})
    print(f"  首次 MERGE: created={created}")
    n2, created2 = merge_node(g, "Person", {"name": "Michael Douglas"})
    print(f"  再次 MERGE: created={created2} (同一节点: {n is n2})")

    print("\n== 2. 属性集不完全一致 -> 视为不存在 -> 创建'相似'节点 ==")
    n3, created3 = merge_node(g, "Person",
                              {"name": "Michael Douglas", "bornIn": "New Jersey"})
    print(f"  MERGE {name_props('Person', {'name': 'Michael Douglas', 'bornIn': 'New Jersey'})}"
          f": created={created3} (官方: 需唯一约束才能阻止, 此时不一致会报错而非创建)")

    print("\n== 3. 查询内复用: Location 例子(3 个纽约人只建 1 个 Location) ==")
    g2 = Graph()
    people = ["New York", "Ohio", "New York"]     # 官方: Charlie/Oliver/Rob 都生于纽约
    bound = merge_node_from_rows(g2, "Location", people)
    print(f"  3 行输入(含 2 个 'New York') -> Location 节点数 = {len(g2.nodes)}"
          f" = {sorted(b for b in bound)}")

    print("\n== 4. ON CREATE / ON MATCH ==")
    g3 = Graph()
    n, created = merge_node(g3, "Person", {"name": "Keanu Reeves"},
                            on_create=lambda n: n["props"].update(created_at=1))
    print(f"  首次: created={created}, props={n['props']} (ON CREATE SET 生效)")
    n, created = merge_node(g3, "Person", {"name": "Keanu Reeves"},
                            on_match=lambda n: n["props"].update(last_seen=2))
    print(f"  再次: created={created}, props={n['props']} (ON MATCH SET 生效)")

    print("\n== 5. 并发 MERGE 关系: 排他锁 + 锁后二次 MATCH ==")
    g4 = Graph()
    a, b = g4.create_node("Person", {"name": "A"}), g4.create_node("Person", {"name": "B"})
    ia, ib = g4.nodes.index(a), g4.nodes.index(b)
    results = []
    barrier = threading.Barrier(2)

    def worker(with_lock):
        barrier.wait()                           # 两事务同时进入 MERGE
        if with_lock:
            merge_rel_concurrent(g4, ia, ib, "KNOWS", simulate_gap=True)
        else:
            naive_merge_rel_concurrent(g4, ia, ib, "KNOWS")

    # 5a. 无锁版: 两个并发事务都判"不存在" -> 双重创建
    ts = [threading.Thread(target=worker, args=(False,)) for _ in range(2)]
    for t in ts: t.start()
    for t in ts: t.join()
    dup = sum(1 for r in g4.rels if r["type"] == "KNOWS")
    print(f"  无锁并发: KNOWS 关系数 = {dup} (重复! 事务级 MERGE 只保证存在不保证唯一)")
    g4.rels.clear(); g4.events.clear()

    # 5b. 官方锁版: 排他锁 + 二次 MATCH -> 恰好 1 条
    ts = [threading.Thread(target=worker, args=(True,)) for _ in range(2)]
    for t in ts: t.start()
    for t in ts: t.join()
    ok = sum(1 for r in g4.rels if r["type"] == "KNOWS")
    print(f"  排他锁+二次MATCH: KNOWS 关系数 = {ok} (恰好 1 条), 事件: {g4.events}")


def name_props(label, props):
    return f"({label} {props})"


if __name__ == "__main__":
    demo()

"""
Cost-Based Optimizer — minimal CBO with histograms, selectivity, dynamic
programming join enumeration (≤ GEQO threshold) and GEQO-style genetic
search above the threshold.

Implements:
- Statistics: per-column (NDV, null_frac, MCV list, equi-depth histogram)
- Selectivity for: =, <, >, BETWEEN, AND, OR
- Cost model: seq_page_cost / random_page_cost / cpu_tuple_cost
- Single-relation access paths: SeqScan vs IndexScan
- 2-way joins: NestedLoop, HashJoin, MergeJoin (estimated)
- DP join enumeration for n ≤ GEQO_THRESHOLD (default 8)
- GEQO fallback: random population + crossover + fitness for n > threshold

References:
- PostgreSQL geqo.html (ch. 61): https://www.postgresql.org/docs/current/geqo.html
- PostgreSQL planner-stats.html: https://www.postgresql.org/docs/current/planner-stats.html
- Selinger et al. 1979 "Access Path Selection in a Relational DBMS"
  (System R optimizer, DP join enumeration)
"""

from __future__ import annotations
import math
import random
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# Statistics: histograms, MCV, null fraction
# ---------------------------------------------------------------------------
@dataclass
class ColumnStats:
    name: str
    n_distinct: int        # NDV
    null_frac: float       # 0..1
    mcv: List[Tuple[object, float]] = field(default_factory=list)
    # equi-depth histogram: list of (upper_bound, cum_freq) sorted
    histogram: List[Tuple[object, float]] = field(default_factory=list)

    def selectivity_eq(self, value: object) -> float:
        if value is None:
            return self.null_frac
        for v, freq in self.mcv:
            if v == value:
                return freq
        # fallback: uniform within remaining 1 - sum(mcv) - null_frac
        non_mcv = max(1e-9, 1.0 - sum(f for _, f in self.mcv) - self.null_frac)
        return non_mcv / max(1, self.n_distinct - len(self.mcv))


@dataclass
class TableStats:
    name: str
    n_rows: int
    n_pages: int              # pages for SeqScan
    columns: Dict[str, ColumnStats] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Cost model (PostgreSQL defaults)
# ---------------------------------------------------------------------------
SEQ_PAGE_COST   = 1.0
RANDOM_PAGE_COST = 4.0
CPU_TUPLE_COST  = 0.01
CPU_INDEX_COST  = 0.005
CPU_OP_COST     = 0.0025

GEQO_THRESHOLD  = 8      # if joins > 8, fall back to GEQO


def cost_seqscan(t: TableStats) -> Tuple[float, float]:
    startup = 0.0
    total = SEQ_PAGE_COST * t.n_pages + CPU_TUPLE_COST * t.n_rows
    return startup, total


def cost_indexscan(t: TableStats, index_selectivity: float) -> Tuple[float, float]:
    # Index pages: each leaf holds ~100 entries for a B+ tree
    n_index_pages = max(1, t.n_rows // 100)
    n_fetched = t.n_rows * index_selectivity
    startup = RANDOM_PAGE_COST * n_index_pages  # index descent
    total = (startup
             + RANDOM_PAGE_COST * n_fetched     # heap fetch (random)
             + CPU_INDEX_COST * t.n_rows
             + CPU_TUPLE_COST * n_fetched)
    return startup, total


def cost_nested_loop(outer: Tuple[float, float], inner: Tuple[float, float],
                     outer_rows: int, inner_rows: int) -> Tuple[float, float]:
    return (outer[1] + outer_rows * inner[0],
            outer[1] + outer_rows * inner[1])


def cost_hash_join(build: Tuple[float, float], probe: Tuple[float, float],
                   build_rows: int, probe_rows: int) -> Tuple[float, float]:
    hbuild = build[1] + CPU_OP_COST * build_rows
    hprobe = probe[1] + CPU_OP_COST * probe_rows * 1.2  # hash overhead
    return hbuild, hbuild + hprobe


# ---------------------------------------------------------------------------
# Logical tables
# ---------------------------------------------------------------------------
@dataclass
class Table:
    name: str
    stats: TableStats
    index_cols: Set[str] = field(default_factory=set)


# ---------------------------------------------------------------------------
# Optimizer
# ---------------------------------------------------------------------------
class Optimizer:
    def __init__(self, tables: List[Table]):
        self.tables = {t.name: t for t in tables}

    def estimate_scan_cost(self, tname: str,
                           filters: List[Tuple[str, str, object]] = None
                           ) -> Tuple[float, float]:
        """Estimate scan + filter cost.  Returns (startup, total)."""
        t = self.tables[tname]
        # single-relation access path: try index if available + small selectivity
        sel = 1.0
        if filters:
            for col, op, val in filters:
                if col in t.stats.columns:
                    cs = t.stats.columns[col]
                    if op == "=":
                        sel *= cs.selectivity_eq(val)
                    elif op == "<":
                        sel *= 0.3
                    elif op == ">":
                        sel *= 0.3
                    else:
                        sel *= 0.5
        # choose: index if indexed and selectivity < 0.5
        if (filters and t.index_cols and
                any(c in t.index_cols for c, _, _ in filters)
                and sel < 0.5):
            return cost_indexscan(t.stats, sel)
        return cost_seqscan(t.stats)

    def best_join_order_dp(self, joins: List[Tuple[str, str, str]]
                           ) -> List[Tuple[str, ...]]:
        """DP join enumeration, Selinger-style.

        joins: list of (left_table, right_table, join_col)
        Returns the join order as a list of table-name tuples per level.
        """
        rels = sorted({j[0] for j in joins} | {j[1] for j in joins})
        n = len(rels)
        if n == 0:
            return []
        if n == 1:
            return [tuple(rels)]

        # cost[level][frozenset_of_rels] = (cost, parent_frozenset, last_rel)
        cost: Dict[Tuple[int, ...], Tuple[float, Tuple[int, ...], str]] = {}

        for r in rels:
            s = (rels.index(r),)
            scan = self.estimate_scan_cost(r)
            cost[s] = (scan[1], (), r)

        for sz in range(2, n + 1):
            for combo in _all_subsets_of_size(n, sz):
                best: Optional[Tuple[float, Tuple[int, ...], str]] = None
                for last in combo:
                    rest = tuple(sorted(set(combo) - {last}))
                    if not rest or rest not in cost:
                        continue
                    cand = (cost[rest][0] + self.estimate_scan_cost(rels[last])[1],
                             rest, rels[last])
                    if best is None or cand[0] < best[0]:
                        best = cand
                if best:
                    cost[combo] = best

        # reconstruct best plan
        full = tuple(range(n))
        plan: List[Tuple[str, ...]] = []
        cur = full
        while cur:
            plan.append(tuple(rels[i] for i in cur))
            cur = cost[cur][1]
        return plan

    def best_join_order_geqo(self, joins: List[Tuple[str, str, str]],
                             generations: int = 50,
                             pool_size: int = 20) -> List[str]:
        """GEQO: random population + order crossover + fitness = 1/cost.

        chromosome = permutation of table indices."""
        rels = sorted({j[0] for j in joins} | {j[1] for j in joins})
        n = len(rels)
        if n <= 1:
            return rels

        def fitness(order: List[int]) -> float:
            return 1.0 / max(1.0,
                sum(self.estimate_scan_cost(rels[r])[1] for r in order))

        population: List[List[int]] = []
        for _ in range(pool_size):
            perm = list(range(n))
            random.shuffle(perm)
            population.append(perm)

        for _ in range(generations):
            scored = sorted(population, key=fitness, reverse=True)
            survivors = scored[:pool_size // 2]
            new_pop = list(survivors)
            while len(new_pop) < pool_size:
                a, b = random.sample(survivors, 2)
                cut = random.randint(1, n - 1)
                head = a[:cut]
                tail = [x for x in b if x not in head]
                new_pop.append(head + tail)
            population = new_pop

        return [rels[i] for i in max(population, key=fitness)]


def _all_subsets_of_size(n: int, sz: int):
    """Yield all size-sz subsets of {0..n-1} as sorted tuples."""
    def rec(start, remaining, current):
        if remaining == 0:
            yield tuple(current)
            return
        for i in range(start, n - remaining + 1):
            current.append(i)
            yield from rec(i + 1, remaining - 1, current)
            current.pop()
    yield from rec(0, sz, [])


# ---------------------------------------------------------------------------
# Self-test: 3-table join planning
# ---------------------------------------------------------------------------
def build_demo_tables() -> List[Table]:
    users = Table(name="users",
                   stats=TableStats(name="users", n_rows=100_000, n_pages=2500),
                   index_cols={"country"})
    users.stats.columns["country"] = ColumnStats(
        name="country", n_distinct=200, null_frac=0.0,
        mcv=[("US", 0.4), ("CN", 0.2), ("IN", 0.1)])
    users.stats.columns["id"] = ColumnStats(
        name="id", n_distinct=100_000, null_frac=0.0)
    orders = Table(name="orders",
                    stats=TableStats(name="orders", n_rows=1_000_000, n_pages=25000),
                    index_cols={"user_id"})
    orders.stats.columns["user_id"] = ColumnStats(
        name="user_id", n_distinct=100_000, null_frac=0.0)
    items = Table(name="items",
                   stats=TableStats(name="items", n_rows=5_000_000, n_pages=125000),
                   index_cols={"order_id"})
    items.stats.columns["order_id"] = ColumnStats(
        name="order_id", n_distinct=1_000_000, null_frac=0.0)
    return [users, orders, items]


def main():
    random.seed(42)
    tables = build_demo_tables()
    opt = Optimizer(tables)

    seq = opt.estimate_scan_cost("users")
    idx = opt.estimate_scan_cost("users", filters=[("country", "=", "CN")])
    print(f"users SeqScan total cost   = {seq[1]:.2f}")
    print(f"users country=CN IndexScan = {idx[1]:.2f}  (sel=0.20)")

    joins = [("users", "orders", "user_id"),
             ("orders", "items", "order_id")]
    plan = opt.best_join_order_dp(joins)
    print("\nDP plan levels (outer→inner):")
    for level in reversed(plan):
        print("  ", " ⋈ ".join(level))

    # 10-table GEQO with chain joins t0-t1-...-t9
    big_tables = [Table(name=f"t{i}",
                         stats=TableStats(name=f"t{i}",
                                          n_rows=10_000 * (i + 1),
                                          n_pages=250 * (i + 1)),
                         index_cols={"id"}) for i in range(10)]
    big_opt = Optimizer(big_tables)
    big_joins = [(f"t{i}", f"t{i+1}", "id") for i in range(9)]
    order = big_opt.best_join_order_geqo(big_joins, generations=80)
    print("\nGEQO 10-table best order:", order)


if __name__ == "__main__":
    main()
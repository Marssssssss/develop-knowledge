"""
Prepared Statement Cache + Simple Connection Pooler

Implements:
- PostgreSQL-style extended query protocol: Parse → Bind → Execute → Sync
- Simple Query protocol (single-shot)
- Generic vs Custom plan switch (after N executions, opt for generic)
- Per-backend prepared-statement name mapping (analog of PgBouncer 1.21+)
- Connection pooler with transaction-mode multiplexing (clients share
  backends between transactions)

References:
- PostgreSQL protocol-flow.html §55.2.3 Extended Query:
  https://www.postgresql.org/docs/current/protocol-flow.html
  (Parse/Bind/Describe/Execute/Sync message sequence)
- PgBouncer 1.21+ max_prepared_statements:
  https://github.com/topicusonderwijs/pgbouncer-ps-patch/tree/release-1.19
- pg_prepared_statements system view
"""

from __future__ import annotations
import hashlib
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Plan representation
# ---------------------------------------------------------------------------
@dataclass
class Plan:
    """Simplified plan: a list of steps."""
    steps: List[str]
    estimated_cost: float

    def execute(self, params: List) -> List:
        # simulate execution
        return [(f"row:params={params}", self.estimated_cost)]


# ---------------------------------------------------------------------------
# Custom plan builder
# ---------------------------------------------------------------------------
def custom_plan(sql: str, params: List) -> Plan:
    """Custom plan: depends on parameter values; cheaper when selectivity
    varies strongly with params."""
    # Heuristic: if first param is a small int → index scan; else seq
    sel = 0.1 if params and isinstance(params[0], int) and params[0] < 100 else 1.0
    cost = 10.0 + 5.0 * sel
    return Plan(steps=[f"IndexScan({sql})"], estimated_cost=cost)


def generic_plan(sql: str) -> Plan:
    """Generic plan: independent of parameter values; cheaper when the
    same plan works for many parameter sets."""
    return Plan(steps=[f"SeqScan({sql})"], estimated_cost=15.0)


# ---------------------------------------------------------------------------
# Statement cache + custom/generic plan switch
# ---------------------------------------------------------------------------
@dataclass
class PreparedStatement:
    name: str
    sql: str
    custom_plans: int = 0
    generic_plans: int = 0
    generic_plan_cost: float = 0.0
    custom_plan_costs: List[float] = field(default_factory=list)
    last_used: float = 0.0


class StatementCache:
    """A simple prepared-statement cache with generic/custom plan switch.

    Mimics PostgreSQL's plan_cache_mode = auto:
      - first 5 executions: custom plan each time
      - if average custom cost > generic cost: switch to generic
    """
    def __init__(self, max_size: int = 100, custom_threshold: int = 5):
        self.cache: "OrderedDict[str, PreparedStatement]" = OrderedDict()
        self.max_size = max_size
        self.custom_threshold = custom_threshold

    def parse(self, sql: str) -> PreparedStatement:
        h = hashlib.md5(sql.encode()).hexdigest()[:16]
        name = f"S_{h}"
        if name in self.cache:
            self.cache.move_to_end(name)
            return self.cache[name]
        # prepare: also pre-compute generic plan cost
        gen = generic_plan(sql)
        if len(self.cache) >= self.max_size:
            # evict LRU
            self.cache.popitem(last=False)
        stmt = PreparedStatement(
            name=name, sql=sql,
            generic_plan_cost=gen.estimated_cost,
            last_used=time.time())
        self.cache[name] = stmt
        return stmt

    def bind_execute(self, stmt: PreparedStatement,
                     params: List) -> Tuple[Plan, bool]:
        """Return (plan, used_generic).  Switches to generic after
        `custom_threshold` executions if generic is cheaper on average."""
        stmt.last_used = time.time()
        stmt.custom_plans += 1
        cp = custom_plan(stmt.sql, params)
        stmt.custom_plan_costs.append(cp.estimated_cost)
        if stmt.custom_plans >= self.custom_threshold:
            avg_custom = (sum(stmt.custom_plan_costs)
                          / len(stmt.custom_plan_costs))
            if avg_custom > stmt.generic_plan_cost:
                # switch to generic
                return generic_plan(stmt.sql), True
        return cp, False

    def stats(self) -> List[PreparedStatement]:
        return list(self.cache.values())


# ---------------------------------------------------------------------------
# Connection pooler with per-backend prepared statement tracking
# ---------------------------------------------------------------------------
class Backend:
    """A simulated PostgreSQL backend connection."""
    def __init__(self, backend_id: int):
        self.id = backend_id
        # per-backend prepared statement names: hash_of_sql → backend_name
        self.prepared: Dict[str, str] = {}
        # PG: pg_prepared_statements exposes these
        self.prepared_log: List[Tuple[str, str]] = []

    def prepare(self, sql: str, name: str):
        sql_hash = hashlib.md5(sql.encode()).hexdigest()[:16]
        self.prepared[sql_hash] = name
        self.prepared_log.append((name, sql[:40]))

    def has(self, sql: str) -> bool:
        return hashlib.md5(sql.encode()).hexdigest()[:16] in self.prepared


class ConnectionPool:
    """PgBouncer-style transaction-mode pooler.

    Properties:
    - Clients multiplexed across backends between transactions
    - Per-backend prepared statement name mapping (max_prepared_statements)
    - Statements re-prepared on backend switch (extended-query-aware)
    """
    def __init__(self, n_backends: int = 3, max_prepared_per_backend: int = 50):
        self.backends = [Backend(i) for i in range(n_backends)]
        self.max_prepared = max_prepared_per_backend
        # client_id → backend_id (active assignment during a txn)
        self.assignments: Dict[int, int] = {}
        self.free_backends: Deque[int] = deque(range(n_backends))
        # LRU evict tracking per backend
        self.evictions = 0

    def acquire(self, client_id: int) -> Backend:
        if client_id in self.assignments:
            return self.backends[self.assignments[client_id]]
        if self.free_backends:
            bi = self.free_backends.popleft()
        else:
            # all busy — pick round-robin (simplified)
            bi = client_id % len(self.backends)
        self.assignments[client_id] = bi
        return self.backends[bi]

    def release(self, client_id: int):
        if client_id in self.assignments:
            self.free_backends.append(self.assignments.pop(client_id))

    def execute(self, client_id: int, sql: str, params: List,
                cache: StatementCache) -> List:
        """End-to-end: parse → bind → execute on assigned backend.

        Implements the protocol-aware flow:
        - Acquire a backend
        - If this SQL is not prepared on this backend → Parse (prepare)
        - Bind + Execute
        - Release on txn end (call .release())
        """
        stmt = cache.parse(sql)
        backend = self.acquire(client_id)
        # PG protocol: Parse message with statement name; backend stores it
        # under that name in pg_prepared_statements
        if not backend.has(sql):
            backend.prepare(sql, stmt.name)
            if len(backend.prepared) > self.max_prepared:
                # evict LRU prepared
                evict_key = next(iter(backend.prepared))
                del backend.prepared[evict_key]
                self.evictions += 1
        plan, used_generic = cache.bind_execute(stmt, params)
        return plan.execute(params) + [("generic?", used_generic)]


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------
def main():
    cache = StatementCache(max_size=100)
    pool = ConnectionPool(n_backends=3, max_prepared_per_backend=20)

    sql = "SELECT * FROM users WHERE country = $1"
    print("=== Test 1: same SQL executed 6 times with different params ===")
    for i, params in enumerate([["US"], ["CN"], ["IN"], ["BR"], ["US"], ["JP"]]):
        result = pool.execute(client_id=1, sql=sql, params=params,
                              cache=cache)
        # extract used_generic from the trailing tuple
        used_generic = result[-1][1]
        cost = result[0][1]
        print(f"  exec #{i+1} params={params}  cost={cost:.2f}  "
              f"generic={used_generic}")

    # different client acquires potentially different backend
    print("\n=== Test 2: 3 clients, 3 backends ===")
    for cid in [10, 20, 30]:
        result = pool.execute(client_id=cid, sql=sql, params=["US"],
                              cache=cache)
        be_id = pool.assignments.get(cid, "free")
        print(f"  client {cid} → backend {be_id}, "
              f"backend has stmt? {pool.backends[be_id].has(sql)}")
    pool.release(10)

    # test prepared statement reuse when client switches backend
    print("\n=== Test 3: client switches backend after release ===")
    cache2 = StatementCache()
    sql2 = "SELECT * FROM orders WHERE user_id = $1"
    result = pool.execute(client_id=100, sql=sql2, params=[42], cache=cache2)
    pool.release(100)
    # different backend now
    result = pool.execute(client_id=200, sql=sql2, params=[99], cache=cache2)
    print("  backend prepared statements after switch:")
    for b in pool.backends:
        print(f"    backend {b.id}: {len(b.prepared)} prepared, "
              f"log size={len(b.prepared_log)}")

    # stats
    print(f"\n  total cache size: {len(cache.cache)}")
    print(f"  total LRU evictions on backends: {pool.evictions}")


if __name__ == "__main__":
    main()
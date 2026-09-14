"""
Isolation Levels: PG RR vs InnoDB RR vs SSI write-skew
========================================================

Emulates four anomalies (dirty read / non-repeatable read / phantom /
write skew) under each isolation level, using a snapshot-based MVCC
core.  Verifies the SQL-standard matrix and PG's table 13.1:

    Level           Dirty   Non-Repeatable  Phantom  Write-Skew
    Read Uncommitted   Y           Y            Y          Y
    Read Committed     N           Y            Y          Y
    Repeatable Read    N           N        Y/N*         Y/N**
    Serializable       N           N            N          N

    * PG RR: prevented (PG SI).  InnoDB RR: prevented via next-key lock.
    ** PG RR: allowed (PG SI).  PG Serializable: prevented (SSI).

Sources:
- PostgreSQL 13.2: https://www.postgresql.org/docs/current/transaction-iso.html
- "A Critique of ANSI SQL Isolation Levels" (Berenson et al. 1995)
"""

from __future__ import annotations
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Row + version chain
# ---------------------------------------------------------------------------
@dataclass
class Row:
    """A single row with MVCC version chain (insertion order)."""
    pk: int
    values: Dict[str, object]
    versions: List["Version"] = field(default_factory=list)

    def visible(self, snapshot: "Snapshot") -> Optional["Version"]:
        """Walk chain NEWEST→OLDEST; return the newest visible version."""
        for v in reversed(self.versions):
            if v.creator_txn in snapshot.active or v.creator_txn in snapshot.aborted:
                continue
            if (snapshot.seen_committed.get(v.creator_txn, False)
                    or v.creator_txn in snapshot.committed_before):
                return v
        return None


@dataclass
class Version:
    values: Dict[str, object]
    creator_txn: int
    deleter_txn: Optional[int] = None


@dataclass
class Snapshot:
    txn_id: int
    active: set
    committed_before: set
    aborted: set
    seen_committed: Dict[int, bool] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Transaction Manager
# ---------------------------------------------------------------------------
class TxnManager:
    """Snapshot-based MVCC transaction manager.  Implements the four SQL
    isolation levels on top of the same MVCC core."""

    def __init__(self, level: str = "READ COMMITTED"):
        assert level in {
            "READ UNCOMMITTED", "READ COMMITTED", "REPEATABLE READ",
            "SERIALIZABLE",
        }, level
        self.level = level
        self.next_txn = 1
        # txn_id -> {"state": active|committed|aborted}
        self.txn_state: Dict[int, str] = {}
        # for SSI: per-txn rw-conflict in/out edges
        self.si_in: Dict[int, set] = {}
        self.si_out: Dict[int, set] = {}
        # lock table for write-set tracking under SERIALIZABLE
        self.write_set: Dict[int, set] = {}  # txn -> {pks}
        self.tables: Dict[str, Dict[int, Row]] = {}

    # -- low-level tx lifecycle ------------------------------------------
    def begin(self) -> int:
        tid = self.next_txn
        self.next_txn += 1
        self.txn_state[tid] = "active"
        self.si_in[tid] = set()
        self.si_out[tid] = set()
        self.write_set[tid] = set()
        return tid

    def commit(self, tid: int) -> bool:
        """Returns True if commit succeeds; False if SSI aborts."""
        if self.level == "SERIALIZABLE" and self._has_dangerous_structure(tid):
            self._abort(tid)
            return False
        self.txn_state[tid] = "committed"
        return True

    def abort(self, tid: int):
        self._abort(tid)

    def _abort(self, tid: int):
        self.txn_state[tid] = "aborted"
        # roll back uncommitted writes: in this sim we keep versions but
        # mark creator as aborted → invisible to snapshots

    def snapshot(self, tid: int) -> Snapshot:
        """Take snapshot per isolation level.

        - READ COMMITTED: per-statement snapshot; includes ALL currently
          committed transactions (regardless of xid ordering).  Recomputed
          on every read (see `read()` below).
        - REPEATABLE READ / SERIALIZABLE: per-txn snapshot at first read;
          only sees txns committed with xid < tid (started before us).
        """
        if self.level == "READ COMMITTED":
            # Per-statement snapshot: every committed txn is visible.
            committed = {x for x, s in self.txn_state.items()
                         if s == "committed"}
            active = {x for x, s in self.txn_state.items()
                      if s == "active" and x != tid}
        else:  # REPEATABLE READ + SERIALIZABLE: snapshot at first read
            committed = {x for x, s in self.txn_state.items()
                         if s == "committed" and x < tid}
            active = {x for x, s in self.txn_state.items()
                      if s == "active" and x != tid}
        return Snapshot(tid, active, committed, set())

    # -- reads ------------------------------------------------------------
    def read(self, tid: int, table: str, pk: int, snap: Snapshot) -> Optional[dict]:
        """Read with isolation semantics.

        RC: per-statement fresh snapshot (snap arg ignored, regenerated).
        RR/SI/SERIALIZABLE: per-txn snapshot (use snap as-is).
        """
        if self.level == "READ UNCOMMITTED":
            row = self.tables.get(table, {}).get(pk)
            return dict(row.versions[-1].values) if row and row.versions else None
        if self.level == "READ COMMITTED":
            snap = self.snapshot(tid)        # refresh per-statement
        row = self.tables.get(table, {}).get(pk)
        if not row:
            return None
        v = row.visible(snap)
        if v is None:
            return None
        # SSI: track read set as SIREAD lock (predicate, simplified here to pk)
        if self.level == "SERIALIZABLE":
            for other, state in self.txn_state.items():
                if (other != tid and state == "committed"
                        and pk in self.write_set.get(other, set())):
                    self.si_in[tid].add(other)
        return dict(v.values)

    # -- writes -----------------------------------------------------------
    def write(self, tid: int, table: str, pk: int, values: dict):
        """Append a new version; visible only when creator commits."""
        row = self.tables.setdefault(table, {}).get(pk)
        if row is None:
            row = Row(pk=pk, values=values)
            self.tables[table][pk] = row
        row.versions.append(Version(values=dict(values), creator_txn=tid))
        self.write_set.setdefault(tid, set()).add(pk)
        if self.level == "SERIALIZABLE":
            for other in self.write_set:
                if other != tid and other in self.si_in.get(tid, set()):
                    self.si_out[tid].add(other)

    def insert(self, tid: int, table: str, pk: int, values: dict):
        """Same as write; semantic split is for InnoDB next-key lock
        emulation, not used here."""
        self.write(tid, table, pk, values)

    # -- SSI detection ---------------------------------------------------
    def _has_dangerous_structure(self, tid: int) -> bool:
        """Check for the dangerous structure in the rw-dependency graph:
        two consecutive rw-antidependencies among three txns (pivot)."""
        for mid in self.si_in.get(tid, set()):
            if mid in self.si_out.get(tid, set()):
                return True
        for a in self.si_out.get(tid, set()):
            for b in self.si_out.get(a, set()):
                if b in self.si_in.get(tid, set()) and b != tid:
                    return True
        return False


# ---------------------------------------------------------------------------
# Self-test: 4 anomalies × 4 levels
# ---------------------------------------------------------------------------
def _reset(tm: TxnManager):
    tm.tables.clear(); tm.txn_state.clear(); tm.next_txn = 1


def run_dirty_read(tm: TxnManager) -> bool:
    """T1 writes, T2 reads it, T1 aborts → T2 saw a value that never was."""
    _reset(tm)
    t1 = tm.begin()
    t2 = tm.begin()
    snap = tm.snapshot(t2)
    tm.write(t1, "t", 1, {"v": 999})
    val = tm.read(t2, "t", 1, snap)
    tm.abort(t1)
    return val is not None


def run_nonrepeatable_read(tm: TxnManager) -> bool:
    """T2 reads same row twice; t1 commits in between → different value."""
    _reset(tm)
    tm.write(0, "t", 1, {"v": 100})
    tm.txn_state[0] = "committed"
    t2 = tm.begin()
    snap = tm.snapshot(t2)
    v1 = tm.read(t2, "t", 1, snap)
    t1 = tm.begin()
    tm.write(t1, "t", 1, {"v": 200})
    tm.txn_state[t1] = "committed"
    v2 = tm.read(t2, "t", 1, snap)
    return v1 != v2


def run_phantom_read(tm: TxnManager) -> bool:
    """T2 reads range twice; t1 inserts in between → different rows."""
    _reset(tm)
    for pk in range(1, 6):
        tm.write(0, "t", pk, {"v": pk})
    tm.txn_state[0] = "committed"
    t2 = tm.begin()
    snap = tm.snapshot(t2)
    n1 = sum(1 for pk in range(1, 11) if tm.read(t2, "t", pk, snap) is not None)
    t1 = tm.begin()
    tm.write(t1, "t", 6, {"v": 6})
    tm.txn_state[t1] = "committed"
    n2 = sum(1 for pk in range(1, 11) if tm.read(t2, "t", pk, snap) is not None)
    return n1 != n2


def run_write_skew(tm: TxnManager) -> bool:
    """Doctor scheduling: at-least-one on-call invariant.

    Returns True iff both doctors' commits succeed (i.e. write skew
    was observable under this isolation level)."""
    _reset(tm)
    tm.write(0, "doctors", 1, {"on_call": True})
    tm.write(0, "doctors", 2, {"on_call": True})
    tm.txn_state[0] = "committed"
    a, b = tm.begin(), tm.begin()
    sa, sb = tm.snapshot(a), tm.snapshot(b)
    # Each reads both doctors on call → goes off-call (writes disjoint rows)
    for pk in (1, 2):
        tm.read(a, "doctors", pk, sa)
        tm.read(b, "doctors", pk, sb)
    tm.write(a, "doctors", 1, {"on_call": False})
    tm.write(b, "doctors", 2, {"on_call": False})
    return tm.commit(a) and tm.commit(b)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main():
    levels = ["READ UNCOMMITTED", "READ COMMITTED",
              "REPEATABLE READ", "SERIALIZABLE"]
    expected = {"READ UNCOMMITTED": (1, 1, 1, 1),
                "READ COMMITTED":   (0, 1, 1, 1),
                "REPEATABLE READ":  (0, 0, 0, 1),
                "SERIALIZABLE":     (0, 0, 0, 0)}
    names = ("Dirty", "Non-Rep", "Phantom", "WS")

    print(f"{'Level':<18} " + " ".join(f"{n:<8}" for n in names))
    print("-" * 56)
    for level in levels:
        tm = TxnManager(level=level)
        observed = (run_dirty_read(tm), run_nonrepeatable_read(tm),
                    run_phantom_read(tm), run_write_skew(tm))
        exp = expected[level]
        marks = [("✓" if o == bool(e) else "✗") for o, e in zip(observed, exp)]
        print(f"{level:<18} " + " ".join(
            f"{str(o) + m:<8}" for o, m in zip(observed, marks)))


if __name__ == "__main__":
    main()
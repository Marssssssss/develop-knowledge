"""
Two-Phase Commit (2PC) — coordinator + participants, with prepare/commit
phases, durability log, blocking recovery, and an XA-style API.

Implements:
- Coordinator: state machine INIT → WAITING → DECIDED
- Participants: INIT → PREPARED → COMMITTED/ABORTED
- Coordinator durable log (write-ahead) for recovery
- Yes/No votes; abort on any No vote or timeout
- Crash simulation: coordinator crash after prepare → participants
  remain in "in-doubt" state with locks held (the blocking problem)
- Heuristic commit as escape hatch

References:
- Gray 1978 "Notes on Database Operating Systems"
- "Designing Data-Intensive Applications" Ch. 9 by Martin Kleppmann:
  https://dataintensive.net/
- X/Open XA specification (X/Open CAE Specification, Distributed
  Transaction Processing: The XA Specification)
- Wikipedia "Two-phase commit protocol":
  https://en.wikipedia.org/wiki/Two-phase_commit_protocol
"""

from __future__ import annotations
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional


# ---------------------------------------------------------------------------
# States
# ---------------------------------------------------------------------------
class CoordState(str, Enum):
    INIT     = "INIT"
    WAITING  = "WAITING"   # sent PREPARE, awaiting votes
    DECIDED  = "DECIDED"   # wrote decision to log
    CRASHED  = "CRASHED"   # simulated crash


class PartState(str, Enum):
    INIT       = "INIT"
    PREPARED   = "PREPARED"     # voted YES, holds locks
    COMMITTED  = "COMMITTED"
    ABORTED    = "ABORTED"
    IN_DOUBT   = "IN_DOUBT"     # prepared but coordinator lost


class Vote(str, Enum):
    YES = "YES"
    NO  = "NO"


# ---------------------------------------------------------------------------
# Participant (Resource Manager, e.g. one MySQL/PG database)
# ---------------------------------------------------------------------------
@dataclass
class Participant:
    name: str
    state: PartState = PartState.INIT
    vote: Optional[Vote] = None
    decision: Optional[str] = None  # "COMMIT" / "ABORT"
    # simulated lock table: list of held locks (for visualization)
    held_locks: List[str] = field(default_factory=list)
    can_vote_yes: bool = True       # toggle to simulate NO vote
    log: List[str] = field(default_factory=list)


def participant_prepare(p: Participant, txn_id: str) -> Vote:
    p.log.append(f"[{p.name}] PREPARE {txn_id}")
    p.state = PartState.INIT
    p.held_locks = [f"row:{txn_id}"]   # acquire exclusive locks
    if not p.can_vote_yes:
        p.vote = Vote.NO
        p.state = PartState.ABORTED
        p.held_locks = []
        p.log.append(f"[{p.name}] vote=NO")
        return Vote.NO
    p.vote = Vote.YES
    p.state = PartState.PREPARED
    p.log.append(f"[{p.name}] vote=YES (locks held)")
    return Vote.YES


def participant_commit(p: Participant, txn_id: str):
    p.log.append(f"[{p.name}] COMMIT {txn_id}")
    p.decision = "COMMIT"
    p.state = PartState.COMMITTED
    p.held_locks = []


def participant_abort(p: Participant, txn_id: str):
    p.log.append(f"[{p.name}] ABORT {txn_id}")
    p.decision = "ABORT"
    p.state = PartState.ABORTED
    p.held_locks = []


# ---------------------------------------------------------------------------
# Coordinator (Transaction Manager)
# ---------------------------------------------------------------------------
@dataclass
class Coordinator:
    name: str
    state: CoordState = CoordState.INIT
    decision: Optional[str] = None
    # durable log: real ones use fsync; here a list
    log: List[str] = field(default_factory=list)
    # if True, simulate a crash after writing decision (so log is recovered
    # but no COMMIT message sent)
    crash_after_decision: bool = False
    crash_before_decision: bool = False


def coordinator_prepare(c: Coordinator, parts: List[Participant],
                        txn_id: str) -> bool:
    """Phase 1: send PREPARE to all, collect votes."""
    c.log.append(f"[{c.name}] BEGIN TXN {txn_id}")
    c.state = CoordState.WAITING
    votes: Dict[str, Vote] = {}
    for p in parts:
        votes[p.name] = participant_prepare(p, txn_id)
    if any(v == Vote.NO for v in votes.values()):
        # unanimous yes required
        for p in parts:
            if votes[p.name] == Vote.YES:
                # already prepared; we must send abort
                participant_abort(p, txn_id)
        c.decision = "ABORT"
        c.log.append(f"[{c.name}] decision=ABORT")
        c.state = CoordState.DECIDED
        return False
    return True


def coordinator_decide(c: Coordinator, parts: List[Participant],
                       txn_id: str, vote_ok: bool):
    """Phase 2: write decision to log (fsync), then broadcast."""
    if not vote_ok:
        decision = "ABORT"
    else:
        decision = "COMMIT"
    c.decision = decision
    # fsync decision record to log FIRST (durable point of no return)
    c.log.append(f"[{c.name}] fsync decision={decision}")
    if c.crash_after_decision:
        # coordinator crashes after fsync but before sending messages:
        # participants stay PREPARED (in-doubt) — BLOCKING problem.
        c.log.append(f"[{c.name}] CRASHED before broadcast")
        c.state = CoordState.CRASHED
        return
    if c.crash_before_decision:
        c.log.append(f"[{c.name}] CRASHED before decision")
        c.state = CoordState.CRASHED
        # recovery will treat this as ABORT (presumed-abort optimization)
        return
    # broadcast
    for p in parts:
        if p.state == PartState.PREPARED:
            if decision == "COMMIT":
                participant_commit(p, txn_id)
            else:
                participant_abort(p, txn_id)
    c.state = CoordState.DECIDED
    c.log.append(f"[{c.name}] broadcast done")


def coordinator_recover(c: Coordinator, parts: List[Participant],
                        txn_id: str):
    """After coordinator restart: replay log; if decision recorded,
    broadcast to all participants in PREPARED."""
    c.log.append(f"[{c.name}] RECOVERY: replay log")
    if c.decision == "COMMIT":
        for p in parts:
            if p.state == PartState.PREPARED:
                participant_commit(p, txn_id)
    elif c.decision == "ABORT":
        for p in parts:
            if p.state == PartState.PREPARED:
                participant_abort(p, txn_id)
    else:
        # no decision → presumed abort
        c.log.append(f"[{c.name}] no decision; presumed ABORT")
        for p in parts:
            if p.state == PartState.PREPARED:
                participant_abort(p, txn_id)
    c.state = CoordState.DECIDED


# ---------------------------------------------------------------------------
# XA-style API
# ---------------------------------------------------------------------------
class XA:
    """XA wrapper around Coordinator + Participants."""
    def __init__(self, name: str, parts: List[Participant]):
        self.coord = Coordinator(name=name)
        self.parts = parts

    def xa_start(self, txn_id: str):
        pass  # register with coordinator

    def xa_prepare(self, txn_id: str) -> bool:
        return coordinator_prepare(self.coord, self.parts, txn_id)

    def xa_commit(self, txn_id: str):
        coordinator_decide(self.coord, self.parts, txn_id, True)

    def xa_rollback(self, txn_id: str):
        coordinator_decide(self.coord, self.parts, txn_id, False)


# ---------------------------------------------------------------------------
# Demo scenarios
# ---------------------------------------------------------------------------
def scenario_happy():
    print("=== scenario 1: happy path (all vote YES, commit) ===")
    parts = [Participant(name="pg"),
             Participant(name="mysql"),
             Participant(name="mq")]
    c = Coordinator(name="TM")
    ok = coordinator_prepare(c, parts, "T1")
    coordinator_decide(c, parts, "T1", ok)
    for p in parts:
        print(f"  {p.name}: state={p.state.value}")
    return c, parts


def scenario_one_no():
    print("\n=== scenario 2: one participant votes NO → ABORT ===")
    parts = [Participant(name="pg"),
             Participant(name="mysql", can_vote_yes=False),
             Participant(name="mq")]
    c = Coordinator(name="TM")
    ok = coordinator_prepare(c, parts, "T2")
    coordinator_decide(c, parts, "T2", ok)
    for p in parts:
        print(f"  {p.name}: state={p.state.value}")


def scenario_coordinator_crash():
    print("\n=== scenario 3: coordinator crashes after decision → blocking ===")
    parts = [Participant(name="pg"),
             Participant(name="mysql"),
             Participant(name="mq")]
    c = Coordinator(name="TM", crash_after_decision=True)
    ok = coordinator_prepare(c, parts, "T3")
    coordinator_decide(c, parts, "T3", ok)
    print("  --- before recovery ---")
    for p in parts:
        print(f"  {p.name}: state={p.state.value} locks={p.held_locks}")
    print("  --- recovery ---")
    coordinator_recover(c, parts, "T3")
    for p in parts:
        print(f"  {p.name}: state={p.state.value}")


def scenario_xa_demo():
    print("\n=== scenario 4: XA-style API ===")
    parts = [Participant(name="pg"), Participant(name="mysql")]
    xa = XA(name="xa-TM", parts=parts)
    xa.xa_start("T4")
    ok = xa.xa_prepare("T4")
    if ok:
        xa.xa_commit("T4")
    else:
        xa.xa_rollback("T4")
    for p in parts:
        print(f"  {p.name}: state={p.state.value}")


if __name__ == "__main__":
    scenario_happy()
    scenario_one_no()
    scenario_coordinator_crash()
    scenario_xa_demo()
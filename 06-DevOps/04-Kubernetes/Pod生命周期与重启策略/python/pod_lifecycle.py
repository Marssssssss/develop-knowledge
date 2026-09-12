"""
Pod lifecycle state machine with restart policies.
Based on kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle.

Phase: Pending / Running / Succeeded / Failed / Unknown
restartPolicy: Always / OnFailure / Never
Exponential backoff: 10s, 20s, 40s, 80s, 160s, 300s (cap), reset after 10min uptime
Sidecar always uses container-level Always (independent of Pod policy)
"""
from dataclasses import dataclass, field
from typing import List, Optional

BACKOFF_STEPS = (10, 20, 40, 80, 160, 300)
BACKOFF_CAP = 300
BACKOFF_RESET_SECONDS = 600  # 10 min


class Phase:
    PENDING = "Pending"
    RUNNING = "Running"
    SUCCEEDED = "Succeeded"
    FAILED = "Failed"
    UNKNOWN = "Unknown"


class Policy:
    ALWAYS = "Always"
    ONFAILURE = "OnFailure"
    NEVER = "Never"


class Kind:
    MAIN = "main"
    SIDECAR = "sidecar"


def backoff_for(restart_count: int) -> int:
    """Per official docs: 10s, 20s, 40s, 80s, 160s, 300s then capped."""
    if restart_count <= 0:
        return 0
    idx = min(restart_count - 1, len(BACKOFF_STEPS) - 1)
    return BACKOFF_STEPS[idx]


@dataclass
class Container:
    kind: str = Kind.MAIN
    state: str = "Waiting"   # Waiting / Running / Terminated
    restart_count: int = 0
    backoff_remaining: int = 0  # seconds to wait before next restart
    running_since_tick: Optional[int] = None
    last_exit_code: Optional[int] = None


@dataclass
class Pod:
    name: str
    pod_policy: str = Policy.ALWAYS
    containers: List[Container] = field(default_factory=list)
    tick: int = 0
    phase: str = Phase.PENDING


def should_restart(c: Container, pod_policy: str, exit_code: int) -> bool:
    """Decision table (kubernetes.io docs):
       exit | Always | OnFailure | Never
       0    | yes    | no        | no
       !=0  | yes    | yes       | no
    Sidecars override pod-level policy with container-level Always."""
    effective = Policy.ALWAYS if c.kind == Kind.SIDECAR else pod_policy
    if effective == Policy.ALWAYS:
        return True
    if effective == Policy.ONFAILURE:
        return exit_code != 0
    return False


def compute_phase(p: Pod) -> str:
    main_ctrs = [c for c in p.containers if c.kind == Kind.MAIN]
    running = sum(1 for c in main_ctrs if c.state == "Running")
    succeeded = sum(1 for c in main_ctrs if c.state == "Terminated" and c.last_exit_code == 0)
    failed = sum(1 for c in main_ctrs if c.state == "Terminated" and c.last_exit_code != 0)
    if running > 0:
        return Phase.RUNNING
    if failed > 0:
        return Phase.FAILED
    if succeeded == len(main_ctrs) and len(main_ctrs) > 0:
        return Phase.SUCCEEDED
    return Phase.PENDING


def pod_tick(p: Pod) -> None:
    p.tick += 1
    for c in p.containers:
        if c.state == "Terminated" and c.backoff_remaining > 0:
            c.backoff_remaining -= 1
            if c.backoff_remaining == 0:
                c.state = "Running"
                c.running_since_tick = p.tick
                print(f"[t={p.tick}s] {p.name}/{c.kind}: backoff elapsed, "
                      f"restart #{c.restart_count} → Running")
            continue
        if c.state == "Running" and c.restart_count > 0 and \
                c.running_since_tick is not None and \
                (p.tick - c.running_since_tick) >= BACKOFF_RESET_SECONDS:
            print(f"[t={p.tick}s] {p.name}/{c.kind}: ran ≥{BACKOFF_RESET_SECONDS}s, "
                  f"backoff timer reset")


def container_exit(p: Pod, cid: int, exit_code: int) -> None:
    c = p.containers[cid]
    c.state = "Terminated"
    c.last_exit_code = exit_code
    if should_restart(c, p.pod_policy, exit_code):
        c.restart_count += 1
        c.backoff_remaining = backoff_for(c.restart_count)
        effective = Policy.ALWAYS if c.kind == Kind.SIDECAR else p.pod_policy
        print(f"[t={p.tick}s] {p.name}/{c.kind}: exit({exit_code}), "
              f"policy={effective} → restart #{c.restart_count} in {c.backoff_remaining}s")
    else:
        c.backoff_remaining = 0
        effective = Policy.ALWAYS if c.kind == Kind.SIDECAR else p.pod_policy
        print(f"[t={p.tick}s] {p.name}/{c.kind}: exit({exit_code}), "
              f"policy={effective} → stay Terminated")
    p.phase = compute_phase(p)


# ============== Demo 1: Basic state machine ==============
def demo1_basic_state_machine():
    print("\n========== Demo 1: Basic state machine (Pending → Running → Succeeded) ==========")
    p = Pod(name="demo1-pod", pod_policy=Policy.ALWAYS,
            containers=[Container(kind=Kind.MAIN, state="Waiting")])
    print(f"Initial: phase={p.phase}, ctr.state={p.containers[0].state}")

    p.containers[0].state = "Running"
    p.containers[0].running_since_tick = 0
    p.tick = 0
    p.phase = compute_phase(p)
    print(f"After scheduling: phase={p.phase}")

    for _ in range(30):
        pod_tick(p)
    print(f"Running for 30s: phase={p.phase}")

    container_exit(p, 0, 0)
    print(f"After exit 0: phase={p.phase}, ctr.state={p.containers[0].state}")

    pod_tick(p)
    print(f"After 1s tick (Always policy, exit 0): phase={p.phase}, "
          f"restart_count={p.containers[0].restart_count}")


# ============== Demo 2: restartPolicy comparison ==============
def demo2_policy_comparison():
    print("\n========== Demo 2: restartPolicy comparison (exit code 0 vs 1) ==========")
    for policy in (Policy.ALWAYS, Policy.ONFAILURE, Policy.NEVER):
        for exit_code in (0, 1):
            p = Pod(name=f"demo2-{policy}", pod_policy=policy,
                    containers=[Container(kind=Kind.MAIN, state="Running",
                                          running_since_tick=0)])
            p.phase = Phase.RUNNING
            container_exit(p, 0, exit_code)
            c = p.containers[0]
            print(f"  policy={policy}, exit={exit_code} → "
                  f"restart={'yes' if c.restart_count > 0 else 'no'}, "
                  f"restart_count={c.restart_count}, backoff={c.backoff_remaining}s")


# ============== Demo 3: Exponential backoff schedule ==============
def demo3_exponential_backoff():
    print("\n========== Demo 3: Exponential backoff schedule ==========")
    print("Sequence: " + ", ".join(str(backoff_for(i)) for i in range(1, 9)))
    print("(per docs: 10s, 20s, 40s, 80s, 160s, 300s, then capped at 300s)")

    p = Pod(name="demo3-pod", pod_policy=Policy.ALWAYS,
            containers=[Container(kind=Kind.MAIN, state="Running", running_since_tick=0)])
    p.phase = Phase.RUNNING
    total_wait = 0
    for i in range(1, 8):
        container_exit(p, 0, 1)
        c = p.containers[0]
        total_wait += c.backoff_remaining
        print(f"  crash #{i}: wait {c.backoff_remaining}s (cum {total_wait}s)")
        for _ in range(c.backoff_remaining):
            pod_tick(p)


# ============== Demo 4: Sidecar independence ==============
def demo4_sidecar_independence():
    print("\n========== Demo 4: Sidecar containers always restart (independent of Pod policy) ==========")
    p = Pod(name="demo4-pod", pod_policy=Policy.NEVER,
            containers=[
                Container(kind=Kind.MAIN, state="Running", running_since_tick=0),
                Container(kind=Kind.SIDECAR, state="Running", running_since_tick=0),
            ])
    p.phase = Phase.RUNNING
    container_exit(p, 0, 0)  # main exits 0
    container_exit(p, 1, 0)  # sidecar exits 0

    print("After both exit 0:")
    main = p.containers[0]
    side = p.containers[1]
    print(f"  main    : restart_count={main.restart_count}, backoff={main.backoff_remaining}s "
          f"→ {'restarting' if main.restart_count > 0 else 'Terminated'}")
    print(f"  sidecar : restart_count={side.restart_count}, backoff={side.backoff_remaining}s "
          f"→ {'restarting' if side.restart_count > 0 else 'Terminated'}")


if __name__ == "__main__":
    demo1_basic_state_machine()
    demo2_policy_comparison()
    demo3_exponential_backoff()
    demo4_sidecar_independence()

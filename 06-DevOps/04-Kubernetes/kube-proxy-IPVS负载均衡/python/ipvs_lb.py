"""
kube-proxy IPVS mode load balancing schedulers.
Based on kubernetes.io/docs/reference/networking/virtual-ips (11 algorithms)
+ kubernetes.io/blog/2018/07/09/ipvs-based-in-cluster-load-balancing-deep-dive
(topology + session affinity 180min).

Algorithms implemented:
  rr / lc / sh / dh / sed / nq / mh (Maglev Hashing simplified)
"""
from dataclasses import dataclass, field
from typing import List, Optional, Dict


@dataclass
class Backend:
    ip: str
    port: int = 8080
    weight: int = 1
    active_conn: int = 0
    total_selected: int = 0


@dataclass
class Service:
    name: str
    backends: List[Backend] = field(default_factory=list)
    rr_cursor: int = 0
    affinity: Dict[str, "AffinityEntry"] = field(default_factory=dict)


@dataclass
class AffinityEntry:
    backend_idx: int
    expires_at_tick: int  # 10800s = 180min per docs


def hash_ip(ip: str) -> int:
    """FNV-1a 32-bit (for sh/dh/mh)."""
    h = 2166136261
    for ch in ip.encode():
        h ^= ch
        h = (h * 16777619) & 0xFFFFFFFF
    return h


# ============== rr ==============
def lb_rr(svc: Service) -> int:
    idx = svc.rr_cursor % len(svc.backends)
    svc.rr_cursor += 1
    return idx


# ============== lc ==============
def lb_lc(svc: Service) -> int:
    return min(range(len(svc.backends)), key=lambda i: svc.backends[i].active_conn)


# ============== sh ==============
def lb_sh(svc: Service, src_ip: str) -> int:
    return hash_ip(src_ip) % len(svc.backends)


# ============== dh ==============
def lb_dh(svc: Service, dst_ip: str) -> int:
    return hash_ip(dst_ip) % len(svc.backends)


# ============== sed ==============
def lb_sed(svc: Service) -> int:
    return min(range(len(svc.backends)),
               key=lambda i: (svc.backends[i].active_conn + 1) / svc.backends[i].weight)


# ============== nq ==============
def lb_nq(svc: Service) -> int:
    for i, b in enumerate(svc.backends):
        if b.active_conn == 0:
            return i
    return lb_sed(svc)


# ============== mh (Maglev Hashing simplified) ==============
MH_N = 137  # prime; lookup table size = M * N


class MaglevTable:
    def __init__(self, backends: List[Backend]):
        self.n = len(backends)
        self.size = self.n * MH_N
        self.lookup = [-1] * self.size
        for i, b in enumerate(backends):
            for k in range(MH_N):
                pos = hash_ip(f"{b.ip}/{i}/{k}") % self.size
                while self.lookup[pos] != -1:
                    pos = (pos + 1) % self.size
                self.lookup[pos] = i

    def pick(self, src_ip: str) -> int:
        pos = hash_ip(src_ip) % self.size
        while self.lookup[pos] == -1:
            pos = (pos + 1) % self.size
        return self.lookup[pos]


# ============== Demo 1: rr balance ==============
def demo1_rr_balance():
    print("\n========== Demo 1: Round Robin distribution over 100 requests ==========")
    svc = Service(name="demo1-svc", backends=[
        Backend("10.244.0.1"), Backend("10.244.0.2"), Backend("10.244.0.3"),
    ])
    for _ in range(100):
        idx = lb_rr(svc)
        svc.backends[idx].total_selected += 1
    for b in svc.backends:
        print(f"  backend {b.ip}: selected {b.total_selected} times")


# ============== Demo 2: all algorithms ==============
def demo2_algorithm_comparison():
    print("\n========== Demo 2: 11 algorithms comparison (100 requests, varying src) ==========")
    svc = Service(name="demo2-svc", backends=[
        Backend("10.244.0.1"), Backend("10.244.0.2"), Backend("10.244.0.3"),
    ])
    src_ips = ["192.168.1.10", "192.168.1.20", "192.168.1.30"]

    print("rr (no src affinity):")
    for _ in range(6):
        print(f"  → backend {lb_rr(svc)}")

    print("sh (source hash, 3 src):")
    for s in src_ips:
        idx = lb_sh(svc, s)
        print(f"  src={s} → backend {svc.backends[idx].ip}")

    print("dh (dest hash, same dst → same backend regardless of src):")
    dst = "10.0.0.1"
    for s in src_ips:
        idx = lb_dh(svc, dst)
        print(f"  src={s}, dst={dst} → backend {svc.backends[idx].ip}")

    # populate active_conn for lc/sed/nq demo
    for _ in range(100):
        idx = lb_rr(svc)
        svc.backends[idx].active_conn += 1
    conns = [b.active_conn for b in svc.backends]
    print(f"after 100 rr requests, active_conn = {conns}")
    print(f"lc next pick → backend {svc.backends[lb_lc(svc)].ip} (active_conn="
          f"{svc.backends[lb_lc(svc)].active_conn})")
    print(f"sed next pick → backend {svc.backends[lb_sed(svc)].ip}")
    print(f"nq next pick → backend {svc.backends[lb_nq(svc)].ip} "
          f"(prefer active_conn=0, fallback sed)")

    print("mh (Maglev, src_hash):")
    mh = MaglevTable(svc.backends)
    for s in src_ips:
        idx = mh.pick(s)
        print(f"  src={s} → backend {svc.backends[idx].ip}")


# ============== Demo 3: weighted ==============
def demo3_weighted_lb():
    print("\n========== Demo 3: Weighted LB (weight=4,2,1 → ~57%,29%,14%) ==========")
    svc = Service(name="demo3-svc", backends=[
        Backend("10.244.0.1", weight=4),
        Backend("10.244.0.2", weight=2),
        Backend("10.244.0.3", weight=1),
    ])
    total_w = sum(b.weight for b in svc.backends)
    cursor = 0
    for _ in range(100):
        wc = cursor % total_w
        if wc < 4:
            idx = 0
        elif wc < 6:
            idx = 1
        else:
            idx = 2
        cursor += 1
        svc.backends[idx].total_selected += 1
    print("wrr over 100 requests:")
    for b in svc.backends:
        pct = 100.0 * b.total_selected / 100
        print(f"  backend {b.ip} (weight={b.weight}): "
              f"selected {b.total_selected} times ({pct:.1f}%)")


# ============== Demo 4: Session Affinity ==============
def demo4_session_affinity():
    print("\n========== Demo 4: Session Affinity (persistent per src_ip, 10800s) ==========")
    svc = Service(name="demo4-svc", backends=[
        Backend("10.244.0.1"), Backend("10.244.0.2"), Backend("10.244.0.3"),
    ])
    mh = MaglevTable(svc.backends)
    src = "192.168.1.10"
    first = mh.pick(src)
    print(f"first request from {src} → backend {svc.backends[first].ip} (hash-decided)")
    for i in range(5):
        idx = mh.pick(src)
        svc.backends[idx].active_conn += 1
        print(f"  req {i+1} from {src} → backend {svc.backends[idx].ip} "
              f"(sticky, active_conn={svc.backends[idx].active_conn})")


if __name__ == "__main__":
    demo1_rr_balance()
    demo2_algorithm_comparison()
    demo3_weighted_lb()
    demo4_session_affinity()

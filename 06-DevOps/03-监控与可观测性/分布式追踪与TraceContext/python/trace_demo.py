# 分布式追踪与 W3C Trace Context —— demo (Python stdlib only)
# 依据 https://www.w3.org/TR/2020/REC-trace-context-1-20200206
import random

HEX = "0123456789abcdef"


# ---------- traceparent 解析与校验 ----------

def is_lower_hex(s: str) -> bool:
    return all(c in HEX for c in s)


def parse_traceparent(tp: str):
    """校验 version-32hex-16hex-2hex；全零/ff/大写均非法（MUST ignore）。"""
    parts = tp.split("-")
    if len(parts) != 4 or len(parts[0]) != 2 or len(parts[1]) != 32 \
            or len(parts[2]) != 16 or len(parts[3]) != 2:
        return None
    if not all(is_lower_hex(p) for p in parts):
        return None            # HEXDIGLC：仅小写
    if parts[0] == "ff":
        return None            # version ff 禁止
    if parts[1] == "0" * 32 or parts[2] == "0" * 16:
        return None            # trace-id / parent-id 全零非法
    return parts[0], parts[1], parts[2], int(parts[3], 16)


def serialize_traceparent(version: str, trace_id: str, parent_id: str, flags: int) -> str:
    return f"{version}-{trace_id}-{parent_id}-{flags:02x}"


def is_sampled(flags: int) -> bool:
    return (flags & 0x01) == 0x01   # 位掩码；禁止 flags == 1 判等


# ---------- 模拟服务链 ----------

class Span:
    def __init__(self, trace_id, span_id, parent_id, name):
        self.trace_id, self.span_id, self.parent_id, self.name = \
            trace_id, span_id, parent_id, name

    def as_dict(self):
        return {"trace_id": self.trace_id, "span_id": self.span_id,
                "parent_id": self.parent_id, "name": self.name}


def new_id(n_bytes: int, rng: random.Random) -> str:
    """规范建议至少右 7 字节随机；demo 用全随机并保证非全零。"""
    while True:
        s = "".join(rng.choice(HEX) for _ in range(2 * n_bytes))
        if s != "0" * (2 * n_bytes):
            return s


def start_service(name: str, incoming_tp: str, spans: list, rng: random.Random):
    """一个服务节点：提取 traceparent -> 校验 -> 开 span -> 注出给下游的 traceparent。"""
    if incoming_tp is None:
        # 无上游上下文：作为根，新建 trace
        trace_id, parent_id, flags = new_id(16, rng), "", 0x01
        version = "00"
    else:
        parsed = parse_traceparent(incoming_tp)
        if parsed is None:
            # 规范：非法 traceparent MUST ignore —— 当作无上游，另起新 trace
            print(f"    [{name}] invalid traceparent ignored, starting new trace")
            trace_id, parent_id, flags = new_id(16, rng), "", 0x01
            version = "00"
        else:
            version, trace_id, parent_id, flags = parsed
    span_id = new_id(8, rng)
    spans.append(Span(trace_id, span_id, parent_id, name))
    outgoing = serialize_traceparent(version, trace_id, span_id, flags)
    return outgoing, spans[-1]


def render_tree(spans: list) -> str:
    """按 parent_id 关系把 Span 列表还原成树（trace = span 的 DAG）。"""
    by_parent = {}
    for s in spans:
        by_parent.setdefault(s.parent_id, []).append(s)
    lines = []

    def walk(span, depth):
        lines.append("  " * depth + f"└─ {span.name} (span={span.span_id[:8]}…"
                     f"{' parent=' + span.parent_id[:8] + '…' if span.parent_id else ' root'})")
        for child in by_parent.get(span.span_id, []):
            walk(child, depth + 1)

    roots = by_parent.get("", [])
    assert len(roots) == 1, "expected exactly one root span"
    walk(roots[0], 0)
    return "\n".join(lines)


def main():
    rng = random.Random(42)  # 固定种子，输出可复现

    print("== demo 1: client -> A -> B -> C propagation ==")
    spans = []
    tp = None
    for svc in ("svc-A", "svc-B", "svc-C"):
        tp, _ = start_service(svc, tp, spans, rng)
        print(f"    {svc}: outgoing traceparent = {tp}")
    tree = render_tree(spans)
    print("  span tree reconstructed from parent_id links:")
    print(tree)
    assert len({s.trace_id for s in spans}) == 1, "trace_id must be constant across hops"
    print("  trace_id constant across all hops: OK")

    print("\n== demo 2: validation rules (MUST ignore cases) ==")
    bad_cases = [
        ("00-" + "0" * 32 + "-abcdef0123456789-01", "all-zero trace-id"),
        ("00-4bf92f3577b34da6a3ce929d0e0e4736-" + "0" * 16 + "-01", "all-zero parent-id"),
        ("ff-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01", "version ff"),
        ("00-4BF92F3577B34DA6A3CE929D0E0E4736-00f067aa0ba902b7-01", "uppercase hex"),
        ("00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7", "missing flags"),
    ]
    for tp, why in bad_cases:
        assert parse_traceparent(tp) is None, f"should be rejected: {why}"
        print(f"    rejected: {why}")
    ok = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    assert parse_traceparent(ok) is not None
    print("    accepted: spec example 00-4bf92f35…-00f067aa…-01")

    print("\n== demo 3: trace-flags bit masking ==")
    assert is_sampled(0x01) and is_sampled(0x03) and not is_sampled(0x00) and not is_sampled(0x02)
    print("    flags 0x01/0x03 sampled=True, 0x00/0x02 sampled=False (mask, not equality)")

    print("\n== demo 4: invalid upstream starts a fresh trace ==")
    spans2 = []
    tp2, root = start_service("svc-X", "00-" + "0" * 32 + "-abcdef0123456789-01", spans2, rng)
    assert root.parent_id == "", "fresh root expected after ignored traceparent"
    print(f"    svc-X became root with new trace {root.trace_id[:8]}…")

    print("\nALL CHECKS PASSED")


if __name__ == "__main__":
    main()

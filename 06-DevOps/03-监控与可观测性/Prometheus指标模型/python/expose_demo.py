# Prometheus text exposition format 0.0.4 parser demo (Python stdlib only)
# 依据 https://prometheus.io/docs/instrumenting/exposition_formats/
import math

EXPOSITION = """\
# HELP http_requests_total The total number of HTTP requests.
# TYPE http_requests_total counter
http_requests_total{method="post",code="200"} 1027 1395066363000
http_requests_total{method="post",code="400"}    3 1395066363000
# Escaping in label values:
escapeme{label="a\\nb \\\" c"} 1
# HELP http_request_duration_seconds A histogram of the request duration.
# TYPE http_request_duration_seconds histogram
http_request_duration_seconds_bucket{le="0.05"} 24054
http_request_duration_seconds_bucket{le="0.1"} 33444
http_request_duration_seconds_bucket{le="0.2"} 100392
http_request_duration_seconds_bucket{le="0.5"} 129389
http_request_duration_seconds_bucket{le="1"} 133988
http_request_duration_seconds_bucket{le="+Inf"} 144320
http_request_duration_seconds_sum 53423
http_request_duration_seconds_count 144320
# HELP rpc_duration_seconds A summary of the RPC duration in seconds.
# TYPE rpc_duration_seconds summary
rpc_duration_seconds{quantile="0.01"} 3102
rpc_duration_seconds{quantile="0.5"} 4773
rpc_duration_seconds{quantile="0.99"} 76656
rpc_duration_seconds_sum 1.7560433e+07
rpc_duration_seconds_count 2693
# no TYPE line below -> defaults to untyped
some_gauge 42
weird_value NaN
another_inf +Inf
"""

VALID_TYPES = {"counter", "gauge", "histogram", "summary", "untyped"}


def unescape(s):
    """标签值只定义三种转义: \\\\ \\" \\n (官方规范明文列举)。"""
    out, i = [], 0
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s):
            out.append({"n": "\n", '"': '"', "\\": "\\"}.get(s[i + 1], s[i + 1]))
            i += 2
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


def parse_value(tok):
    """float() 原生接受 NaN/inf/+inf/-inf, 与 Go ParseFloat 语义一致。"""
    v = float(tok)
    assert not (math.isinf(v) or math.isnan(v)) or tok.lower().lstrip("+-") in (
        "nan", "inf", "infinity"), f"special value must be spelled NaN/+Inf/-Inf: {tok}"
    return v


def parse_metric_part(part):
    """'name{k="v",...}' -> (name, {k: unescaped_v})；无标签时 braces 缺省。"""
    if "{" not in part:
        return part, {}
    name, rest = part.split("{", 1)
    assert rest.endswith("}"), f"unterminated label set: {part}"
    body, labels = rest[:-1], {}
    i = 0
    while i < len(body):
        eq = body.index("=", i)
        key = body[i:eq].strip().strip('"')
        assert body[eq + 1] == '"', "label value must be double-quoted"
        j = eq + 2
        while j < len(body):  # 找未转义的收尾引号
            if body[j] == '"' and body[j - 1] != "\\":
                break
            j += 1
        assert j < len(body), "unterminated label value"
        labels[key] = unescape(body[eq + 2:j])
        i = j + 1
        while i < len(body) and body[i] in ", ":
            i += 1
    return name, labels


def parse_sample_line(line):
    """EBNF: metric_name_or_labels value [timestamp] —— 从右往左切最稳。"""
    tokens = line.split()
    has_ts = len(tokens) >= 3 and tokens[-1].lstrip("+-").isdigit()
    if has_ts:
        value = parse_value(tokens[-2])
        ts = int(tokens[-1])
        part = " ".join(tokens[:-2])
    else:
        value = parse_value(tokens[-1])
        ts = None
        part = " ".join(tokens[:-1])
    name, labels = parse_metric_part(part)
    return name, labels, value, ts


def parse_exposition(text):
    """主入口: 按指标名聚合为 MetricFamily{name,type,help,samples}。"""
    families = {}  # name -> dict
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue  # 空行忽略
        if line.startswith("#"):
            toks = line[1:].split(None, 2)
            if not toks:
                continue  # 普通注释
            if toks[0] == "HELP":
                fam = families.setdefault(toks[1], {"name": toks[1], "type": "untyped", "help": "", "samples": []})
                fam["help"] = unescape(toks[2]) if len(toks) > 2 else ""
            elif toks[0] == "TYPE":
                assert toks[2] in VALID_TYPES, f"bad TYPE {toks[2]}"
                fam = families.setdefault(toks[1], {"name": toks[1], "type": "untyped", "help": "", "samples": []})
                assert not fam["samples"], f"TYPE must appear before first sample of {toks[1]}"
                fam["type"] = toks[2]
            continue
        name, labels, value, ts = parse_sample_line(line)
        fam = families.setdefault(name, {"name": name, "type": "untyped", "help": "", "samples": []})
        fam["samples"].append({"labels": labels, "value": value, "ts": ts})
    return families


def check_histogram(families, name):
    """校验三条不变式: +Inf 存在 / == _count / 桶计数单调不减。"""
    fam = families[f"{name}_bucket"]
    buckets = sorted(
        ((s["labels"]["le"], s["value"]) for s in fam["samples"]),
        key=lambda t: (math.inf if t[0] == "+Inf" else float(t[0])),
    )
    print(f"  histogram '{name}' buckets:")
    for le, cnt in buckets:
        print(f"    le={le:<6} count={cnt}")
    assert buckets[-1][0] == "+Inf", "highest bucket must be +Inf"          # 校验 1
    assert buckets[-1][1] == families[f"{name}_count"]["samples"][0]["value"], \
        "+Inf bucket must equal <name>_count"                                 # 校验 2
    counts = [c for _, c in buckets]
    assert all(a <= b for a, b in zip(counts, counts[1:])), "cumulative buckets must be non-decreasing"  # 校验 3
    return buckets


def main():
    print("== demo 1: parse official-style exposition ==")
    fams = parse_exposition(EXPOSITION)
    for name in sorted(fams):
        f = fams[name]
        print(f"family={name:<34} type={f['type']:<9} samples={len(f['samples'])}")

    print("\n== demo 2: histogram invariants ==")
    check_histogram(fams, "http_request_duration_seconds")
    print("  all 3 invariants hold (has +Inf / == count / monotonic)")

    print("\n== demo 3: label escaping round-trip ==")
    s = fams["escapeme"]["samples"][0]
    want = 'a\nb " c'
    assert s["labels"]["label"] == want, f"unescaped mismatch: {s['labels']['label']!r}"
    print(f"  label value = {s['labels']['label']!r}  (real newline + quote survived)")

    print("\n== demo 4: missing TYPE -> untyped ==")
    assert fams["some_gauge"]["type"] == "untyped"
    print("  some_gauge type = untyped (spec: no TYPE line => untyped)")

    print("\n== demo 5: NaN / +Inf values ==")
    assert math.isnan(fams["weird_value"]["samples"][0]["value"])
    assert fams["another_inf"]["samples"][0]["value"] == math.inf
    print("  weird_value = NaN, another_inf = +Inf (valid per spec)")

    print("\nALL CHECKS PASSED")


if __name__ == "__main__":
    main()

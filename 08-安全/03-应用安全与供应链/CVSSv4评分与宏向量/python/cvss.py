"""CVSS v4.0 评分模型：宏向量(MacroVector) + 插值。

转写自官方参考实现 https://github.com/FIRSTdotorg/cvss-v4-calculator 的
`cvss_score.js`（评分与宏向量判定）、`cvss_lookup.js`（270 个宏向量得分）、
`max_composed.js`（每个宏向量的最高严重度向量）、`max_severity.js`（宏向量"深度"），
并对照 https://www.first.org/cvss/specification-document 的 §8。

核心事实：v4.0 **没有闭式公式**。专家组先把全部向量按"定性严重度相当"聚成
若干等价类（MacroVector），用查表给每个等价类一个分数，再在类内用
「严重度距离 / 深度」的比例做插值。
"""

import math

from cvssdata import (
    BASE_METRICS, DEPTH, EQ1_MAX, EQ2_MAX, EQ3EQ6_MAX, EQ4_MAX, EQ5_MAX,
    LEVELS, LOOKUP, ORDER, STEP, VALID,
)



class VectorError(ValueError):
    """向量串不合法（前缀错、必填缺失、取值越界、重名）。"""


def parse_vector(text):
    """把 `CVSS:4.0/AV:N/...` 解析成完整字典，未给出的可选指标填 X。"""
    text = text.strip()
    if not text.startswith("CVSS:4.0"):
        raise VectorError("向量串必须以 CVSS:4.0 开头: %r" % text[:20])
    body = text[len("CVSS:4.0"):].lstrip("/")
    sel = {name: "X" for name in VALID}
    seen = set()
    for part in body.split("/"):
        if not part:
            continue
        if ":" not in part:
            raise VectorError("片段缺少冒号: %r" % part)
        name, value = part.split(":", 1)
        if name not in VALID:
            raise VectorError("未知指标: %r" % name)
        if name in seen:
            raise VectorError("指标重复: %r" % name)
        seen.add(name)
        if value not in VALID[name]:
            raise VectorError("%s 的取值 %r 不合法（合法值 %s）"
                              % (name, value, "/".join(VALID[name])))
        sel[name] = value
    for m in BASE_METRICS:
        if sel[m] == "X":
            raise VectorError("基础指标 %s 必填" % m)
    return sel


def format_vector(sel):
    """按官方顺序输出向量串，取值是 X 的省略（与计算器的 `vector` 一致）。"""
    out = ["CVSS:4.0"]
    for name in ORDER:
        if sel.get(name, "X") != "X":
            out.append("%s:%s" % (name, sel[name]))
    return "/".join(out)


def resolve(sel, metric):
    """官方 `m()`：解析"实际参与计算的取值"。

    三条默认规则（cvss_score.js 顶部注释）：
      - E:X  -> A（未定义取最坏情况）
      - CR/IR/AR:X -> H（同上）
      - 其余带 M 前缀的修正指标：不是 X 就覆盖基础值，是 X 就用基础值
    """
    value = sel[metric]
    if metric == "E" and value == "X":
        return "A"
    if metric in ("CR", "IR", "AR") and value == "X":
        return "H"
    modified = sel.get("M" + metric)
    if modified is not None and modified != "X":
        return modified
    return value


def _eq1(sel):
    av, pr, ui = resolve(sel, "AV"), resolve(sel, "PR"), resolve(sel, "UI")
    if av == "N" and pr == "N" and ui == "N":
        return 0
    if (av == "N" or pr == "N" or ui == "N") and not (av == "N" and pr == "N" and ui == "N") \
            and av != "P":
        return 1
    if av == "P" or not (av == "N" or pr == "N" or ui == "N"):
        return 2
    raise AssertionError("EQ1 不可达")


def _eq2(sel):
    return 0 if (resolve(sel, "AC") == "L" and resolve(sel, "AT") == "N") else 1


def _eq3(sel):
    vc, vi, va = resolve(sel, "VC"), resolve(sel, "VI"), resolve(sel, "VA")
    if vc == "H" and vi == "H":
        return 0
    if (vc == "H" or vi == "H" or va == "H"):
        return 1
    return 2


def _msi_msa(sel):
    """MSI/MSA 为 X 时按规范回落到 SI/SA（规范 Table 15）。"""
    msi = sel.get("MSI", "X")
    msa = sel.get("MSA", "X")
    if msi == "X":
        msi = sel["SI"]
    if msa == "X":
        msa = sel["SA"]
    return msi, msa


def _eq4(sel):
    msi, msa = _msi_msa(sel)
    if msi == "S" or msa == "S":
        return 0
    sc, si, sa = resolve(sel, "SC"), resolve(sel, "SI"), resolve(sel, "SA")
    if sc == "H" or si == "H" or sa == "H":
        return 1
    return 2


def _eq5(sel):
    e = resolve(sel, "E")
    return {"A": 0, "P": 1, "U": 2}[e]


def _eq6(sel):
    cr, ir, ar = resolve(sel, "CR"), resolve(sel, "IR"), resolve(sel, "AR")
    vc, vi, va = resolve(sel, "VC"), resolve(sel, "VI"), resolve(sel, "VA")
    if (cr == "H" and vc == "H") or (ir == "H" and vi == "H") or (ar == "H" and va == "H"):
        return 0
    return 1


def macrovector(sel):
    """六个等价类的层级拼成 6 位字符串，顺序 EQ1 EQ2 EQ3 EQ4 EQ5 EQ6。"""
    return "%d%d%d%d%d%d" % (_eq1(sel), _eq2(sel), _eq3(sel), _eq4(sel), _eq5(sel), _eq6(sel))


def _metric_of(token, name):
    """从 "AV:N/PR:N/UI:N/" 这类片段里取出 AV 的取值。"""
    idx = token.find(name + ":")
    if idx < 0:
        return None
    rest = token[idx + len(name) + 1:]
    slash = rest.find("/")
    return rest[:slash] if slash >= 0 else rest


def _pick_max_vector(sel, mv):
    """在"各 EQ 最高严重度向量"的笛卡尔积里挑第一个能支配当前向量的。"""
    e1, e2, e3, e4, e5, e6 = (int(c) for c in mv)
    for a in EQ1_MAX[e1]:
        for b in EQ2_MAX[e2]:
            for c in EQ3EQ6_MAX[e3][e6]:
                for d in EQ4_MAX[e4]:
                    for e in EQ5_MAX[e5]:
                        cand = a + b + c + d + e
                        ok = True
                        for name in LEVELS:
                            want = _metric_of(cand, name)
                            if want is None:
                                continue
                            got = resolve(sel, name)
                            if LEVELS[name][got] - LEVELS[name][want] < 0:
                                ok = False
                                break
                        if ok:
                            return cand
    raise AssertionError("没有能支配当前向量的最高严重度向量")


def _nan():
    return float("nan")


def score(sel):
    """CVSS v4.0 基础/威胁/环境合并分数（官方算法逐行转写）。"""
    # 短路：所有影响指标都是 N 时直接 0 分（官方 `Exception for no impact`）
    if all(resolve(sel, m) == "N" for m in ("VC", "VI", "VA", "SC", "SI", "SA")):
        return 0.0
    mv = macrovector(sel)
    value = LOOKUP[mv]
    e1, e2, e3, e4, e5, e6 = (int(c) for c in mv)

    # 各 EQ 的"下一个更低宏向量"得分；不存在就是 NaN，后面整项忽略
    def lower_score(key):
        v = LOOKUP.get(key)
        return _nan() if v is None else v

    s1 = lower_score("%d%d%d%d%d%d" % (e1 + 1, e2, e3, e4, e5, e6))
    s2 = lower_score("%d%d%d%d%d%d" % (e1, e2 + 1, e3, e4, e5, e6))
    s4 = lower_score("%d%d%d%d%d%d" % (e1, e2, e3, e4 + 1, e5, e6))
    s5 = lower_score("%d%d%d%d%d%d" % (e1, e2, e3, e4, e5 + 1, e6))
    if (e3, e6) == (0, 0):
        # 两个方向都行，官方取分数更高的那个
        left = lower_score("%d%d%d%d%d%d" % (e1, e2, e3, e4, e5, e6 + 1))
        right = lower_score("%d%d%d%d%d%d" % (e1, e2, e3 + 1, e4, e5, e6))
        s3 = left if left > right else right
    elif (e3, e6) == (1, 1):
        s3 = lower_score("%d%d%d%d%d%d" % (e1, e2, e3 + 1, e4, e5, e6))
    elif (e3, e6) == (0, 1):
        s3 = lower_score("%d%d%d%d%d%d" % (e1, e2, e3 + 1, e4, e5, e6))
    elif (e3, e6) == (1, 0):
        s3 = lower_score("%d%d%d%d%d%d" % (e1, e2, e3, e4, e5, e6 + 1))
    else:
        # (2,1)：下一级是 (3,2)，不存在
        s3 = _nan()

    cand = _pick_max_vector(sel, mv)
    dist1 = sum(LEVELS[m][resolve(sel, m)] - LEVELS[m][_metric_of(cand, m)]
                for m in ("AV", "PR", "UI"))
    dist2 = sum(LEVELS[m][resolve(sel, m)] - LEVELS[m][_metric_of(cand, m)]
                for m in ("AC", "AT"))
    dist3 = sum(LEVELS[m][resolve(sel, m)] - LEVELS[m][_metric_of(cand, m)]
                for m in ("VC", "VI", "VA", "CR", "IR", "AR"))
    dist4 = sum(LEVELS[m][resolve(sel, m)] - LEVELS[m][_metric_of(cand, m)]
                for m in ("SC", "SI", "SA"))

    depth1 = DEPTH["eq1"][e1] * STEP
    depth2 = DEPTH["eq2"][e2] * STEP
    depth3 = DEPTH["eq3eq6"][e3][e6] * STEP
    depth4 = DEPTH["eq4"][e4] * STEP

    total = 0.0
    count = 0
    for available, dist, depth, forced_zero in (
            (value - s1, dist1, depth1, False),
            (value - s2, dist2, depth2, False),
            (value - s3, dist3, depth3, False),
            (value - s4, dist4, depth4, False),
            (value - s5, 0.0, DEPTH["eq5"][e5] * STEP, True),
    ):
        if math.isnan(available):
            continue
        count += 1
        # EQ5 官方把比例恒定为 0：威胁成熟度改的是宏向量分数，不参与插值
        total += 0.0 if forced_zero else available * (dist / depth)

    mean_distance = 0.0 if count == 0 else total / count
    value -= mean_distance
    if value < 0:
        value = 0.0
    if value > 10:
        value = 10.0
    return math.floor(value * 10 + 0.5) / 10.0


def severity(score_value):
    """官方定性分级（计算器 `qualScore`）：None / Low / Medium / High / Critical。"""
    if score_value == 0:
        return "None"
    if score_value < 4.0:
        return "Low"
    if score_value < 7.0:
        return "Medium"
    if score_value < 9.0:
        return "High"
    return "Critical"


def score_vector(text):
    return score(parse_vector(text))

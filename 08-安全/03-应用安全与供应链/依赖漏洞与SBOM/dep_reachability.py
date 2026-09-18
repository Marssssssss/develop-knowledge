#!/usr/bin/env python3
"""依赖漏洞判定：SemVer 区间匹配 → 传递闭包 → **可达性**剪枝 → SPDX 风格 SBOM。

为什么需要三步而不是一步：
  1. 版本区间匹配是**纯语法**判定，只要解析出来的版本落在 CVE 的影响区间就告警；
  2. 传递闭包告诉你哪些包**真的进了产物**（声明了但没被解析进来的不算）；
  3. 可达性才是**语义**判定 —— CVE 描述的是某个函数有洞，只有从程序入口
     能沿着调用图走到那个函数，这个 CVE 才可能被触发。

未做可达性剪枝的工具会报出大量「装了但调不到」的漏洞，这正是告警疲劳的主要来源。
"""

import hashlib
from typing import Dict, List, Optional, Tuple

# ------------------------------------------------------------------ SemVer

Version = Tuple[int, int, int, Tuple, str]


def parse_semver(s: str) -> Version:
    """按 semver.org 2.0.0 解析。返回 (major, minor, patch, prerelease_ids, build)。

    规范要点：X/Y/Z 为非负整数且**不得有前导零**；build metadata 不参与优先级比较。
    """
    core, pre, build = s, "", ""
    if "+" in s:
        core, build = s.split("+", 1)
    if "-" in core:
        core, pre = core.split("-", 1)
    parts = core.split(".")
    if len(parts) != 3:
        raise ValueError("bad semver core: %r" % s)
    nums = []
    for p in parts:
        if not p.isdigit():
            raise ValueError("non-numeric: %r" % s)
        if len(p) > 1 and p[0] == "0":
            raise ValueError("leading zero: %r" % s)
        nums.append(int(p))
    pre_ids = tuple(pre.split(".")) if pre else ()
    return (nums[0], nums[1], nums[2], pre_ids, build)


def _cmp_pre(a: Tuple, b: Tuple) -> int:
    """semver.org §11.4：预发布标识符逐个比较。

    纯数字按数值比；含字母/连字符按 ASCII 字典序；**数字标识符优先级低于非数字**；
    前面都相等时，标识符集合更大的优先级更高。空集（正式版）优先级最高。
    """
    if not a and not b:
        return 0
    if not a:
        return 1    # 1.0.0 > 1.0.0-alpha
    if not b:
        return -1
    for x, y in zip(a, b):
        xn, yn = x.isdigit(), y.isdigit()
        if xn and yn:
            c = (int(x) > int(y)) - (int(x) < int(y))
        elif xn != yn:
            c = -1 if xn else 1          # 数字 < 非数字
        else:
            c = (x > y) - (x < y)        # ASCII 字典序
        if c:
            return c
    return (len(a) > len(b)) - (len(a) < len(b))


def cmp_semver(a: Version, b: Version) -> int:
    """只看 major/minor/patch/prerelease；**build metadata 不参与比较**（规范 §10）。"""
    for i in range(3):
        if a[i] != b[i]:
            return -1 if a[i] < b[i] else 1
    return _cmp_pre(a[3], b[3])


def v(s: str) -> Version:
    return parse_semver(s)


# -------------------------------------------------------------- 区间与解析

def caret_range(s: str) -> List[Tuple[str, Version]]:
    """^X.Y.Z —— 常见误解区。

    规范 §4 明写：major 为 0 时（0.y.z）是**初始开发期**，任何东西都可能变，
    公开 API 不应视为稳定。因此：
        ^1.2.3 -> >=1.2.3 <2.0.0     （只锁 major）
        ^0.2.3 -> >=0.2.3 <0.3.0     （0.x 时锁到 minor，因为 minor 就可能是破坏性变更）
        ^0.0.3 -> >=0.0.3 <0.0.4
    """
    p = parse_semver(s)
    lo = (p[0], p[1], p[2], (), "")
    if p[0] > 0:
        hi = (p[0] + 1, 0, 0, (), "")
    elif p[1] > 0:
        hi = (0, p[1] + 1, 0, (), "")
    else:
        hi = (0, 0, p[2] + 1, (), "")
    return [(">=", lo), ("<", hi)]


def tilde_range(s: str) -> List[Tuple[str, Version]]:
    """~X.Y.Z：允许 patch 级变动；只写 ~X.Y 时允许 minor 级；只写 ~X 时允许 major 级。"""
    core = s.split("+")[0].split("-")[0]
    parts = core.split(".")
    nums = [int(x) for x in parts]
    while len(nums) < 3:
        nums.append(0)
    lo = (nums[0], nums[1], nums[2], (), "")
    if len(parts) >= 2:
        hi = (nums[0], nums[1] + 1, 0, (), "")
    else:
        hi = (nums[0] + 1, 0, 0, (), "")
    return [(">=", lo), ("<", hi)]


def parse_range(spec: str) -> List[Tuple[str, Version]]:
    """支持 "^x.y.z" / "~x.y.z" / 以及 ">=a <b" 这类空白分隔的操作符列表。"""
    spec = spec.strip()
    if spec.startswith("^"):
        return caret_range(spec[1:])
    if spec.startswith("~"):
        return tilde_range(spec[1:])
    out = []
    for tok in spec.split():
        for op in (">=", "<=", ">", "<", "=="):
            if tok.startswith(op):
                out.append((op, parse_semver(tok[len(op):])))
                break
        else:
            out.append(("==", parse_semver(tok)))
    return out


def satisfies(ver: Version, cons: List[Tuple[str, Version]]) -> bool:
    for op, ref in cons:
        c = cmp_semver(ver, ref)
        if op == ">=" and not c >= 0:
            return False
        if op == ">" and not c > 0:
            return False
        if op == "<=" and not c <= 0:
            return False
        if op == "<" and not c < 0:
            return False
        if op == "==" and c != 0:
            return False
    return True


# ---------------------------------------------------------------- 依赖解析

from dep_fixture import REGISTRY, DEPS, ROOT, CALLGRAPH, ENTRY, CVES  # noqa: F401

# 包 -> 可用版本列表
def resolve() -> Dict[str, str]:
    """解析传递闭包：同名包的所有区间取交集，取满足交集的**最高**可用版本。

    广度推进：先把根约束收进来，再逐层把新包的依赖约束并入（真实解析器还要回溯）。
    """
    cons: Dict[str, List[Tuple[str, Version]]] = {
        n: list(parse_range(s)) for n, s in ROOT[1].items()}
    frontier = list(ROOT[1].keys())
    done = set()
    while frontier:
        name = frontier.pop(0)
        if name in done:
            continue
        done.add(name)
        ver = _fmt(_pick(name, cons[name]))       # 查 DEPS 要用字符串版本
        for dn, dspec in DEPS[(name, ver)].items():
            cons.setdefault(dn, []).extend(parse_range(dspec))
            frontier.append(dn)
    return {n: _fmt(_pick(n, c)) for n, c in cons.items()}


def _pick(name: str, c: List[Tuple[str, Version]]) -> Version:
    cands = [parse_semver(x) for x in REGISTRY.get(name, [])]
    ok = [x for x in cands if satisfies(x, c)]
    if not ok:
        raise ValueError("no version of %s satisfies %r" % (name, c))
    best = ok[0]
    for x in ok[1:]:
        if cmp_semver(x, best) > 0:
            best = x
    return best


def _fmt(p: Version) -> str:
    s = "%d.%d.%d" % (p[0], p[1], p[2])
    if p[3]:
        s += "-" + ".".join(p[3])
    if p[4]:
        s += "+" + p[4]
    return s


def reachable_from(entry: str, graph: Dict[str, List[str]]) -> set:
    seen, stack = set(), [entry]
    while stack:
        f = stack.pop()
        if f in seen:
            continue
        seen.add(f)
        stack.extend(graph.get(f, []))
    return seen


def scan(resolved: Dict[str, str]) -> List[dict]:
    """三步判定：版本命中 → 装进产物 → 可达。返回每条 CVE 的判定明细。"""
    reach = reachable_from(ENTRY, CALLGRAPH)
    out = []
    for cid, pkg, spec, fn in CVES:
        ver = resolved.get(pkg)
        by_version = ver is not None and satisfies(v(ver), parse_range(spec))
        out.append({
            "cve": cid, "pkg": pkg, "version": ver,
            "by_version": bool(by_version),
            "reachable": bool(by_version) and fn in reach,
            "symbol": fn,
        })
    return out


# --------------------------------------------------------------------- SBOM

def sbom(resolved: Dict[str, str]) -> dict:
    """产出 SPDX 2.3 风格文档骨架（字段与关系类型取自 SPDX 规范目录）。"""
    def purl(name, version):
        return "pkg:generic/%s@%s" % (name, version)

    def sha(s):
        return hashlib.sha256(s.encode()).hexdigest()

    root_id = "SPDXRef-Package-%s" % ROOT[0]
    doc = {
        "spdxVersion": "SPDX-2.3",
        "SPDXID": "SPDXRef-DOCUMENT",
        "documentDescribes": [root_id],
        "packages": [{
            "SPDXID": root_id,
            "name": ROOT[0],
            "versionInfo": "1.0.0",
            "checksums": [{"algorithm": "SHA256", "value": sha(ROOT[0])}],
        }],
        "relationships": [{"spdxElementId": "SPDXRef-DOCUMENT",
                           "relationshipType": "DESCRIBES",
                           "relatedSpdxElement": root_id}],
    }
    for name, version in sorted(resolved.items()):
        pid = "SPDXRef-Package-%s" % name
        doc["packages"].append({
            "SPDXID": pid,
            "name": name,
            "versionInfo": version,
            # externalRefs 里的 PURL 用于跨仓库唯一定位组件
            "externalRefs": [{"referenceType": "purl", "referenceLocator": purl(name, version)}],
            "checksums": [{"algorithm": "SHA256", "value": sha(name + version)}],
        })
        doc["relationships"].append({"spdxElementId": root_id,
                                     "relationshipType": "CONTAINS",
                                     "relatedSpdxElement": pid})
        for dn in DEPS.get((name, version), {}):
            doc["relationships"].append({"spdxElementId": pid,
                                         "relationshipType": "DEPENDS_ON",
                                         "relatedSpdxElement": "SPDXRef-Package-%s" % dn})
    return doc


if __name__ == "__main__":
    r = resolve()
    print("resolved (%d):" % len(r))
    for k in sorted(r):
        print("  %-10s %s" % (k, r[k]))
    print()
    print("%-14s %-9s %-7s %-11s %s" % ("CVE", "pkg", "ver", "by_version", "reachable"))
    for row in scan(r):
        print("%-14s %-9s %-7s %-11s %s" % (
            row["cve"], row["pkg"], row["version"], row["by_version"], row["reachable"]))

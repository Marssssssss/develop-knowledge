# -*- coding: utf-8 -*-
"""Terraform 声明式状态重构模型：moved / removed / import 块的解析与计划期地址搬迁。

口径全部来自实际读过的官方原文（见 README 参考资料）：
  - Refactor modules：moved 语义、整资源 vs 实例级寻址、模块拆分、move 链、删除 moved 是破坏性变更
  - moved block reference：from/to 均为 address，plan 前先查 state
  - removed block reference：默认 destroy=true（从 state 移除并销毁真实资源），destroy=false 只移出 state
  - import block reference：to/id/identity/for_each；to 必须匹配已有 resource 块地址
  - How Terraform Works With Plugins 不涉及本节
"""
from __future__ import annotations


def _bracket(key):
    """count 索引是裸数字，for_each 键带引号 —— Terraform 地址的字面写法。"""
    if key is None:
        return ""
    if key.lstrip("-").isdigit():
        return "[%s]" % key
    return '["%s"]' % key


class Addr:
    """资源/模块地址。modules=[(name, key)], key 为 None 表示未带实例键。"""

    __slots__ = ("modules", "rtype", "rname", "key", "is_data")

    def __init__(self, modules, rtype=None, rname=None, key=None, is_data=False):
        self.modules = list(modules)
        self.rtype = rtype
        self.rname = rname
        self.key = key
        self.is_data = is_data

    # ---- 判定 ----
    @property
    def is_module_only(self) -> bool:
        return self.rtype is None

    @property
    def has_any_key(self) -> bool:
        return self.key is not None or any(k is not None for _, k in self.modules)

    def __str__(self) -> str:
        out = "".join("module.%s%s." % (n, _bracket(k)) for n, k in self.modules)
        if self.rtype is None:
            return out.rstrip(".")
        head = "data." if self.is_data else ""
        return "%s%s%s.%s%s" % (out, head, self.rtype, self.rname, _bracket(self.key))


def _split_top(s: str):
    """按顶层 '.' 切分，尊重 [] 嵌套与引号内的点。"""
    parts, buf, depth, quote = [], [], 0, None
    for ch in s:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            buf.append(ch)
            continue
        if ch == "[":
            depth += 1
            buf.append(ch)
            continue
        if ch == "]":
            depth -= 1
            buf.append(ch)
            continue
        if ch == "." and depth == 0:
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    parts.append("".join(buf))
    return [p for p in parts if p != ""]


def _split_key(seg: str):
    """'a[2]' -> ('a','2')，'a' -> ('a',None)，键内引号剥除。"""
    i = seg.find("[")
    if i < 0:
        return seg, None
    name = seg[:i]
    key = seg[i + 1:seg.rfind("]")].strip()
    if len(key) >= 2 and key[0] == key[-1] and key[0] in "\"'":
        key = key[1:-1]
    return name, (key if key != "" else None)


def parse_addr(s: str) -> Addr:
    parts = _split_top(s.strip())
    modules, i = [], 0
    # "module" 与其名字之间的点位于方括号之外，会被 _split_top 切开，这里成对取回
    while i + 1 < len(parts) and parts[i] == "module":
        modules.append(_split_key(parts[i + 1]))
        i += 2
    rest = parts[i:]
    if not rest:
        return Addr(modules)
    is_data = False
    if rest[0] == "data" and len(rest) >= 3:
        is_data = True
        rest = rest[1:]
    if len(rest) != 2:
        raise ValueError("无法解析地址: %r" % s)
    name, key = _split_key(rest[1])
    return Addr(modules, rest[0], name, key, is_data)


def _mod_prefix_matches(addr: Addr, prefix) -> bool:
    if len(addr.modules) < len(prefix):
        return False
    for (n1, k1), (n2, k2) in zip(addr.modules, prefix):
        if n1 != n2 or k1 != k2:
            return False
    return True


def _rewrite(addr: Addr, frm: Addr, to: Addr, instance_level: bool) -> Addr:
    """把 addr 中匹配 frm 的部分替换成 to。

    关键差别在实例键：整资源级搬迁**保留**原实例键（a[0]→b[0]）；
    实例级搬迁则以 to 的键为准，to 不带键时**丢掉**键（d[2]→d）。
    """
    if frm.is_module_only:
        return Addr(to.modules + addr.modules[len(frm.modules):],
                    addr.rtype, addr.rname,
                    to.key if instance_level else addr.key, addr.is_data)
    return Addr(to.modules, to.rtype, to.rname,
                to.key if instance_level else addr.key, to.is_data)


class Move:
    def __init__(self, frm: str, to: str):
        self.frm = parse_addr(frm)
        self.to = parse_addr(to)
        if self.frm.is_data != self.to.is_data:
            raise ValueError("moved 不能在托管资源与 data 资源之间搬迁: %s -> %s" % (frm, to))

    @property
    def instance_level(self) -> bool:
        return self.frm.has_any_key or self.to.has_any_key

    def matches(self, addr: Addr) -> bool:
        f = self.frm
        if f.is_module_only:
            if not _mod_prefix_matches(addr, f.modules):
                return False
            if self.instance_level:
                return len(addr.modules) == len(f.modules) and addr.key == f.key
            return True
        if self.instance_level:
            # 至少一侧带键 → 两侧都按「具体实例」理解：地址必须整体相等
            return str(addr) == str(f)
        if not _mod_prefix_matches(addr, f.modules) or len(addr.modules) != len(f.modules):
            return False
        return (addr.rtype == f.rtype and addr.rname == f.rname
                and addr.is_data == f.is_data)


def apply_moves(state: dict, moves) -> dict:
    """按配置顺序依次应用 moved 块，返回搬迁后的 state（addr_str -> obj）。"""
    cur = {str(parse_addr(k)) if isinstance(k, str) else str(k): v for k, v in state.items()}
    for mv in moves:
        nxt = {}
        for s, obj in cur.items():
            a = parse_addr(s)
            nxt[str(_rewrite(a, mv.frm, mv.to, mv.instance_level)) if mv.matches(a) else s] = obj
        cur = nxt
    return cur


def auto_count_move(state: dict, config: dict, moves) -> dict:
    """给原本单实例的资源加 count 时，未显式写 moved 的自动搬到实例 0。"""
    mentioned = set()
    for mv in moves:
        if not mv.frm.is_module_only:
            mentioned.add((tuple(map(tuple, mv.frm.modules)), mv.frm.rtype, mv.frm.rname))
        if not mv.to.is_module_only:
            mentioned.add((tuple(map(tuple, mv.to.modules)), mv.to.rtype, mv.to.rname))
    cur = dict(state)
    for res, spec in config.items():
        if spec.get("mode") != "count":
            continue
        a = parse_addr(res)
        ident = (tuple(map(tuple, a.modules)), a.rtype, a.rname)
        if ident in mentioned:
            continue
        plain = str(Addr(a.modules, a.rtype, a.rname))
        if plain in cur:
            cur[plain + "[0]"] = cur.pop(plain)
    return cur


def desired_addresses(config: dict) -> list:
    """返回 [(address, desired_attrs)]，按配置书写顺序展开 count/for_each。"""
    out = []
    for res, spec in config.items():
        a = parse_addr(res)
        mode = spec.get("mode")
        attrs = spec.get("attrs", {})
        if mode == "count":
            keys = [str(i) for i in range(spec["n"])]
        elif mode == "for_each":
            keys = [str(k) for k in spec["keys"]]
        else:
            keys = [None]
        for k in keys:
            out.append((str(Addr(a.modules, a.rtype, a.rname, k, a.is_data)), attrs))
    return out


def plan(state: dict, config: dict, moves=(), removed=(), imports=()):
    """返回 [(address, action)]，action ∈ create/import/noop/update/destroy/forget。"""
    removed_map = {}
    for r in removed:
        removed_map[str(parse_addr(r["from"]))] = bool(r.get("destroy", True))
    imp = {}
    for i in imports:
        to = str(parse_addr(i["to"]))
        if "id" in i and "identity" in i:
            raise ValueError("import 的 id 与 identity 互斥: %s" % i["to"])
        imp[to] = i

    cur = apply_moves(state, moves)
    cur = auto_count_move(cur, config, moves)
    desired = desired_addresses(config)

    dset = set(addr for addr, _ in desired)
    for to in imp:
        if to not in dset:
            raise ValueError("import 的 to 必须匹配已有 resource 块地址: %s" % to)

    out = []
    for addr, attrs in desired:
        dset.add(addr)
        if addr in cur:
            out.append((addr, "noop" if cur[addr] == attrs else "update"))
        elif addr in imp:
            out.append((addr, "import"))
        else:
            out.append((addr, "create"))
    for addr in cur:
        if addr in dset:
            continue
        if addr in removed_map:
            out.append((addr, "destroy" if removed_map[addr] else "forget"))
        else:
            out.append((addr, "destroy"))
    return out

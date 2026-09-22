#!/usr/bin/env python3
"""PURL 的解析与规范化（package-url/purl-spec 第 5 章 + How to build a PURL）。

规范要点（逐条对应 purl_c5.md / purl_build.md）：

* scheme 恒为 ``pkg``，其后允许出现一个或多个 ``/``，解析时应剥离
* type 大小写不敏感，规范形式是**小写**，且**不做百分号编码**
* namespace / name / version / subpath 段都需要百分号编码
* 百分号编码的豁免集 = 字母数字 + ``.-_~`` + ``:``；空格编码成 ``%20``
* qualifier 的 **key 小写**、**空值丢弃**、整体按 ``key=value`` 字符串**字典序排序**
* subpath 丢弃空段、``.`` 与 ``..``
* version 是「不透明字符串」，规范不做任何归一化
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

# --------------------------------------------------------------------------
# 1. 百分号编码
# --------------------------------------------------------------------------

# 规范 5.4：豁免集为字母数字 + 标点符号 .-_~ ；此外冒号 ':' 无论何时都不编码
_ALLOWED = re.compile(r"[A-Za-z0-9._~\-:]")


def pct_encode(s: str) -> str:
    out = []
    for b in s.encode("utf-8"):
        ch = chr(b)
        if _ALLOWED.match(ch):
            out.append(ch)
        else:
            out.append("%%%02X" % b)
    return "".join(out)


def pct_decode(s: str) -> str:
    buf = bytearray()
    i = 0
    while i < len(s):
        if s[i] == "%" and i + 2 < len(s) + 1 and re.match(r"[0-9A-Fa-f]{2}", s[i + 1:i + 3] or ""):
            buf.append(int(s[i + 1:i + 3], 16))
            i += 3
        else:
            buf.extend(s[i].encode("utf-8"))
            i += 1
    return buf.decode("utf-8", "replace")


# --------------------------------------------------------------------------
# 2. PURL 数据结构
# --------------------------------------------------------------------------


class Purl:
    def __init__(self, type_: str, namespace: Optional[List[str]] = None,
                 name: str = "", version: str = "",
                 qualifiers: Optional[Dict[str, str]] = None,
                 subpath: Optional[List[str]] = None):
        self.type = type_
        self.namespace = list(namespace or [])
        self.name = name
        self.version = version
        self.qualifiers = dict(qualifiers or {})
        self.subpath = list(subpath or [])

    def __eq__(self, other: object) -> bool:
        return (isinstance(other, Purl) and self.type == other.type
                and self.namespace == other.namespace and self.name == other.name
                and self.version == other.version
                and self.qualifiers == other.qualifiers
                and self.subpath == other.subpath)

    def __repr__(self) -> str:
        return "Purl(%s,%s,%s,%s,%s,%s)" % (self.type, self.namespace, self.name,
                                            self.version, self.qualifiers, self.subpath)


def parse_purl(s: str) -> Purl:
    """按 purl-spec 的 how-to-parse 逆向拆解；只保留本 demo 需要的严格性。"""
    text = s.strip()
    if text.lower().startswith("pkg:"):
        text = text[4:]
    # 5.6.1: scheme 与 colon 之后的若干 '/' 应被忽略并移除
    text = text.lstrip("/")

    # 先切 subpath
    subpath: List[str] = []
    if "#" in text:
        text, sp = text.split("#", 1)
        subpath = [seg for seg in sp.split("/") if seg not in ("", ".", "..")]

    # 再切 qualifiers
    qualifiers: Dict[str, str] = {}
    if "?" in text:
        text, qs = text.split("?", 1)
        for pair in qs.split("&"):
            if not pair:
                continue
            k, _, v = pair.partition("=")
            if v == "":
                continue       # 空值等同于该 key 不存在
            qualifiers[pct_decode(k.lower())] = pct_decode(v)

    # 再切 version（只在最后一个 '@' 之前没有 '/' 时才是 version）
    version = ""
    m = re.match(r"^([^@]*)@(.*)$", text)
    if m:
        text, version = m.group(1), pct_decode(m.group(2))

    # 剩下的 type/namespace/name
    parts = [p for p in text.split("/") if p != ""]
    if not parts:
        raise ValueError("empty purl")
    type_ = parts[0].lower()          # 5.6.2: type 大小写不敏感，形式小写
    rest = parts[1:]
    if len(rest) >= 2:
        namespace = [pct_decode(p) for p in rest[:-1]]
        name = pct_decode(rest[-1])
    elif len(rest) == 1:
        namespace, name = [], pct_decode(rest[0])
    else:
        namespace, name = [], ""
    return Purl(type_, namespace, name, version, qualifiers, subpath)


def build_purl(p: Purl) -> str:
    """按 how-to-build 逐条构造规范形式。"""
    out = "pkg:" + p.type.lower() + "/"
    if p.namespace:
        out += "/".join(pct_encode(seg) for seg in p.namespace) + "/"
    out += pct_encode(p.name)
    if p.version:
        out += "@" + pct_encode(p.version)
    if p.qualifiers:
        pairs = []
        for k, v in p.qualifiers.items():
            if v == "":
                continue                       # 空值丢弃
            pairs.append(k.lower() + "=" + pct_encode(v))
        pairs.sort()                           # 按 key=value 字符串字典序
        if pairs:
            out += "?" + "&".join(pairs)
    if p.subpath:
        segs = [seg for seg in p.subpath if seg not in ("", ".", "..")]
        if segs:
            out += "#" + "/".join(pct_encode(s) for s in segs)
    return out


def canonical_purl(s: str) -> str:
    return build_purl(parse_purl(s))


def purl_key(s: str) -> Tuple[str, ...]:
    """跨格式关联用的规范化主键：同组件的两种写法必须落到同一 key。"""
    p = parse_purl(s)
    return (p.type, "/".join(p.namespace), p.name, p.version,
            tuple(sorted(p.qualifiers.items())), "/".join(p.subpath))

"""
从 AS_PATH 推「路由源 AS」（RFC 6811 §2 的定义）

RFC 6811 把 Route Origin ASN 定义为**最后一个 AS_PATH 段的类型**决定的结果，
而不是「最后出现的那个 AS 号」：

  - 最后一段是 AS_SEQUENCE      → 该段最右边的 AS
  - 最后一段是 AS_CONFED_SEQUENCE / AS_CONFED_SET，或 AS_PATH 为空
                                → 本机 AS 号（联邦内部的 AS 号不出现在对外路径里）
  - 最后一段是任何其它类型（如 AS_SET）→ 特殊值 NONE，**它永远不可能被 VRP 匹配**

顺带一提 AS_PATH 可以同时含 2 字节与 4 字节 AS 号（RFC 6793），所以解析出的
AS 号一律按整数处理，只在本文件里做范围校验。
"""

from __future__ import annotations

AS_SET = 1
AS_SEQUENCE = 2
AS_CONFED_SEQUENCE = 3
AS_CONFED_SET = 4

SEGMENT_NAMES = {
    AS_SET: "AS_SET",
    AS_SEQUENCE: "AS_SEQUENCE",
    AS_CONFED_SEQUENCE: "AS_CONFED_SEQUENCE",
    AS_CONFED_SET: "AS_CONFED_SET",
}

NONE = None          # 规范里的特殊值 NONE
AS_TRANS = 23456     # RFC 6793：2 字节实现用来代表 4 字节 AS 的保留值


def origin_asn(segments: list[tuple[int, list[int]]], local_asn: int) -> int | None:
    """segments 形如 [(AS_SEQUENCE, [65001, 65002]), ...]，顺序与线上一致（最近的在前）。"""
    if not segments:
        return local_asn
    seg_type, asns = segments[-1]
    if seg_type == AS_CONFED_SEQUENCE or seg_type == AS_CONFED_SET:
        return local_asn
    if seg_type != AS_SEQUENCE:
        return NONE
    if not asns:
        raise ValueError(f"空的 {SEGMENT_NAMES.get(seg_type, seg_type)} 段不是合法 AS_PATH")
    return asns[-1]


def asn_problems(asn: int) -> list[str]:
    """返回 AS 号的合法性说明；空列表表示合法。AS 0 是保留值（RFC 7607）。"""
    issues = []
    if asn < 0 or asn > 0xFFFFFFFF:
        issues.append("AS 号超出 4 字节范围")
    elif asn == 0:
        issues.append("AS 0 是保留值，不得作为源 AS 出现")
    elif asn == AS_TRANS:
        issues.append("AS_TRANS 只用于 2 字节实现中转，不应出现在验证通过的路径末尾")
    return issues


def as_path_text(segments: list[tuple[int, list[int]]]) -> str:
    """把段列表渲染成常见的 AS_PATH 文本形式，SET 用花括号。"""
    out = []
    for seg_type, asns in segments:
        body = " ".join(str(a) for a in asns)
        if seg_type in (AS_SET, AS_CONFED_SET):
            out.append("{" + body + "}")
        else:
            out.append(body)
    return " ".join(out)

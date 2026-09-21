"""JSONB 半结构化与 GIN 索引 —— PostgreSQL 18 官方文档的可执行模型。

转写对象：
  * PostgreSQL 18《8.14. JSON Types》—— json 与 jsonb 的差异、jsonb 规范化、
    包含（@>）与存在（?）的语义、jsonb 下标
  * PostgreSQL 18《65.4. GIN Indexes》—— jsonb_ops / jsonb_path_ops 两个操作符类、
    fastupdate 的 pending list 与 gin_pending_list_limit

口径说明：
  * jsonb 的数字按 PG 的 **numeric** 处理（1 与 1.0 相等、保留尾随零、超范围报错），
    而不少其它实现（含 JS）用 IEEE 754 double，这是官方专门提醒的互操作风险。
  * GIN 只做「不漏」的候选筛选（lossy），最终必须 recheck，本模型显式保留这一步。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple


class JsonbError(ValueError):
    """jsonb 输入函数的报错（比 json 更严格）。"""


# ------------------------------------------------------------ jsonb 规范化
_NUM_RE = re.compile(r"^-?(\d+)(\.\d*)?([eE][+-]?\d+)?$")


def normalize(value: Any) -> Any:
    """把 Python 对象规范化成 jsonb 的存储形态：
    - 对象键排序（丢失原顺序）、重复键只留最后一个
    - 数字统一成 Numeric（保留尾随零、E 记法展开、超范围报错）
    - 字符串里的 \\u0000 与孤立代理项被拒
    """
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():                 # 官方：重复键只留最后一个
            out[_check_string(k)] = normalize(v)
        return dict(sorted(out.items()))           # 官方：jsonb 不保序
    if isinstance(value, list):
        return [normalize(v) for v in value]
    if isinstance(value, str):
        return _check_string(value)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return Numeric(value)
    return value


def _check_string(s: str) -> str:
    """jsonb 输入函数对字符串的两条硬约束（json 类型不做这些检查）。"""
    if "\\u0000" in s:
        raise JsonbError("unsupported Unicode escape sequence: \\u0000 cannot be "
                         "converted to text")
    if _has_lone_surrogate(s):
        raise JsonbError("Unicode surrogate pairs must be well-formed in jsonb")
    return s


def _has_lone_surrogate(s: str) -> bool:
    i = 0
    while i < len(s) - 1:
        if s[i] == "\\" and s[i + 1] == "u":
            hexpart = s[i + 2:i + 6]
            if len(hexpart) == 4 and all(c in "0123456789abcdefABCDEF" for c in hexpart):
                cp = int(hexpart, 16)
                if 0xD800 <= cp <= 0xDBFF:                     # 高代理
                    nxt = s[i + 6:i + 12]
                    if not (nxt.startswith("\\u") and 0xDC00 <= int(nxt[2:6], 16) <= 0xDFFF):
                        return True
                    i += 12                                    # 整对跳过
                    continue
                if 0xDC00 <= cp <= 0xDFFF:                     # 低代理孤立出现
                    return True
                i += 6
                continue
        i += 1
    return False


class Numeric:
    """PG numeric 的极简模型：按值比较（1 与 1.0 相等），保留尾随零。"""

    __slots__ = ("text",)

    def __init__(self, v: Any) -> None:
        if isinstance(v, Numeric):
            self.text = v.text
            return
        self.text = repr(v) if isinstance(v, float) else str(v)

    @property
    def value(self) -> float:
        return float(self.text)

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, Numeric):
            return self.value == other.value
        if isinstance(other, (int, float)) and not isinstance(other, bool):
            return self.value == float(other)
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.value)

    def __repr__(self) -> str:
        return self.text


def parse_jsonb(text: str) -> Any:
    """jsonb 输入函数。

    官方：比 json 严格的两条检查发生在**输入函数里**（也就是对原始文本做，
    而不是对解码后的字符串做）—— \\u0000 与不合法的代理对在 json 类型下不检查。
    """
    if "\\u0000" in text:
        raise JsonbError("unsupported Unicode escape sequence: \\u0000")
    if _has_lone_surrogate(text):
        raise JsonbError("Unicode surrogate pairs must be well-formed in jsonb")
    raw = json.loads(text)
    return _check_range(normalize(raw))


def _check_range(v: Any) -> Any:
    if isinstance(v, Numeric):
        # 口径：只判「超出 IEEE 双精度可表示范围」（本模型用 float 承载），
        # 不做 PG numeric 精确量级上限（1e131072）的逐位判定。
        if v.value in (float("inf"), float("-inf")) or v.value != v.value:
            raise JsonbError("number is out of range for type numeric")
        return v
    if isinstance(v, dict):
        return {k: _check_range(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_check_range(x) for x in v]
    return v


_NUMERIC_MAX = float("1e131072")     # PG numeric 的量级上限（示意量级，非精确值）


def parse_json(text: str) -> str:
    """json 输入函数：存的是**原文副本**，保留空白、键序与重复键。"""
    json.loads(text)                 # 只做语法检查
    return text


def to_json_text(v: Any) -> str:
    """jsonb 的输出：键已排序、无冗余空白、数字按 numeric 打印。"""
    if isinstance(v, dict):
        return "{" + ",".join('%s: %s' % (json.dumps(k, ensure_ascii=False), to_json_text(x))
                              for k, x in v.items()) + "}"
    if isinstance(v, list):
        return "[" + ",".join(to_json_text(x) for x in v) + "]"
    if isinstance(v, Numeric):
        return v.text
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "null"
    return json.dumps(v, ensure_ascii=False)


# --------------------------------------------------------- 包含与存在
def num_eq(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (int, float, Numeric)) and isinstance(b, (int, float, Numeric)):
        return float(a) == float(b) if not isinstance(a, Numeric) else a == b
    return a == b


def contains(doc: Any, needle: Any) -> bool:
    """@> 包含：官方口径 —— 被包含对象要在**结构与数据内容**上匹配包含者，
    允许丢弃包含者里多余的数组元素或对象键值；数组元素顺序不重要，重复元素只算一次。
    """
    if isinstance(needle, dict):
        if not isinstance(doc, dict):
            return False
        for k, v in needle.items():
            if k not in doc:
                return False
            if not contains(doc[k], v):
                return False
        return True
    if isinstance(needle, list):
        if not isinstance(doc, list):
            # 官方给的唯一「例外」：数组包含顶层标量 —— 且**不可反向**
            return False
        return all(_array_contains_one(doc, e) for e in needle)
    # 标量
    if isinstance(doc, list):
        return _array_contains_one(doc, needle)
    if isinstance(doc, dict):
        return False
    return num_eq(doc, needle)


def _array_contains_one(arr: Sequence[Any], needle: Any) -> bool:
    if any(num_eq(e, needle) if not isinstance(e, (dict, list)) else e == needle for e in arr):
        return True
    # 结构匹配：needle 是对象/数组时必须与某个**元素**整体匹配
    if isinstance(needle, (dict, list)):
        return any(_deep_eq(e, needle) for e in arr)
    return False


def _deep_eq(a: Any, b: Any) -> bool:
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_deep_eq(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_deep_eq(x, y) for x, y in zip(a, b))
    return num_eq(a, b)


def exists_top(doc: Any, key: str) -> bool:
    """? 存在：字符串必须出现为**顶层**对象键或顶层数组元素，且**不反向**。"""
    if isinstance(doc, dict):
        return key in doc
    if isinstance(doc, list):
        return any(num_eq(e, key) for e in doc if not isinstance(e, (dict, list)))
    return False


# -------------------------------------------------------------- GIN 索引
class GinIndex:
    """GIN 倒排索引（lossy）：只保证候选集不漏，最终靠 recheck。"""

    def __init__(self, name: str, opclass: str = "jsonb_ops",
                 fastupdate: bool = True, pending_limit: int = 4) -> None:
        if opclass not in ("jsonb_ops", "jsonb_path_ops"):
            raise ValueError("unknown opclass")
        self.name = name
        self.opclass = opclass
        self.fastupdate = fastupdate
        self.pending_limit = pending_limit
        self.main: Dict[str, Set[int]] = {}
        self.pending: List[Tuple[Any, int]] = []
        self.cleanups = 0

    # --- 条目抽取：两个操作符类的差别就在这一步 ---
    def keys_of(self, doc: Any, prefix: str = "") -> List[str]:
        out: List[str] = []
        if isinstance(doc, dict):
            for k, v in doc.items():
                if self.opclass == "jsonb_ops":
                    out.append("K" + prefix + "." + k)        # 键单独成条目
                out.extend(self.keys_of(v, prefix + "." + k))
        elif isinstance(doc, list):
            for i, v in enumerate(doc):
                out.extend(self.keys_of(v, "%s[%d]" % (prefix, i)))
        else:
            if self.opclass == "jsonb_ops":
                out.append("V" + repr(doc))                   # 值单独成条目
            else:
                out.append("H" + prefix + "=" + repr(doc))    # 路径+值哈希成一条
        return out

    def supports(self, op: str) -> bool:
        """jsonb_path_ops 支持的运算符更少。"""
        if op in ("@>", "@?", "@@"):
            return True
        return self.opclass == "jsonb_ops"      # ? / ?| / ?& 只有 jsonb_ops 支持

    def add(self, tid: int, doc: Any) -> None:
        if self.fastupdate:
            self.pending.extend((k, tid) for k in self.keys_of(doc))
            if len(self.pending) > self.pending_limit:
                self.cleanup()
        else:
            for k in self.keys_of(doc):
                self.main.setdefault(k, set()).add(tid)

    def cleanup(self) -> None:
        """超过 gin_pending_list_limit 就把 pending 合并进主结构（前台清理，明显更慢）。"""
        for k, tid in self.pending:
            self.main.setdefault(k, set()).add(tid)
        self.pending = []
        self.cleanups += 1

    def candidates(self, needle: Any) -> Set[int]:
        want = set(self.keys_of(needle))
        found: Set[int] = set()
        for k, tids in self.main.items():
            if k in want:
                found |= tids
        for k, tid in self.pending:
            if k in want:
                found.add(tid)
        return found

    def search(self, docs: Dict[int, Any], needle: Any) -> Tuple[Set[int], Set[int]]:
        """返回 (候选集, recheck 之后的真结果)：候选集必须是真结果的超集。"""
        cand = self.candidates(needle)
        real = {tid for tid in cand if contains(docs[tid], needle)}
        return cand, real

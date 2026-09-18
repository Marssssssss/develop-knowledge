"""记录级变换与过滤:attributes processor 的六种动作 + filter processor。

只实现可被纯手写断言的子集:条件语言为「路径 运算符 字面量」,不含 OTTL 的
函数、布尔组合与括号。`error_mode` 的两种取值与官方一致:
  ignore    条件求值出错时该条记录原样放行
  propagate 出错即中断
"""

from __future__ import annotations

import hashlib
import re

from otel_config import ConfigError

# 极简 OTTL 子集:路径 运算符 字面量。
_COND_RE = re.compile(r"^([a-zA-Z0-9_.]+)\s*(==|!=|=~|!~|<=|>=|<|>)\s*(.+)$")


class AttributesProcessor:
    """insert / update / upsert / delete / hash / extract 六种动作。

    语义差异是这里最容易写错的地方:insert 不覆盖已有、update 不新增、
    upsert 总是写;extract 从 from_attribute 取值写到 key。
    """

    KNOWN = ("insert", "update", "upsert", "delete", "hash", "extract")

    def __init__(self, actions):
        for a in actions:
            if a.get("action") not in self.KNOWN:
                raise ConfigError("unknown attributes action: %r" % (a.get("action"),))
        self.actions = actions

    @staticmethod
    def hash_value(v):
        """官方用 SHA-1 替换原值,便于保留关联性而不落原始 PII。"""
        return hashlib.sha1(str(v).encode("utf-8")).hexdigest()

    def apply(self, rec):
        for a in self.actions:
            act = a["action"]
            key = a.get("key")
            if act == "insert":
                rec.attrs.setdefault(key, a.get("value"))
            elif act == "update":
                if key in rec.attrs:
                    rec.attrs[key] = a.get("value")
            elif act == "upsert":
                rec.attrs[key] = a.get("value")
            elif act == "delete":
                rec.attrs.pop(key, None)
            elif act == "hash":
                if key in rec.attrs:
                    rec.attrs[key] = self.hash_value(rec.attrs[key])
            elif act == "extract":
                src = a.get("from_attribute")
                if src in rec.attrs:
                    rec.attrs[key] = rec.attrs[src]
        return rec


def eval_condition(cond, rec):
    """求值单条条件;语法非法抛 ConfigError。

    属性缺失时的行为是明确的:比较类运算符一律不命中(`==` 为 False),
    因此 `!=` 对缺失属性会返回 True —— 与直觉相反,容易误删数据。
    """
    m = _COND_RE.match(cond.strip())
    if not m:
        raise ConfigError("无法解析的过滤条件: %s" % cond)
    key, op, rhs = m.group(1), m.group(2), m.group(3).strip()
    val = rec.attrs.get(key)
    if op in ("=~", "!~"):
        hit = re.fullmatch(rhs.strip("\"'"), "" if val is None else str(val)) is not None
        return hit if op == "=~" else not hit
    if op in ("==", "!="):
        hit = val is not None and str(val) == rhs.strip("\"'")
        return hit if op == "==" else not hit
    if val is None:
        return False
    num = float(rhs)
    return {"<": val < num, ">": val > num, "<=": val <= num, ">=": val >= num}[op]


class FilterProcessor:
    """任一条件命中即丢弃记录(OR 语义,与 OTTL 多条 condition 一致)。

    注意正则按整串匹配(等价 OTTL 的 matches 语义),前缀不算命中。
    """

    def __init__(self, conditions, error_mode="ignore"):
        if error_mode not in ("ignore", "propagate"):
            raise ConfigError("error_mode 只能取 ignore / propagate")
        self.conditions = list(conditions)
        self.error_mode = error_mode

    def matches(self, rec):
        for c in self.conditions:
            try:
                if eval_condition(c, rec):
                    return True
            except ConfigError:
                if self.error_mode == "propagate":
                    raise
        return False

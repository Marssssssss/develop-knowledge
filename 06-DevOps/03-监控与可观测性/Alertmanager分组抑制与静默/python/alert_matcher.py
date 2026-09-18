"""告警匹配与分组:matcher 语义、fingerprint、group key。

matcher 语言来自 Alertmanager 官方配置文档:`key="value"` / `key=~"regex"` /
`key!="value"` / `key!~"regex"`,**没有 IN 操作符** —— 多值匹配只能写成锚定正则,
写成 `team="payments"` 试图匹配 `team="payments-eu"` 会静默失配。

缺失标签在匹配中取**空字符串**,这是本文件最重要的语义:
  - `env="prod"` 对没有 env 的告警 → 不命中
  - `env!="prod"` 对没有 env 的告警 → **命中**(空 ≠ prod)
  - `env=~".*"` 对没有 env 的告警 → 命中(空串匹配 .*)
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

OPERATORS = ("=", "!=", "=~", "!~")

# 特殊值:group_by 只含它时表示"按所有标签聚合",即不聚合、每条告警单独一组
GROUP_BY_ALL = "..."

_MATCHER_RE = re.compile(r'^([a-zA-Z_][a-zA-Z0-9_]*)\s*(=~|!~|!=|=)\s*"([^"]*)"$')


class MatcherError(Exception):
    """matcher 语法非法。"""


@dataclass(frozen=True)
class Matcher:
    key: str
    op: str
    value: str

    def __post_init__(self):
        if self.op not in OPERATORS:
            raise MatcherError("非法运算符: %r" % (self.op,))

    def matches(self, labels):
        """缺失标签按空字符串参与匹配。"""
        actual = labels.get(self.key, "")
        if self.op in ("=", "!="):
            hit = actual == self.value
            return hit if self.op == "=" else not hit
        # 官方对 =~ 做完全锚定:"a|b" 等价 "^(?:a|b)$",前缀不算命中
        hit = re.fullmatch(self.value, actual) is not None
        return hit if self.op == "=~" else not hit

    def __str__(self):
        return '%s%s"%s"' % (self.key, self.op, self.value)


def parse_matcher(text):
    """`severity="critical"` -> Matcher。两边的引号必需,与官方语法一致。"""
    m = _MATCHER_RE.match(text.strip())
    if not m:
        raise MatcherError("无法解析的 matcher: %r(需要 key\"op\"value 且带引号)" % (text,))
    return Matcher(m.group(1), m.group(2), m.group(3))


def parse_matchers(texts):
    return [parse_matcher(t) for t in texts]


def labels_match(matchers, labels):
    """所有 matcher 必须命中(AND 语义)。"""
    return all(m.matches(labels) for m in matchers)


def label_signature(labels):
    """稳定可读的标签签名,用于调试与 group 显示。"""
    return "{" + ", ".join("%s=%s" % (k, labels[k]) for k in sorted(labels)) + "}"


def fingerprint(labels):
    """告警指纹:标签集排序后取 SHA-256 前 16 位。

    真实 Alertmanager 用 xxhash 并对标签做特定编码,这里用标准库替代 ——
    只保证"同一标签集稳定同指纹、不同标签集不同指纹",不与实现对位。
    """
    raw = "\x00".join("%s\x01%s" % (k, labels[k]) for k in sorted(labels))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def group_key(labels, group_by):
    """组键。

    - `group_by` 为空列表(默认)  -> 所有告警聚成同一个组,键为 ""
    - `group_by == ["..."]`      -> 不聚合,每条告警独占一组(用指纹当键)
    - 其它                       -> 按指定标签的取值组合
    """
    if group_by == [GROUP_BY_ALL]:
        return fingerprint(labels)
    if not group_by:
        return ""
    return "|".join("%s=%s" % (k, labels.get(k, "")) for k in sorted(group_by))

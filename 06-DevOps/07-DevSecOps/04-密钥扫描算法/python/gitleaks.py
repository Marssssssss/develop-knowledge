#!/usr/bin/env python3
"""gitleaks 的规则模型与扫描管线的转写。

对应文件（gitleaks/gitleaks@master 实读）：

* ``config/rule.go``            —— ``Rule`` 结构与 ``Validate()``
* ``detect/utils.go``           —— ``shannonEntropy``
* ``detect/detect.go``          —— 关键词预筛与熵过滤的调用点
* ``config/gitleaks.toml``      —— 内置规则的 ``entropy`` / ``secretGroup`` / ``keywords``
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional, Tuple

# --------------------------------------------------------------------------
# 1. 香农熵（detect/utils.go）
# --------------------------------------------------------------------------


def shannon_entropy(data: str) -> float:
    """对应 gitleaks 的 ``shannonEntropy``。

    与 detect-secrets 的关键差别：这里统计的是 **data 里实际出现的每个 rune**，
    不做任何字符集限制，所以得到的是教科书意义上的完整香农熵。
    """
    if data == "":
        return 0
    counts: Dict[str, int] = {}
    for ch in data:
        counts[ch] = counts.get(ch, 0) + 1
    inv_length = 1.0 / len(data)
    entropy = 0.0
    for count in counts.values():
        freq = float(count) * inv_length
        entropy -= freq * math.log2(freq)
    return entropy


# --------------------------------------------------------------------------
# 2. 规则（config/rule.go）
# --------------------------------------------------------------------------


class Allowlist:
    """简化版 allowlist：只保留本 demo 需要的行为。"""

    def __init__(self, regexes: Optional[List[str]] = None,
                 paths: Optional[List[str]] = None,
                 stopwords: Optional[List[str]] = None,
                 regex_target: str = "match"):
        self.regexes = [re.compile(r, re.IGNORECASE) for r in (regexes or [])]
        self.paths = [re.compile(p, re.IGNORECASE) for p in (paths or [])]
        self.stopwords = [s.lower() for s in (stopwords or [])]
        self.regex_target = regex_target      # match | secret | line

    def validate(self) -> Optional[str]:
        if self.regex_target not in ("match", "secret", "line"):
            return "invalid regexTarget: %s" % self.regex_target
        return None


class Rule:
    def __init__(self, rule_id: str, regex: str,
                 entropy: float = 0.0, secret_group: int = 1,
                 keywords: Optional[List[str]] = None,
                 path: Optional[str] = None,
                 allowlists: Optional[List[Allowlist]] = None):
        self.rule_id = rule_id
        self.regex = re.compile(regex) if regex else None
        self.entropy = entropy
        self.secret_group = secret_group
        self.keywords = [k.lower() for k in (keywords or [])]
        self.path = re.compile(path) if path else None
        self.allowlists = allowlists or []
        self.validated = False

    def num_subexp(self) -> int:
        return self.regex.groups if self.regex else 0

    def validate(self) -> Optional[str]:
        """对应 ``Rule.Validate()``：拦截常见误配置。"""
        if self.validated:
            return None
        if not self.rule_id.strip():
            return "rule |id| is missing or empty"
        if self.regex is None and self.path is None:
            return "%s: both |regex| and |path| are empty, this rule will have no effect" % self.rule_id
        if self.regex is not None and self.secret_group > self.num_subexp():
            return ("%s: invalid regex secret group %d, max regex secret group %d"
                    % (self.rule_id, self.secret_group, self.num_subexp()))
        for al in self.allowlists:
            err = al.validate()
            if err:
                return "%s: %s" % (self.rule_id, err)
        self.validated = True
        return None


# --------------------------------------------------------------------------
# 3. 扫描（detect/detect.go 的调用点语义）
# --------------------------------------------------------------------------


def prefilter_hit(rule: Rule, fragment_keywords: set) -> bool:
    """对应 detect.go 里的关键词预筛。

    * 规则没有 keywords → **总是**扫（源码注释：if no keywords are associated
      with the rule always scan the fragment using the rule）
    * 有 keywords → 片段里必须命中其中**任一**（源码用 Aho-Corasick trie 加速）
    """
    if not rule.keywords:
        return True
    return any(k in fragment_keywords for k in rule.keywords)


def apply_allowlists(rule: Rule, secret: str, match: str, line: str,
                     path: str) -> bool:
    """返回 True 表示**命中 allowlist，应跳过**该 finding。"""
    for al in rule.allowlists:
        for p in al.paths:
            if p.search(path):
                return True
        for r in al.regexes:
            target = {"match": match, "secret": secret, "line": line}.get(al.regex_target, match)
            if r.search(target or ""):
                return True
        low = (secret or "").lower()
        for w in al.stopwords:
            if w in low:
                return True
    return False


def detect_rule(rule: Rule, fragment: str, path: str = "") -> List[Dict[str, Any]]:
    """在一段文本上跑一条规则，返回 findings（已过熵阈值与 allowlist）。"""
    if rule.regex is None:
        return []
    findings: List[Dict[str, Any]] = []
    for m in rule.regex.finditer(fragment):
        match_text = m.group(0)
        try:
            secret = m.group(rule.secret_group)
        except (IndexError, re.error):
            secret = match_text
        if apply_allowlists(rule, secret, match_text, fragment, path):
            continue
        entropy = shannon_entropy(secret)
        # 源码：if r.Entropy != 0.0 { if entropy <= r.Entropy { skip } }
        if rule.entropy != 0.0 and entropy <= rule.entropy:
            continue
        findings.append({"rule": rule.rule_id, "secret": secret,
                         "entropy": entropy, "match": match_text})
    return findings


def scan(rules: List[Rule], fragment: str, path: str = "") -> List[Dict[str, Any]]:
    """跑一组规则；关键词预筛在规则级别生效。"""
    kw = set(re.findall(r"[a-z0-9\-_]+", fragment.lower()))
    out: List[Dict[str, Any]] = []
    for rule in rules:
        if not prefilter_hit(rule, kw):
            continue
        out.extend(detect_rule(rule, fragment, path))
    return out

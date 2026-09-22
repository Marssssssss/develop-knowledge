#!/usr/bin/env python3
"""demo574 的入口：把两套扫描器的判定放在一起做对比。

    python main.py            # 打印若干对比样例
    python selfcheck_secret.py  # 跑断言
"""

from __future__ import annotations

from typing import Dict, List

from detect_secrets import (  # noqa: F401
    Base64HighEntropyString, HexHighEntropyString, HighEntropyStringsPlugin,
    KeywordDetector, REGEX_BY_FILETYPE, DENYLIST,
)
from gitleaks import Allowlist, Rule, detect_rule, prefilter_hit, scan, shannon_entropy  # noqa: F401


def ds_base64_report(line: str, limit: float = 4.5) -> List[str]:
    """detect-secrets 的 Base64 高熵插件在一行上的命中。"""
    return sorted(Base64HighEntropyString(limit).analyze_line(line))


def ds_hex_report(line: str, limit: float = 3.0) -> List[str]:
    return sorted(HexHighEntropyString(limit).analyze_line(line))


def ds_keyword_report(line: str, filetype: str = "python") -> List[str]:
    return sorted(KeywordDetector().analyze_line(line, filetype))


def gl_report(rules: List[Rule], fragment: str, path: str = "") -> List[Dict[str, object]]:
    return scan(rules, fragment, path)


def entropy_gap(sample: str) -> Dict[str, float]:
    """同一串在两套口径下的熵：gitleaks 恒 >= detect-secrets（后者受字符集限制）。"""
    return {
        "detect_secrets_base64": Base64HighEntropyString().calculate_shannon_entropy(sample),
        "detect_secrets_hex": HexHighEntropyString().calculate_shannon_entropy(sample),
        "gitleaks": shannon_entropy(sample),
    }


if __name__ == "__main__":
    demo_lines = [
        'aws_key = "AKIAIOSFODNN7EXAMPLE"',
        'token = "aaaaaaaaaaaaaaaaaaaa"',
        'password: hunter2',
        'password = hunter2',
        'hex = "0123456789"',
        'hex = "01234567890123456789"',
    ]
    for line in demo_lines:
        print("line:", line)
        print("   base64 :", ds_base64_report(line))
        print("   hex    :", ds_hex_report(line))
        print("   keyword:", ds_keyword_report(line))
        print("   entropies:", {k: round(v, 4) for k, v in entropy_gap(line).items()})

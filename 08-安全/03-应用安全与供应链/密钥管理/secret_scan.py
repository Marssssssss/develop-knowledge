#!/usr/bin/env python3
"""硬编码凭据检测：为什么「熵阈值」这条判据天生会漏掉一批密钥。

CWE-798（Use of Hard-coded Credentials）把硬编码凭据分成两类：
  * **Inbound**：产品自带认证逻辑，把输入与内置凭据比对（默认管理员账号）；
    每个安装的密码都一样，管理员往往改不掉、也检测不到。
  * **Outbound**：产品用它去连另一个系统（前端里塞后端凭据）。
可利用性被标为 **High** —— 客户端二进制里的字符串提取起来太容易了。

检测侧最常被抄的一条判据是「香农熵超过阈值即告警」。本 demo 说明它为什么不够：
**香农熵的上限由字符集大小决定**，`log2(16)=4.0` 的十六进制串永远到不了 4.5。
于是「阈值 4.5」这个在很多教程里出现的数字，会系统性漏掉**质量很高**的十六进制密钥。

对应地给出两级改进：**按字符集归一化**、再叠加**规则**（关键字 / 结构 / 上下文白名单）。
"""

import math
import re
from collections import Counter
from typing import List, Tuple

MIN_LEN = 20          # 熵判据只对足够长的串生效；短串的归一化熵没有意义
RAW_THRESHOLD = 4.5   # 常见的「教程阈值」
NORM_THRESHOLD = 0.90


def shannon_entropy(s: str) -> float:
    """香农熵，单位 bit/字符。均匀分布在给定字符集上取最大值 log2(|A|)。"""
    if not s:
        return 0.0
    n = len(s)
    counts = Counter(s)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def alphabet_size(s: str) -> int:
    return len(set(s))


def normalized_entropy(s: str) -> float:
    """H / log2(|A|) ∈ [0,1]。

    除以**观测到的字符集大小**后，不同字符集的串才有可比性：
    十六进制串最多 4.0 bit，除以 log2(16)=4 后同样能接近 1.0。
    """
    a = alphabet_size(s)
    if a <= 1:
        return 0.0
    return shannon_entropy(s) / math.log2(a)


# ------------------------------------------------------------------ 规则

# 值上的结构规则
VALUE_RULES = [
    ("AWS access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("服务商 live key", re.compile(r"\btok_live_[0-9a-zA-Z]{20,}")),
    ("PEM private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]
# 变量名上的关键字规则
NAME_RULE = re.compile(
    r"(password|passwd|secret|token|api[_-]?key|credential|private[_-]?key)", re.I)
# 上下文白名单：名字里带这些词的，即使是高熵串也不是凭据
NAME_ALLOWLIST = re.compile(
    r"(sha|sha1|sha256|commit|digest|checksum|uuid|request[_-]?id|version|hash)", re.I)


# ------------------------------------------------------------------ 语料

CORPUS: List[Tuple[str, str, bool]] = [
    ("aws_key",     "AKIAIOSFODNN7EXAMPLE", True),
    ("api_token",   "3f8a1c7d5e2b4096af17c3de85b0f2146e9a7c31", True),
    ("jwt_secret",  "c3VwZXJzZWNyZXR2YWx1ZTEyMzQ1Njc4OTA=", True),
    ("db_password", "password123", True),
    ("vendor_sk",   "tok_live_51H8xQ2eZvKYlo2Cabcdefghijklm", True),
    ("git_sha",     "da39a3ee5e6b4b0d3255bfef95601890afd80709", False),
    ("request_id",  "550e8400-e29b-41d4-a716-446655440000", False),
    ("png_b64",     "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ", False),
    ("api_url",     "https://api.example.com/v1/users?id=12345", False),
    ("version",     "1.2.3", False),
]


# ------------------------------------------------------------------ 检测器

def detect_raw_entropy(value: str) -> bool:
    """D1：裸香农熵超阈值（且长度达标）。"""
    return len(value) >= MIN_LEN and shannon_entropy(value) >= RAW_THRESHOLD


def detect_norm_entropy(value: str) -> bool:
    """D2：归一化熵超阈值。"""
    return len(value) >= MIN_LEN and normalized_entropy(value) >= NORM_THRESHOLD


def detect_combined(name: str, value: str) -> bool:
    """D3：归一化熵 OR 结构规则 OR（变量名关键字 AND 熵 ≥ 3.0）。

    白名单优先——名字里带 sha/uuid/digest 的高熵串先排除掉。
    """
    if NAME_ALLOWLIST.search(name):
        return False
    if any(r.search(value) for _, r in VALUE_RULES):
        return True
    if detect_norm_entropy(value):
        return True
    return bool(NAME_RULE.search(name)) and shannon_entropy(value) >= 3.0


DETECTORS = [
    ("D1 裸熵 ≥4.5", lambda n, v: detect_raw_entropy(v)),
    ("D2 归一化熵 ≥0.90", lambda n, v: detect_norm_entropy(v)),
    ("D3 规则+归一化熵", detect_combined),
]


def evaluate(det) -> Tuple[int, int, int, int, List[str]]:
    tp = fp = fn = tn = 0
    wrong: List[str] = []
    for name, value, is_secret in CORPUS:
        got = det(name, value)
        if is_secret and got:
            tp += 1
        elif is_secret and not got:
            fn += 1
            wrong.append("漏报 %s" % name)
        elif not is_secret and got:
            fp += 1
            wrong.append("误报 %s" % name)
        else:
            tn += 1
    return tp, fp, fn, tn, wrong


def sweep(lo: float = 3.0, hi: float = 5.0, step: float = 0.01) -> List[Tuple[float, int, int]]:
    """扫描裸熵阈值，返回 [(threshold, TP, FP), ...]。

    目的是量化「不存在一个阈值能同时做到高召回与低误报」。
    """
    out = []
    t = lo
    while t <= hi + 1e-9:
        tp = fp = 0
        for name, value, is_secret in CORPUS:
            if len(value) < MIN_LEN:
                continue
            hit = shannon_entropy(value) >= t
            if hit and is_secret:
                tp += 1
            elif hit and not is_secret:
                fp += 1
        out.append((round(t, 2), tp, fp))
        t += step
    return out


def separable_by_raw_threshold(a: str, b: str) -> bool:
    """是否存在某个裸熵阈值使得「命中 a、不命中 b」。

    当 a 是密钥、b 是非密钥、且 H(a) < H(b) 时，答案恒为 False ——
    这两个串在**任何**熵阈值下都不可分。
    """
    ha, hb = shannon_entropy(a), shannon_entropy(b)
    if ha < hb:
        return False
    return True


def report() -> None:
    print("%-22s %-8s %-6s %-10s %s" % ("item", "H(bit)", "|A|", "H/log2|A|", "truth"))
    print("-" * 60)
    for name, value, is_secret in CORPUS:
        print("%-22s %-8.3f %-6d %-10.3f %s" % (
            name, shannon_entropy(value), alphabet_size(value),
            normalized_entropy(value), "SECRET" if is_secret else "-"))
    print()
    print("%-22s %-4s %-4s %-4s %-4s %s" % ("detector", "TP", "FP", "FN", "TN", "错判"))
    print("-" * 78)
    for label, det in DETECTORS:
        tp, fp, fn, tn, wrong = evaluate(det)
        print("%-22s %-4d %-4d %-4d %-4d %s" % (
            label, tp, fp, fn, tn, "、".join(wrong) or "-"))


if __name__ == "__main__":
    report()

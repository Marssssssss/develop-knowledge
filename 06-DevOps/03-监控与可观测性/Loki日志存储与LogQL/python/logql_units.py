"""LogQL / Loki 的数值与单位解析。

**字节单位是 1024 进制**，不是 1000 进制。依据：官方 ingester 配置把
`chunk_target_size` 的默认值 1572864 注释为「1.5 MB」——1.5 × 1024² =
1572864；同理 `chunk_block_size` 262144 注释为 256 KB。按 1000 进制，
256KB 会算成 256000，与官方注释对不上。

时长单位复用 Prometheus 的 `model.Duration` 解析表。
"""

from __future__ import annotations

import re

from logql_syntax import LogQLError

__all__ = [
    "DURATION_UNITS",
    "BYTE_UNITS",
    "parse_duration",
    "parse_bytes",
    "classify_number",
]

DURATION_UNITS = {
    "ns": 1e-9,
    "us": 1e-6,
    "µs": 1e-6,
    "ms": 1e-3,
    "s": 1.0,
    "m": 60.0,
    "h": 3600.0,
    "d": 86400.0,
    "w": 604800.0,
    "y": 31536000.0,
}

BYTE_UNITS = {
    "b": 1,
    "kb": 1024,
    "kib": 1024,
    "mb": 1024**2,
    "mib": 1024**2,
    "gb": 1024**3,
    "gib": 1024**3,
    "tb": 1024**4,
    "tib": 1024**4,
    "pb": 1024**5,
    "pib": 1024**5,
}

_NUM_RE = re.compile(r"^(-?\d+(?:\.\d+)?)([a-zA-Zµ]*)$")


def _split_number(text: str) -> tuple[float, str]:
    m = _NUM_RE.match(text)
    if m is None:
        raise LogQLError(f"不是合法的数值字面量: {text!r}")
    return float(m.group(1)), m.group(2).lower()


def parse_duration(text: str) -> float:
    """把 `5m` / `1h30m` 这类字面量转成秒。"""
    if not text:
        raise LogQLError("空时长")
    m = _NUM_RE.match(text)
    if m is not None:
        value, unit = float(m.group(1)), m.group(2).lower()
        if unit not in DURATION_UNITS:
            raise LogQLError(f"未知时长单位: {unit!r}")
        return value * DURATION_UNITS[unit]
    total = 0.0
    pos = 0
    for mm in re.finditer(r"(\d+(?:\.\d+)?)([a-zA-Zµ]+)", text):
        if mm.start() != pos:
            raise LogQLError(f"非法时长字面量: {text!r}")
        unit = mm.group(2).lower()
        if unit not in DURATION_UNITS:
            raise LogQLError(f"未知时长单位: {unit!r}")
        total += float(mm.group(1)) * DURATION_UNITS[unit]
        pos = mm.end()
    if pos != len(text):
        raise LogQLError(f"非法时长字面量: {text!r}")
    return total


def parse_bytes(text: str) -> float:
    value, unit = _split_number(text)
    if unit not in BYTE_UNITS:
        raise LogQLError(f"未知字节单位: {unit!r}")
    return value * BYTE_UNITS[unit]


def classify_number(text: str) -> tuple[str, float]:
    """判断标签过滤器右侧字面量的类型与归一化数值。

    返回 ("plain"|"duration"|"bytes", 数值)。纯数字保持原值；duration 归一化
    成秒；bytes 归一化成字节。对应 Loki 允许 `duration > 1s`、`bytes > 20MB`
    这类带单位的标签比较。
    """
    value, unit = _split_number(text)
    if not unit:
        return "plain", value
    if unit in DURATION_UNITS:
        return "duration", value * DURATION_UNITS[unit]
    if unit in BYTE_UNITS:
        return "bytes", value * BYTE_UNITS[unit]
    raise LogQLError(f"无法归类单位: {unit!r}")

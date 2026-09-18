"""依赖解析 fixture：注册表、依赖边、根约束、调用图、CVE 表。

单独成模块以满足单源文件 <=300 行的约束（与 Go 版 dep_fixture.go 对应）。
"""

from typing import Dict, List, Tuple

REGISTRY: Dict[str, List[str]] = {
    "httpkit":  ["2.3.0", "2.4.0", "3.0.0"],
    "codec":    ["1.4.2", "1.4.3", "1.5.0"],
    "compress": ["0.2.7"],               # 上游没发修复版 → 只能停在受影响版本
    "logfmt":   ["1.0.1", "1.0.2"],
    "orm":      ["3.1.0"],
}

# 包版本 -> {依赖名: 区间}
DEPS: Dict[Tuple[str, str], Dict[str, str]] = {
    ("httpkit", "2.3.0"): {"codec": "~1.4.0"},
    ("httpkit", "2.4.0"): {"codec": "~1.4.2"},
    ("httpkit", "3.0.0"): {"codec": "^1.5.0"},
    ("codec", "1.4.2"):   {"compress": "~0.2.0"},
    ("codec", "1.4.3"):   {"compress": "~0.2.0"},
    ("codec", "1.5.0"):   {"compress": "^0.3.0"},
    ("compress", "0.2.7"): {},
    ("logfmt", "1.0.1"):  {},
    ("logfmt", "1.0.2"):  {},
    ("orm", "3.1.0"):     {},
}

ROOT = ("app", {"httpkit": "^2.0.0", "logfmt": "^1.0.0", "orm": "^3.0.0"})


# ------------------------------------------------------------------ 可达性

# 调用图：函数 -> 它直接调用的函数
CALLGRAPH: Dict[str, List[str]] = {
    "app.main":          ["httpkit.Handler", "logfmt.Format", "orm.Find"],
    "httpkit.Handler":   ["codec.Decode"],
    "codec.Decode":      [],          # 注：codec 1.4.2 不调用 compress.Inflate
    "compress.Inflate":  [],
    "orm.Find":          ["orm.RawQuery"],
    "orm.RawQuery":      [],
    "logfmt.Format":     [],
}
ENTRY = "app.main"

# CVE：包、影响区间、有洞的函数
CVES = [
    ("CVE-COMPRESS", "compress", ">=0.2.0 <0.2.8", "compress.Inflate"),
    ("CVE-CODEC",    "codec",    ">=1.4.0 <1.5.0", "codec.Decode"),
    ("CVE-ORM",      "orm",      ">=3.0.0 <3.1.1", "orm.RawQuery"),
    ("CVE-LOGFMT",   "logfmt",   ">=1.0.0 <1.0.2", "logfmt.Format"),
]

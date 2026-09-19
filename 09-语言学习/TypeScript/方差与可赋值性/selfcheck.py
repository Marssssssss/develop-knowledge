#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""方差与可赋值性 demo 自检。

每个 fixture 可以单独指定额外的 tsc 参数（用于对照 strictFunctionTypes 开/关），
再比对文件头 `// EXPECT:` 声明的错误码集合（精确相等）。
运行期部分：node 直跑 variance.ts。

用法：python selfcheck.py
"""

import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TYPES = os.path.join(HERE, "types")
MAIN = os.path.join(HERE, "variance.ts")

NODE = os.environ.get("NODE_BIN") or shutil.which("node") or "node"
TSC = os.environ.get("TSC_JS") or os.path.join(
    os.path.expanduser("~"),
    ".workbuddy", "binaries", "node", "workspace",
    "node_modules", "typescript", "lib", "tsc.js",
)

BASE = ["--strict", "--target", "es2020", "--noEmit", "--pretty", "false"]

# (文件名, 额外编译开关)
CASES = [
    ("bivariance_strict.ts", []),
    ("bivariance_off.ts", ["--strictFunctionTypes", "false"]),
    ("variance_annotations.ts", []),
    ("variance_wrong_annotation.ts", []),
    ("recursive_variance.ts", []),
    ("recursive_variance_fixed.ts", []),
]


def run(argv):
    p = subprocess.run(argv, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.returncode, p.stdout + p.stderr


def expect_codes(path):
    with open(path, "r", encoding="utf-8") as f:
        head = f.read(4000)
    m = re.search(r"^//\s*EXPECT:\s*(.*)$", head, re.M)
    if not m:
        raise SystemExit("fixture 缺少 // EXPECT: 头：%s" % path)
    body = m.group(1).strip()
    if body.upper() == "OK":
        return set()
    return set(c.strip() for c in body.split(",") if c.strip())


def check_type_fixtures():
    if not os.path.exists(TSC):
        raise SystemExit("找不到 tsc：%s（可设 TSC_JS 环境变量）" % TSC)
    on_disk = sorted(f for f in os.listdir(TYPES) if f.endswith(".ts"))
    assert sorted(n for n, _ in CASES) == on_disk, "fixture 清单与磁盘不一致：%r" % on_disk
    for name, extra in CASES:
        path = os.path.join(TYPES, name)
        want = expect_codes(path)
        _, out = run([NODE, TSC] + BASE + extra + [path])
        got = set(re.findall(r"error (TS\d+)", out))
        assert got == want, (
            "fixture %s 期望 %s 实得 %s\n%s" % (name, sorted(want), sorted(got), out.strip())
        )
        print("[ok] types/%s %s EXPECT=%s" % (name, " ".join(extra), sorted(want) or "no-error"))


def check_runtime():
    code, out = run([NODE, MAIN])
    assert code == 0, "运行期脚本非零退出 %d\n%s" % (code, out)
    lines = [ln for ln in out.splitlines() if ln.startswith("ok ")]
    bad = [ln for ln in out.splitlines() if ln.startswith("FAIL")]
    assert not bad, "存在失败断言：%r" % bad
    assert len(lines) >= 8, "运行时断言条数不足：%d\n%s" % (len(lines), out)
    print("[ok] variance.ts 运行时断言 %d 条全绿" % len(lines))


def main():
    check_type_fixtures()
    check_runtime()
    print("=== 方差与可赋值性 demo 自检全部通过 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())

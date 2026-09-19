#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""类型收窄与守卫 demo 自检。

1. 编译期：types/*.ts 跑 tsc，比对 `// EXPECT:` 声明的错误码集合（精确相等）；
2. 运行期：node 直跑 narrowing.ts，要求全部运行时断言通过。

用法：python selfcheck.py
"""

import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TYPES = os.path.join(HERE, "types")
MAIN = os.path.join(HERE, "narrowing.ts")

NODE = os.environ.get("NODE_BIN") or shutil.which("node") or "node"
TSC = os.environ.get("TSC_JS") or os.path.join(
    os.path.expanduser("~"),
    ".workbuddy", "binaries", "node", "workspace",
    "node_modules", "typescript", "lib", "tsc.js",
)

COMMON = ["--strict", "--target", "es2020", "--noEmit", "--pretty", "false"]


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
    return set(c.strip() for c in m.group(1).split(",") if c.strip())


def check_type_fixtures():
    if not os.path.exists(TSC):
        raise SystemExit("找不到 tsc：%s（可设 TSC_JS 环境变量）" % TSC)
    files = sorted(f for f in os.listdir(TYPES) if f.endswith(".ts"))
    assert len(files) == 5, "fixture 数量不对：%r" % files
    for name in files:
        path = os.path.join(TYPES, name)
        want = expect_codes(path)
        _, out = run([NODE, TSC] + COMMON + [path])
        got = set(re.findall(r"error (TS\d+)", out))
        assert got == want, (
            "fixture %s 期望 %s 实得 %s\n%s" % (name, sorted(want), sorted(got), out.strip())
        )
        print("[ok] types/%s EXPECT=%s" % (name, sorted(want) or "no-error"))


def check_runtime():
    code, out = run([NODE, MAIN])
    assert code == 0, "运行期脚本非零退出 %d\n%s" % (code, out)
    lines = [ln for ln in out.splitlines() if ln.startswith("ok ")]
    bad = [ln for ln in out.splitlines() if ln.startswith("FAIL")]
    assert not bad, "存在失败断言：%r" % bad
    assert len(lines) >= 8, "运行时断言条数不足：%d\n%s" % (len(lines), out)
    print("[ok] narrowing.ts 运行时断言 %d 条全绿" % len(lines))


def main():
    check_type_fixtures()
    check_runtime()
    print("=== 类型收窄与守卫 demo 自检全部通过 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())

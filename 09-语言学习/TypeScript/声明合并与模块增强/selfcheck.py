#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""声明合并与模块增强 demo 自检。

三部分：
  1. 编译期：7 个 fixture 与 `// EXPECT:` 声明的错误码集合精确比对；
  2. 负控：把 overload_order 里的特化签名删掉，确认那些赋值会真的失败（TS2322）；
  3. 运行期：merging_runtime.ts 校验「合并」在 emit 之后的运行时对应物。

用法：python selfcheck.py        （可选环境变量 NODE_BIN / TSC_JS）
"""

import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TYPES = os.path.join(HERE, "types")

NODE = os.environ.get("NODE_BIN") or shutil.which("node") or "node"
TSC = os.environ.get("TSC_JS") or os.path.join(
    os.path.expanduser("~"), ".workbuddy", "binaries", "node", "workspace",
    "node_modules", "typescript", "lib", "tsc.js",
)

COMMON = ["--strict", "--target", "es2020", "--noEmit", "--pretty", "false"]

NEGATIVE = """interface Base { k: string }
interface Elem extends Base { e: true }
interface Div extends Elem { d: true }

// 只留一条 `make(tag: string)` 签名：没有后写的组、也没有特化签名可以冒泡
interface Factory { make(tag: string): Elem }

declare const f: Factory;
const d: Div = f.make("div");
export { d };
"""


def run(argv):
    p = subprocess.run(argv, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.returncode, p.stdout + p.stderr


def codes_of(path):
    _, out = run([NODE, TSC] + COMMON + [path])
    return set(re.findall(r"error (TS\d+)", out)), out


def expect_codes(path):
    with open(path, "r", encoding="utf-8") as f:
        head = f.read(4000)
    m = re.search(r"^//\s*EXPECT:\s*(.*)$", head, re.M)
    if not m:
        raise SystemExit("fixture 缺少 // EXPECT: 头：%s" % path)
    body = m.group(1).strip()
    return set() if body.upper() == "OK" else set(c.strip() for c in body.split(",") if c.strip())


def check_type_fixtures():
    if not os.path.exists(TSC):
        raise SystemExit("找不到 tsc：%s（可设 TSC_JS 环境变量）" % TSC)
    files = sorted(f for f in os.listdir(TYPES) if f.endswith(".ts"))
    assert len(files) == 7, "fixture 数量不对：%r" % files
    for name in files:
        path = os.path.join(TYPES, name)
        want = expect_codes(path)
        got, out = codes_of(path)
        assert got == want, (
            "fixture %s 期望 %s 实得 %s\n%s" % (name, sorted(want), sorted(got), out.strip())
        )
        print("[ok] types/%s EXPECT=%s" % (name, ",".join(sorted(want)) or "OK"))


def check_overload_order_negative():
    """没有 later-group / 特化冒泡时，同一批赋值必须失败，否则说明 fixture 是恒真的。"""
    probe = os.path.join(HERE, ".probe_order.ts")
    with open(probe, "w", encoding="utf-8") as f:
        f.write(NEGATIVE)
    try:
        got, out = codes_of(probe)
        # 这里报的是 TS2741（返回类型缺了 Div 独有的成员）而不是 TS2322：
        # 两者都表明「没有走 Div 那条重载」，正是这条负控要证明的事。
        assert got == {"TS2741"}, "负控应当只报 TS2741，实得 %s\n%s" % (got, out.strip())
    finally:
        os.remove(probe)
    print("[ok] overload order 负控：去掉后写的组与特化签名后同一赋值报 TS2741")


def check_augmentation_limits():
    """模块增强里额外声明一个全新顶层 interface，实测放行（手册那条限制没有强制检查）。"""
    probe = os.path.join(TYPES, ".probe_newtop.ts")
    src = (
        'import "./observable";\n'
        'declare module "./observable" {\n'
        "  interface Brand { gap: number }\n"
        "}\n"
        "export {};\n"
    )
    with open(probe, "w", encoding="utf-8") as f:
        f.write(src)
    try:
        got, out = codes_of(probe)
        assert got == set(), "新顶层 interface 本应放行，实得 %s\n%s" % (got, out.strip())
    finally:
        os.remove(probe)
    probe2 = os.path.join(TYPES, ".probe_ambient_declare.ts")
    src2 = (
        'import "./observable";\n'
        'declare module "./observable" {\n'
        "  declare const q: number;\n"
        "}\n"
        "export {};\n"
    )
    with open(probe2, "w", encoding="utf-8") as f:
        f.write(src2)
    try:
        got2, _ = codes_of(probe2)
        assert got2 == {"TS1038"}, "ambient 里再写 declare 应报 TS1038，实得 %s" % got2
    finally:
        os.remove(probe2)
    print("[ok] 增强边界：新顶层 interface 放行 / ambient 里写 declare 报 TS1038")


def check_runtime():
    main = os.path.join(HERE, "merging_runtime.ts")
    code, out = run([NODE, main])
    assert code == 0, "运行期脚本非零退出 %d\n%s" % (code, out)
    lines = [ln for ln in out.splitlines() if ln.startswith("ok ")]
    assert len(lines) == 12, "运行时断言条数不对：%d\n%s" % (len(lines), out)
    bad = [ln for ln in out.splitlines() if ln.startswith("FAIL")]
    assert not bad, "存在失败断言：%r" % bad
    print("[ok] merging_runtime.ts 运行时断言 %d 条全绿" % len(lines))


def main():
    check_type_fixtures()
    check_overload_order_negative()
    check_augmentation_limits()
    check_runtime()
    print("=== 声明合并与模块增强 demo 自检全部通过 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())

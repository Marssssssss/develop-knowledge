#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""类型实例化深度与上限 demo 自检。

三部分：
  1. 编译期：4 个 fixture 与 `// EXPECT:` 错误码集合精确比对；
  2. 边界扫描：现场生成探针，把三道护栏的**精确阈值**钉死
     （非尾递归 48/49 字符、尾递归 999/1000 字符、笛卡尔积 316^2 / 317^2）；
  3. 运行期：guard_limits_runtime.ts 复刻 tsc.js 里的常量与判据，19 条断言。

用法：python selfcheck.py        （可选环境变量 NODE_BIN / TSC_JS）
"""

import glob
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TYPES = os.path.join(HERE, "types")
BQ = chr(96)

NODE = os.environ.get("NODE_BIN") or shutil.which("node") or "node"
TSC = os.environ.get("TSC_JS") or os.path.join(
    os.path.expanduser("~"), ".workbuddy", "binaries", "node", "workspace",
    "node_modules", "typescript", "lib", "tsc.js",
)

COMMON = ["--strict", "--target", "es2020", "--noEmit", "--pretty", "false"]

NONTAIL_SRC = (
    "type GetChars<S> = S extends {BQ}${{infer C}}${{infer R}}{BQ}\n"
    "  ? C | GetChars<R>\n"
    "  : never;\n"
    "type T = GetChars<{lit}>;\n"
    "export type {{ T }};\n"
).replace("{BQ}", BQ)

TAIL_SRC = (
    "type GetChars<S> = Helper<S, never>;\n"
    "type Helper<S, Acc> = S extends {BQ}${{infer C}}${{infer R}}{BQ}\n"
    "  ? Helper<R, C | Acc>\n"
    "  : Acc;\n"
    "type T = GetChars<{lit}>;\n"
    "export type {{ T }};\n"
).replace("{BQ}", BQ)

CROSS_SRC = (
    "type Digit = {union};\n"
    "type IDs = {BQ}{body}{BQ};\n"
    "export type {{ IDs }};\n"
).replace("{BQ}", BQ)


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
    assert len(files) == 4, "fixture 数量不对：%r" % files
    for name in files:
        path = os.path.join(TYPES, name)
        want = expect_codes(path)
        got, out = codes_of(path)
        assert got == want, (
            "fixture %s 期望 %s 实得 %s\n%s" % (name, sorted(want), sorted(got), out.strip())
        )
        print("[ok] types/%s EXPECT=%s" % (name, ",".join(sorted(want)) or "OK"))


def _probe(src, label):
    probe = os.path.join(HERE, ".probe_%s.ts" % os.getpid())
    with open(probe, "w", encoding="utf-8") as f:
        f.write(src)
    try:
        got, out = codes_of(probe)
        return got, out
    finally:
        os.remove(probe)


def cleanup_probes():
    """清掉上一次被中断留下的探针；否则它们会污染工作区并可能被误提交。"""
    for p in glob.glob(os.path.join(HERE, ".probe_*.ts")):
        try:
            os.remove(p)
        except OSError:
            pass


def check_thresholds():
    """把三道护栏的精确阈值钉死：紧贴阈值的两侧各跑一次。"""
    cleanup_probes()
    # ① 非尾递归 GetChars：48 放行 / 49 报警
    got48, _ = _probe(NONTAIL_SRC.format(lit='"' + "a" * 48 + '"'), "nt48")
    got49, _ = _probe(NONTAIL_SRC.format(lit='"' + "a" * 49 + '"'), "nt49")
    assert got48 == set(), "非尾递归 48 字符本应放行，实得 %s" % got48
    assert got49 == {"TS2589"}, "非尾递归 49 字符本应 TS2589，实得 %s" % got49
    print("[ok] 非尾递归阈值：48 放行 / 49 报 TS2589")

    # ② 尾递归（走有名字的类型别名）：999 放行 / 1000 报警 —— 对应 tailCount === 1e3
    got999, _ = _probe(TAIL_SRC.format(lit='"' + "a" * 999 + '"'), "t999")
    got1000, _ = _probe(TAIL_SRC.format(lit='"' + "a" * 1000 + '"'), "t1000")
    assert got999 == set(), "尾递归 999 字符本应放行，实得 %s" % got999
    assert got1000 == {"TS2589"}, "尾递归 1000 字符本应 TS2589，实得 %s" % got1000
    print("[ok] 尾递归阈值：999 放行 / 1000 报 TS2589（= tailCount === 1e3）")

    # ③ 联合笛卡尔积：99856 放行 / 100489 报警 —— 对应 getCrossProductUnionSize >= 1e5
    def digits(n):
        return " | ".join('"%d"' % i for i in range(n))

    lo = CROSS_SRC.format(union=digits(316), body="${Digit}" * 2)
    hi = CROSS_SRC.format(union=digits(317), body="${Digit}" * 2)
    gotlo, _ = _probe(lo, "lo")
    gothi, _ = _probe(hi, "hi")
    assert gotlo == set(), "316^2=99856 本应放行，实得 %s" % gotlo
    assert gothi == {"TS2590"}, "317^2=100489 本应 TS2590，实得 %s" % gothi
    print("[ok] 笛卡尔积阈值：99856 放行 / 100489 报 TS2590（阈值夹在这两个数之间）")


def check_runtime():
    main = os.path.join(HERE, "guard_limits_runtime.ts")
    code, out = run([NODE, main])
    assert code == 0, "运行期脚本非零退出 %d\n%s" % (code, out)
    lines = [ln for ln in out.splitlines() if ln.startswith("ok ")]
    assert len(lines) == 17, "运行时断言条数不对：%d\n%s" % (len(lines), out)
    bad = [ln for ln in out.splitlines() if ln.startswith("FAIL")]
    assert not bad, "存在失败断言：%r" % bad
    print("[ok] guard_limits_runtime.ts 运行时断言 %d 条全绿" % len(lines))


def main():
    check_type_fixtures()
    check_thresholds()
    check_runtime()
    print("=== 类型实例化深度与上限 demo 自检全部通过 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""装饰器与元数据反射 demo 自检。

四部分：
  1. 编译期：4 个 fixture 与 `// EXPECT:` 错误码集合精确比对（支持 `// FLAGS:` 逐文件开关）；
  2. 成员级：metadata_sample.ts 在 --experimentalDecorators --emitDecoratorMetadata 下
     编译成 JS 跑起来，检查每种成员挂了哪几个 design:* key；
  3. 序列化表：逐条比对 design:type 的值与 my serializeTypeNode 的源码规则；
  4. 顺序：同一个成员上叠多个装饰器时「求值自上而下、应用自下而上」。

用法：python selfcheck.py        （可选环境变量 NODE_BIN / TSC_JS）
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TYPES = os.path.join(HERE, "types")

NODE = os.environ.get("NODE_BIN") or shutil.which("node") or "node"
TSC = os.environ.get("TSC_JS") or os.path.join(
    os.path.expanduser("~"), ".workbuddy", "binaries", "node", "workspace",
    "node_modules", "typescript", "lib", "tsc.js",
)

COMMON = ["--strict", "--target", "es2020", "--noEmit", "--pretty", "false"]

RUN_JS = r"""
const records = [];
function render(v) {
  if (v === undefined) return "undefined";
  if (Array.isArray(v)) return "[" + v.map(render).join(",") + "]";
  if (typeof v === "function") return v.name;
  return String(v);
}
function collect(k, v) {
  return function (target, propertyKey) {
    const owner = typeof target === "function"
      ? target.name
      : target.constructor && target.constructor.name;
    records.push({
      owner: owner,
      key: propertyKey === undefined ? null : String(propertyKey),
      kind: k,
      val: render(v)
    });
  };
}
Reflect.metadata = function (k, v) { return collect(k, v); };
const m = require("./metadata_sample.js");
console.log(JSON.stringify({ records: records, evalLog: m.evalLog }));
"""

# design:type 期望值：与 src/compiler/transformers/typeSerializer.ts 的
# serializeTypeNode / serializeUnionOrIntersectionConstituents 逐条对齐
EXPECTED_TYPE = {
    "s": "String", "n": "Number", "b": "Boolean",
    "lit42": "Number", "negNum": "Number", "boolTrue": "Boolean", "nul": "undefined",
    "arr": "Array", "tup": "Array", "fn": "Function", "ctorType": "Function",
    "objLit": "Object",
    "anyV": "Object", "unknownV": "Object", "typeLit": "Object", "idxAcc": "Object",
    "mapped": "Object", "typeQuery": "Object", "thisType": "Object",
    "sameUnion": "String", "neverUnion": "String", "heteroUnion": "Object",
    "anyUnion": "Object", "unknownUnion": "Object", "withVoid": "Object",
    "inter": "Object",
    "vd": "undefined", "nvr": "undefined", "big": "BigInt", "sym": "Symbol",
    "tplLit": "String", "readonlyArr": "Array", "classRef": "Ref",
    "noAnnotation": "Object",
    # 方法与访问器分支
    "plainMethod": "Function", "typedMethod": "Function",
    "getter": "Number", "setter": "String",
}


def run(argv):
    p = subprocess.run(argv, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.returncode, p.stdout + p.stderr


def expect_lines(path):
    with open(path, "r", encoding="utf-8") as f:
        head = f.read(4000)
    m = re.search(r"^//\s*EXPECT:\s*(.*)$", head, re.M)
    if not m:
        raise SystemExit("fixture 缺少 // EXPECT: 头：%s" % path)
    body = m.group(1).strip()
    want = set() if body.upper() == "OK" else set(c.strip() for c in body.split(",") if c.strip())
    f = re.search(r"^//\s*FLAGS:\s*(.*)$", head, re.M)
    flags = f.group(1).split() if f else []
    return want, flags


def check_type_fixtures():
    if not os.path.exists(TSC):
        raise SystemExit("找不到 tsc：%s（可设 TSC_JS 环境变量）" % TSC)
    files = sorted(f for f in os.listdir(TYPES) if f.endswith(".ts"))
    assert len(files) == 4, "fixture 数量不对：%r" % files
    for name in files:
        path = os.path.join(TYPES, name)
        want, flags = expect_lines(path)
        _, out = run([NODE, TSC] + COMMON + flags + [path])
        got = set(re.findall(r"error (TS\d+)", out))
        assert got == want, (
            "fixture %s 期望 %s 实得 %s\n%s" % (name, sorted(want), sorted(got), out.strip())
        )
        print("[ok] types/%s EXPECT=%s FLAGS=%s" % (name, ",".join(sorted(want)) or "OK",
                                                    " ".join(flags) or "-"))


def compile_and_run_metadata():
    """把 metadata_sample.ts 编译成 JS 并在 Reflect.metadata 桩上跑一遍。"""
    tmp = tempfile.mkdtemp(prefix="ts_meta_")
    try:
        src = os.path.join(HERE, "metadata_sample.ts")
        code, out = run([NODE, TSC,
                         "--strict", "--target", "es2022", "--module", "commonjs",
                         "--experimentalDecorators", "--emitDecoratorMetadata",
                         "--outDir", tmp, src])
        assert code == 0, "编译 metadata_sample.ts 失败\n%s" % out
        runner = os.path.join(tmp, "run.js")
        with open(runner, "w", encoding="utf-8") as f:
            f.write(RUN_JS)
        code, out = run([NODE, runner])
        assert code == 0, "运行 JS 失败\n%s" % out
        payload = json.loads(out.strip().splitlines()[-1])
        return payload
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check_metadata_keys(payload):
    got = {}
    for r in payload["records"]:
        got.setdefault((r["owner"], r["key"]), []).append((r["kind"], r["val"]))
    assert len(payload["records"]) > 30, "metadata 记录太少：%d" % len(payload["records"])

    # ① 属性：只有 design:type
    kinds = [k for k, _ in got[("Sample", "s")]]
    assert kinds == ["design:type"], "普通属性只应挂 design:type，实得 %s" % kinds

    # ② 方法：三个 key 都在（design:type 是 Function）
    names = [k for k, _ in got[("Sample", "plainMethod")]]
    assert set(names) == {"design:type", "design:paramtypes", "design:returntype"}, names
    assert dict(got[("Sample", "plainMethod")])["design:type"] == "Function"
    assert dict(got[("Sample", "plainMethod")])["design:paramtypes"] == "[]"
    assert dict(got[("Sample", "plainMethod")])["design:returntype"] == "undefined"

    # ③ getter/setter：shouldAddReturnTypeMetadata 只对 MethodDeclaration 为真
    gk = set(k for k, _ in got[("Sample", "getter")])
    assert gk == {"design:type", "design:paramtypes"}, "getter 不该有 returntype：%s" % gk
    sk = set(k for k, _ in got[("Sample", "setter")])
    assert sk == {"design:type", "design:paramtypes"}, "setter 不该有 returntype：%s" % sk

    # ④ 类：shouldAddParamTypesMetadata 要求「第一个带 body 的构造函数」存在
    assert dict(got[("Sample", None)])["design:paramtypes"] == "[Ref]"
    nos = [k for (o, _), v in got.items() if o == "NoCtor" for k, _ in v]
    assert "design:paramtypes" not in nos, "没有构造函数的类不该有 paramtypes"

    # ⑤ __decorate 是倒序应用 ⇒ 同一成员上 key 的**运行次序**是 返回值 → 参数 → 类型
    order = [k for k, _ in got[("Sample", "typedMethod")]]
    assert order == ["design:returntype", "design:paramtypes", "design:type"], order

    print("[ok] 成员级 key：属性 type / 方法 三个 / 访问器无 returntype / 类级 paramtypes 需构造函数")


def check_serialization_table(payload):
    got = {}
    for r in payload["records"]:
        if r["owner"] == "Sample" and r["kind"] == "design:type":
            got[r["key"]] = r["val"]
    for key, want in EXPECTED_TYPE.items():
        assert key in got, "缺少成员 %s 的 design:type" % key
        assert got[key] == want, "成员 %s 期望 %s 实得 %s" % (key, want, got[key])
    # 构造函数参数的类型：去看类级 paramtypes
    assert dict(
        (r["key"], r["val"]) for r in payload["records"]
        if r["owner"] == "Sample" and r["kind"] == "design:paramtypes"
    )[None] == "[Ref]"
    print("[ok] 序列化表：%d 个成员的 design:type 与源码规则一致" % len(EXPECTED_TYPE))


def check_decorator_order(payload):
    # __decorate 的循环是 `for (var i = decorators.length - 1; i >= 0; i--)`：
    # 装饰器工厂自上而下求值，返回的装饰器自下而上应用。
    expected = ["eval:first", "eval:second", "apply:second", "apply:first"]
    assert payload["evalLog"] == expected, "顺序不符合倒序应用：%r" % payload["evalLog"]
    print("[ok] 求值自上而下 / 应用自下而上（源自 __decorate 的倒序遍历）")


def main():
    check_type_fixtures()
    payload = compile_and_run_metadata()
    check_metadata_keys(payload)
    check_serialization_table(payload)
    check_decorator_order(payload)
    print("=== 装饰器与元数据反射 demo 自检全部通过 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())

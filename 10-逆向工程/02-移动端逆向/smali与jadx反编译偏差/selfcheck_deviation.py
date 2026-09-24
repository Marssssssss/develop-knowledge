# -*- coding: utf-8 -*-
"""smali 与 jadx 还原路径的偏差断言(访问标志/desugar 覆盖/失败块留痕)。"""

from deviation import (
    ACC_BRIDGE, ACC_CONSTRUCTOR, ACC_DECLARED_SYNCHRONIZED, ACC_NATIVE,
    ACC_SYNTHETIC, DexClass, DexMethod, desugar_covers, java_view,
    smali_roundtrip, smali_view,
)

PASS = []


def ok(msg):
    PASS.append(msg)
    print(f"  [ok] {msg}")


def demo_class():
    return DexClass("com.a.Main", [
        DexMethod("onCreate", 0x2, [0x12, 0x54, 0x0a], line=17),
        DexMethod("access$000", ACC_SYNTHETIC | 0x8, [0x12, 0x22], line=9),
        DexMethod("run", ACC_BRIDGE | ACC_SYNTHETIC, [0x12], line=None),
        DexMethod("nativeCalc", ACC_NATIVE, [], line=None),
        DexMethod("<init>", ACC_CONSTRUCTOR, [0x70], line=5),
        DexMethod("locked", ACC_DECLARED_SYNCHRONIZED | 0x1, [0x12, 0x01], line=21),
        DexMethod("weird", 0x1, [0x12, 0xff, 0x0a], line=30),          # 反编译失败块
    ])


def main():
    cls = demo_class()
    print("1. 访问标志位值(dex-format 官方)")
    assert (ACC_BRIDGE, ACC_SYNTHETIC, ACC_NATIVE,
            ACC_CONSTRUCTOR, ACC_DECLARED_SYNCHRONIZED) == (0x40, 0x1000, 0x100,
                                                            0x10000, 0x20000)
    ok("bridge=0x40 / synthetic=0x1000 / native=0x100 / constructor=0x10000 / "
       "declared-synchronized=0x20000(官方 access 标志表)")

    print("2. smali 侧:结构无损")
    sv = smali_view(cls)
    by = {m["method"]: m for m in sv["methods"]}
    assert by["access$000"]["flags"] == ["synthetic"]
    assert by["run"]["flags"] == ["synthetic", "bridge"]
    assert by["nativeCalc"]["flags"] == ["native"] and by["nativeCalc"]["op_count"] == 0
    assert by["<init>"]["line"] == 5 and by["run"]["line"] is None
    rt = smali_roundtrip(sv)
    assert rt == sv and len(rt["methods"]) == 7
    ok("baksmali 全量保留:每个标志、指令数、debug info 有则有无则无(.line 与剥壳现状一致)")

    print("3. jadx 侧:转译与失真标记")
    jv = java_view(cls)
    jby = {m["method"]: m for m in jv["methods"]}
    assert jby["access$000"]["notes"] == ["synthetic"]
    assert jby["run"]["notes"] == ["synthetic", "bridge"]
    assert jby["run"]["source_line"] == 0 and jby["onCreate"]["source_line"] == 17
    ok("synthetic/bridge 变注释;行号取自 debug info,被剥离时退化为近似值")

    weird = jby["weird"]
    assert "jadx: fallback" in weird["notes"] and weird["source_line"] == 30
    ok("反编译失败块显式留痕(README『无法 100%』的工程化落点:注释而非静默丢弃)")

    print("4. 反混淆改名通道")
    jv2 = java_view(cls, deobf={"com.a.Main": "com.a.Source", "weird": "parseHeader"})
    assert jv2["class"] == "com.a.Source" and jv2["methods"][6]["method"] == "parseHeader"
    ok("deobfuscator 是改名映射:改的是视图名,不动 smali 侧结构(smali 永远是 ground truth)")

    print("5. desugar 覆盖面(java8_support 页面口径)")
    assert desugar_covers("lambda expressions") is True
    assert desugar_covers("method references") is True
    assert desugar_covers("default and static interface methods") is True
    ok("lambda/方法引用/接口默认与静态方法:字节码层面就被 D8/R8 变换掉,smali 里看不到原语法")
    assert desugar_covers("MethodHandle.invoke / invokeExact") is False
    ok("MethodHandle.invoke/invokeExact 明确不被 desugar 支持(源码含它就是运行期硬错)")
    assert desugar_covers("try-with-resources") != True   # 版本附带条件
    assert desugar_covers("type annotations (TYPE_USE/PARAMETER)") != True
    ok("try-with-resources 到 AGP3.0+ 才全级别;type annotations 只有编译期语义——"
       "反编译出的 Java 源不等于运行期行为")

    print(f"\n共 {len(PASS)} 项断言全部通过")


if __name__ == "__main__":
    main()

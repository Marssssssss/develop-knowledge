# -*- coding: utf-8 -*-
"""smali(汇编/反汇编)与 jadx(反编译到 Java)两条还原路径的语义模型。

口径(实读源):
  dex 格式官方文档 source.android.com/docs/core/runtime/dex-format —— 访问标志位值
  smali/baksmali README —— 面向 dex 全功能(annotations/debug info/line info)的汇编/反汇编
  developer.android.com/studio/write/java8-support —— D8/R8 desugar 覆盖面
  skylot/jadx README —— "多数情况下无法 100% 反编译"、内置 deobfuscator

本文件的 smali_view/java_view 是**语义模型**(结构保真度对比),不是工具字面输出。
"""

# dex-format 官方访问标志(方法相关)
ACC_NATIVE = 0x100
ACC_BRIDGE = 0x40
ACC_SYNTHETIC = 0x1000
ACC_CONSTRUCTOR = 0x10000
ACC_DECLARED_SYNCHRONIZED = 0x20000

FLAG_NAMES = [
    (ACC_SYNTHETIC, "synthetic"),      # "not directly defined in source code"
    (ACC_BRIDGE, "bridge"),            # "added automatically by compiler as a type-safe bridge"
    (ACC_NATIVE, "native"),
    (ACC_CONSTRUCTOR, "constructor"),
    (ACC_DECLARED_SYNCHRONIZED, "declared-synchronized"),
]

# java8_support 页面列出的 D8/R8 desugar 语言特性覆盖面
DESUGAR_LANGUAGE = {
    "lambda expressions": True,
    "method references": True,
    "default and static interface methods": True,
    "repeating annotations": True,
    "try-with-resources": "AGP 3.0.0+ 扩展到所有 API 级别",
    "MethodHandle.invoke / invokeExact": False,   # 明确"不支持"
    "type annotations (TYPE_USE/PARAMETER)": "仅编译期,API<=24 运行期不可见",
}

# jadx README:"in most cases jadx can't decompile all 100% of the code"
FALLBACK_OPCODE = 0xff      # 模型里代表"该指令块反编译失败"
CUSTOM_NEWARRAY = 0xfe      # 模型里代表可反编译但源码形态有损的指令


class DexMethod:
    def __init__(self, name, access, ops, line=None):
        self.name, self.access, self.ops = name, access, ops
        self.line = line          # debug info 里的源码行号;None = 信息被剥离


class DexClass:
    def __init__(self, name, methods):
        self.name, self.methods = name, methods


# ---- 视图 1:smali(反汇编)——结构无损 ----

def smali_view(cls):
    """baksmali 语义:保留全部访问标志、全部指令、调试信息有则保留。"""
    out = []
    for m in cls.methods:
        flags = [label for bit, label in FLAG_NAMES if m.access & bit]
        entry = {"method": m.name, "flags": flags,
                 "op_count": len(m.ops),
                 "line": m.line,               # debug info 存在时 smali 逐指令带 .line
                 }
        out.append(entry)
    return {"class": cls.name, "methods": out, "lossless": True}


def smali_roundtrip(view):
    """汇编/反汇编互逆:视图字段可完整还原(README 的 full-functionality 语义)。"""
    return view  # 模型直通:字段一一对应,无损


# ---- 视图 2:jadx(反编译)——尽力还原 + 显式失真标记 ----

def java_view(cls, deobf=None):
    """jadx 语义:synthetic/bridge 转注释;失败块保留 fallback 注释;行号靠 debug info。"""
    rename = deobf or {}
    out = []
    for m in cls.methods:
        notes = []
        if m.access & ACC_SYNTHETIC:
            notes.append("synthetic")          # 编译器生成,源码里没有
        if m.access & ACC_BRIDGE:
            notes.append("bridge")
        fallback = FALLBACK_OPCODE in m.ops
        if fallback:
            notes.append("jadx: fallback")     # 反编译失败的块必须显式留痕
        out.append({
            "method": rename.get(m.name, m.name),
            "notes": notes,
            "source_line": m.line if m.line is not None else 0,  # 无 debug info → 近似行号
        })
    return {"class": rename.get(cls.name, cls.name), "methods": out}


def desugar_covers(feature):
    """D8/R8 语言特性覆盖查询(口径见 DESUGAR_LANGUAGE)。"""
    return DESUGAR_LANGUAGE[feature]

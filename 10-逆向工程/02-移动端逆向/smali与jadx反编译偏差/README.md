# smali 与 jadx 反编译偏差(汇编/反编译两条还原路径)

> 同一个 dex,baksmali 反汇编成 smali、jadx 反编译成 Java——**保真度完全不同**:
> smali 是 dex 的逐指令映射(可再汇编回等价 dex);jadx 是"尽力重建源码",
> 官方 README 明说 *in most cases jadx can't decompile all 100% of the code*。
> 逆向工作流必须知道哪一侧是 ground truth、哪些偏差是工具必然而非混淆。

## 1. 两条路径的本质

| 路径 | 输出 | 性质 | 保真度 |
| --- | --- | --- | --- |
| baksmali → smali | 汇编文本 | 可逆变换(smali README:dex 全功能含 annotations/debug info/line info) | **无损**,回汇编可得等价 dex |
| jadx → Java | 源码文本 | 不可逆重建(模式匹配 + 控制流重排) | **尽力**,失败块打 fallback 注释 |

## 2. 访问标志:dex 官方位值

| 标志 | 位值 | 说明(dex-format 官方表) |
| --- | --- | --- |
| ACC_BRIDGE | 0x40 | 编译器自动加的类型安全桥方法 |
| ACC_SYNTHETIC | 0x1000 | *not directly defined in source code* |
| ACC_NATIVE | 0x100 | native 实现 |
| ACC_CONSTRUCTOR | 0x10000 | 构造器/实例初始化器 |
| ACC_DECLARED_SYNCHRONIZED | 0x20000 | 方法级 synchronized |

- smali 侧:每个标志都渲染出来,`access$000`(synthetic)、桥方法一眼可辨;
- jadx 侧:synthetic/bridge 转成 `/* synthetic */` 类注释——**源码里本来就没有这些方法**,
  看到它们就知道在看编译器产物;
- 行号:jadx 的源码行来自 debug info;strip 后的 dex 反编译出的行号是**近似值**。

## 3. 反编译失败块:留痕而非丢弃

jadx 的工程化处理是给失败方法/块打 fallback 注释。判读原则:
**smali 里存在而 jadx 输出缺失/注释掉的逻辑,一律回 smali 侧核实**——
它可能是工具失败,也可能正是被保护的关键逻辑。

## 4. desugar:D8/R8 在编译期就抹掉的源码形态

developer.android.com 的 Java 8 支持页(desugaring):

- ✅ lambda 表达式、方法引用、接口 default/static 方法、重复注解——
  **字节码层面已被变换**,smali 里不会出现 Java 8 原语法;
- ✅ try-with-resources:AGP 3.0.0+ 才扩展到所有 API 级别(老版本编译产物里有手写 close 链);
- ❌ **MethodHandle.invoke / invokeExact 明确不被 desugar 支持**;
- ⚠️ type annotations(TYPE_USE/TYPE_PARAMETER)只有编译期语义,
  API≤24 运行期不可见——**反编译出的 Java 源 ≠ 运行期行为**。

## 5. 反混淆改名是视图层

jadx 内置 deobfuscator,本质是 `identifier → 新名` 的映射,只作用于展示;
smali 侧结构永远不变。跨工具协作时以 smali 名为准,各家改名表各自维护。

## 自检

`python selfcheck_deviation.py` —— 8 项断言:官方标志位值 / smali 无损视图 /
jadx 注释化与 fallback 留痕 / 改名映射 / desugar 覆盖面(含 MethodHandle 排除与
版本附带条件)。Go 侧 `deviation.go` 为同语义复刻(静态审查)。

## 参考资料(实读)

- [Dalvik Executable format(访问标志表)— source.android.com](https://source.android.com/docs/core/runtime/dex-format)
- [smali/baksmali README — JesusFreke/smali](https://github.com/JesusFreke/smali/blob/master/README.md)
- [Use Java 8 language features(desugaring)— developer.android.com](https://developer.android.com/studio/write/java8-support)
- [jadx README — skylot/jadx](https://github.com/skylot/jadx/blob/master/README.md)
- 本目录 [smali与Dalvik指令编码/](../smali与Dalvik指令编码/)、[DEX文件格式解析/](../DEX文件格式解析/)(前置)

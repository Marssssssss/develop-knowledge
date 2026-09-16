# Ghidra 反编译流水线

> 反编译器的输出之所以"像 C 又不像 C",根源是它在**中间表示层**做了大量规范化。
> 本 demo 按 Ghidra 官方文档给出的 15 阶段工作流,把其中最核心的
> 「P-code → SSA(Heritage)→ 死代码消除 → 项重写」四步落成可断言的模型。

## 一、简介

官方文档把反编译器描述为"编译器理论的标准数据结构与算法"的**逆用**:
编译器解析高级语言、生成 IR、再降到机器码;反编译器从机器码出发、升到 IR,
再**推断**出编译器当初丢掉的信息。它复用了编译器的 P-code(RTL)、SSA、基本块/CFG、
项重写、死代码消除、符号表;但有几件事编译器里没有对应物:

* **变量合并** —— 一个高级变量在机器码里可能散落在多个位置、多个时刻;
* **类型传播** —— 类型信息在二进制里基本不存在,只能从使用方式反推;
* **控制流结构化** —— 要把 `goto` 图重新拼成 `if/else/while/switch`;
* **函数原型恢复**、**表达式恢复**。

## 二、官方 15 阶段工作流(实读原文)

| # | 阶段 | 本 demo 是否覆盖 |
| --- | --- | --- |
| 01 | Specify Entry Point | — |
| 02 | Generate Raw **P-code**(由 SLEIGH 从处理器规格文件翻译机器指令) | 建模为 IR |
| 03 | Generate Basic Blocks and the CFG(**基于 p-code 而非机器指令**建块) | 建模为 IR |
| 04 | Inspect Sub-functions(恢复参数信息、间接调用改直接) | — |
| 05 | Adjust/Annotate P-code(插入 COPY/LOAD/INDIRECT/RETURN 输入) | 部分 |
| 06 | The Main Simplification Loop | **主体** |
| 07 | Perform Final P-code Transformations | **往返演示** |
| 08 | Exit SSA Form and Merge Low-level Variables (phase 1) | 说明 |
| 09 | Determine Expressions and Temporary Variables | — |
| 10 | Merge Low-level Variables (phase 2) | — |
| 11 | Add Type Casts | — |
| 12 | Establish Function's Prototype | — |
| 13 | Select Variable Names | — |
| 14 | Do Final Control Flow Structuring | 见 `控制流图与结构化反编译` |
| 15 | Emit Final C Tokens(Oppen 漂亮打印) | — |

06 主简化循环的六个子步骤:**a 生成 SSA / b 消除死代码 / c 传播局部类型 /
d 项重写 / e 调整 CFG / f 恢复控制流结构**。

## 三、原理详解

### 3.1 P-code:为逆向而生的 RTL

p-code 是一套**与处理器无关**的寄存器传输语言,由 SLEIGH 处理器规格文件把机器指令
逐条翻译过来。它的几个设计选择直接服务于逆向:

* **基本块建在 p-code 上而非机器指令上** —— 一条 `x86` 指令可能展开成十几条 p-code,
  块边界以 p-code 的分支语义为准;
* **`INDIRECT` 操作**用来表达"输出由输入经某种(常常未知)的间接效果派生",
  它是别名信息的载体(跨子函数调用的别名也靠它标注);
* **`RETURN` 会被改写**:隐藏返回地址的使用、把返回值变成它的一个输入;
* **栈引用先表示为 `LOAD`/`STORE`**,等项重写跑完再"提升"成 SSA 树里的完整变量 ——
  所以官方明确说 SSA 构造是**增量式**的,"往往需要 1 次或多次额外遍历才能完全构建"。

### 3.2 Heritage:phi 放置与重命名

```
支配树(立即支配者)──► 支配边界 DF ──► 在 DF 上放 MULTIEQUAL ──► 支配树 DFS 重命名
```

本 demo 复刻的算法:

1. **支配者**:逆后序 + 迭代式不动点(Cooper-Harvey-Kennedy);
2. **支配边界**:对每个多前驱块 `y`,沿每个前驱 `p` 的支配链上溯到 `idom[y]` 为止,
   途中的块都加入 `DF[...] ∪= {y}`;
3. **phi 放置**:把 phi 本身也当作"定义"重新投入工作列表,迭代到不动点
   —— 这条回边正是循环变量需要 `phi` 的原因;
4. **重命名**:沿支配树 DFS,每个变量一个版本栈,进入块压栈、离开块弹栈;
   **phi 的第 k 个入边要取「前驱 k 路径上」的版本**,所以补边这一步必须在递归进子树之前做。

实测(循环求和函数):

```
B1   (pred=B0,B2 succ=B2,B3)
    i#2 = MULTIEQUAL i#1 i#3
    t#2 = MULTIEQUAL t#1 t#3
    cc#1 = INT_LESS i#2 n#0
```

`cc` 只在 B1 内定义并使用,**向上暴露分析判定它不活跃**,因此不在 `DF[B1]={B1}`
处插 phi —— 这就是"最小 SSA 需要活跃性检查"的落地,少了这一步会凭空多出一条 phi。

### 3.3 死代码消除与项重写:目标不是优化

官方原文强调:死代码消除对反编译器**至关重要**,因为大量机器指令会写标志位等
副作用,而这些副作用在代码的某些点上与该函数无关;难点在于分不清临时/局部/全局变量,
而且编译器常把 1~2 字节变量塞进 4 字节寄存器,高位会残留垃圾 ——
所以反编译器**精确到 bit** 地判死并及时截断。

项重写则明确**不以优化为目标**,而以「便于人类分析师阅读」为目标做**简化与规范化**。
最典型的痕迹是:主简化循环把**所有 `INT_SUB` 规范化为「加二进制补码」**
(`x - y` → `x + (-y)`),好让规则集更小;到第 07 阶段再转回来。
若把这一步反过来理解,就能解释反编译输出里那些莫名其妙的补码加法。

## 四、环境与运行

无第三方依赖;不需要安装 Ghidra。

```bash
python pipeline_check.py     # SSA 构造 + DCE + 项重写,全部断言实跑
go run .                     # 同题 Go 实现(本机无 Go 工具链,走人工审查)
```

## 五、关键代码

* `pcode_ssa.py::Func.idoms()/df()/liveness()` —— 支配树、支配边界、活跃性;
* `heritage.go::(*Func).PlacePhis()/Rename()` —— phi 放置与版本栈重命名;
* `pipeline_check.py::dce()/rewrite()/normalize_sub()/denormalize_sub()`;
* `ssa_verify.py::verify_ssa()` —— 三条性质校验。

## 六、性能边界

* 支配者不动点是 O(V·E) 级(每次遍历若干轮),实测在 4~5 块的小函数上是瞬时;
* phi 放置按变量逐个跑工作列表,复杂度与「定义点 × 支配边界」成正比;
* 真实反编译器还要处理**别名**(`INDIRECT`/`LoadGuard`),本 demo 用
  「输出是否出现在任何 use 列表」做 bit 级保守近似,不做指针别名分析。

## 七、注意事项与常见坑

1. **phi 的入边是"边上的使用"**:校验时应比较「定义是否支配对应**前驱**」,
   而不是「支配 phi 所在块」。本 demo 首版按后者写,循环变量被误报两条。
2. **插 phi 要做活跃性检查**:否则同块内定义-使用的临时量(如比较结果 `cc`)
   会在 `DF[B]={B}` 的自环处插出无用 phi。
3. **RPO 顺序是迭代式支配算法收敛的前提**;用块声明顺序会得到错误 idom。
4. **`__slots__` 与动态属性**:给 `Op` 临时挂 `src_name` 会直接抛 `AttributeError`,
   这类"小结构体"要预留字段。
5. **别把 SUB→ADD 的规范化当成编译器的优化**,它是反编译器为了缩小规则集做的表示选择。
6. 本 demo 不替代真实反编译器:类型传播、变量合并、控制流结构化都在其之外
   (后者见 `控制流图与结构化反编译`)。

## 八、参考资料(已读)

* [Decompiler Analysis Engine — Ghidra 官方反编译器文档总览(Main Work Flow 15 阶段 + Capabilities + p-code/SLEIGH)](https://ghidradocs.com/11.4_PUBLIC/docs/DecompilerDoxygen/html/index.html)
* [Decompiler System — google/ghidra 源码走读(多趟流水线、Funcdata/Varnode/PcodeOp 三大结构)](https://deepwiki.com/NationalSecurityAgency/ghidra/2-decompiler-system)
* [SSA Construction and Data Flow — Heritage 系统(支配边界 phi 放置、支配树重命名、LocationMap 增量式 heritaged 区段、LoadGuard)](https://deepwiki.com/NationalSecurityAgency/ghidra/2.4-ssa-construction-and-data-flow)
* [核心反编译过程 — Heritage/ruleaction/SubvariableFlow 的源码位置与职责](https://deepwiki.org.cn/NationalSecurityAgency/ghidra/2.1-core-decompilation-process)
* [Decompiler Overview — thixotropist(反编译器进程模型、p-code 逐指令展开示例、类型转换导致 Varnode 短命)](https://thixotropist.github.io/ghidra_decompiler_commons/docs/ghidra_decompiler_internals/overview)

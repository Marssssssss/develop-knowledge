# 二进制逆向

> 从可执行文件还原软件逻辑。静态侧核心工具为 Ghidra：由 NSA 研究理事会（Research Directorate）开发并开源的软件逆向工程（SRE）框架，具备反汇编、汇编、反编译、图谱（graphing）与脚本能力，支持大量处理器指令集与可执行格式，可交互式或自动化（headless）运行，脚本可用 Java 或 Python 编写。

## 核心研究主题

- **静态分析**：反汇编与反编译（Ghidra / IDA / radare2）、交叉引用、函数与数据类型识别
- **可执行文件格式**：ELF（Linux）/ PE（Windows）/ Mach-O（macOS）结构与加载过程
- **指令集**：x86 / x64 调用约定与栈帧、ARM / AArch64
- **动态分析**：调试器（gdb / x64dbg / lldb）、trace 与插桩
- **脚本化与自动化**：Ghidra headless 批处理、自定义分析脚本（Java / Python）
- **实战**：crackme、CTF 逆向题、静态特征匹配（YARA）

## 已完成 demo 索引

| ID | Demo | 知识点 | 语言 |
| --- | --- | --- | --- |
| 087 | [ELF文件解析/](./ELF文件解析/) | Elf64_Ehdr/Shdr/Sym 手工解析(elf(5) 布局、节名间接寻址、strip 判定) | Python / C |
| 088 | [PE文件解析/](./PE文件解析/) | DOS 头→COFF→可选头→节表→导入表(Microsoft PE 规范、RVA→文件偏移换算、IAT 双表) | Python |
| 089 | [x64调用约定/](./x64调用约定/) | System V AMD64 psABI(参数寄存器 rdi..r9、栈传递、16B 对齐、callee-saved、汇编猜签名) | C / Python |
| 090 | [GOT与PLT延迟绑定/](./GOT与PLT延迟绑定/) | PLT 三段式 + GOT 槽惰性解析(JMP_SLOT、_dl_runtime_resolve 5 步、GOT 劫持与 RELRO) | Python / C |
| 091 | [YARA静态特征匹配/](./YARA静态特征匹配/) | 规则四段式 + 匹配引擎语义(xor/wide/nocase 展开、hex 通配/跳变、#/@/of 条件量词) | Python |
| 162 | [x86-64指令编码/](./x86-64指令编码/) | 变长指令解码(legacy prefix 四组 / REX 0x40-0x4F / ModRM / SIB / RIP 相对 / 15 字节上限) | C / Python |
| 163 | [ptrace断点调试器/](./ptrace断点调试器/) | 软件断点四步生命周期(埋 0xCC → SIGTRAP → 恢复原字节+RIP 回退 → 单步 → 重埋) | C / Python |
| 164 | [栈溢出与ROP/](./栈溢出与ROP/) | 栈帧与 rbp 链 / NX 边界 / ROP 链模型(`ret` = `pop rip`) / CET SHSTK 与 16B 对齐 | C / Python / Go |
| 165 | [Mach-O文件解析/](./Mach-O文件解析/) | mach_header_64 → load_command[] → segment/section(8 字节对齐、lc_str 相对偏移、节编号从 1 起) | Python / Go |
| 166 | [控制流图与结构化反编译/](./控制流图与结构化反编译/) | leader 切块 / 支配树与 idom / 回边与自然循环 / 可归约性 / follow 节点结构化 | Python / Go |
| 257 | [ARM64指令集与调用约定/](./ARM64指令集与调用约定/) | A64 定长 32 位编码(组标识位 [28:24]/[28:23]、字段级还原、别名折叠 `mov`/`ret` 是伪指令、SP 与 XZR 共用编号 31)+ AAPCS64(x0-x7 传参、x8 间接结果、x16/x17 veneer 可破坏、x19-x28 callee-saved、SP mod 16、帧记录双字与回溯链) | Python / C |
| 258 | [Ghidra反编译流水线/](./Ghidra反编译流水线/) | 官方 Main Work Flow 15 阶段 + 主简化循环六步(SSA/死代码/类型传播/项重写/CFG 调整/控制流结构化) + SLEIGH→p-code + Heritage(支配树/支配边界/phi 放置/支配树 DFS 重命名)+ INDIRECT 别名标注与 INT_SUB 规范化 | Python / Go |
| 259 | [Ghidra-headless自动化/](./Ghidra-headless自动化/) | analyzeHeadless 命令行组合约束(-import/-process 互斥)+ HeadlessContinuationOption 4×4 合并规则 + `-okToDelete` 安全门 + `getScriptArgs()` 与 askXxx 的消费顺序 | Python / Go |
| 260 | [二进制差分与函数匹配/](./二进制差分与函数匹配/) | BinDiff 三类指纹(官方 signature 三元组、prime 素数乘积及其 uint64 回溢、MD index 六项加权公式与"求和先排序")+ 分阶段匹配与 drill down + confidence S 型压扁 + similarity 权重(含只统计非库函数) | Python / Go |
| 261 | [Ghidra脚本化批量重命名/](./Ghidra脚本化批量重命名/) | SourceType 优先级链(USER_DEFINED > IMPORTED > ANALYSIS > DEFAULT)+ createLabel 重名幂等与 makeUnique 口径 + 计划/预演/事务三段式(非法名整批回滚) | Python / Go |

## 待研究

- [x] Ghidra headless 自动化分析流程（analyzeHeadless）→ 259 Ghidra-headless自动化
- [ ] Ghidra 反编译输出与真实源码的差异（伪 C 的失真点）
- [x] ELF 动态链接机制（.got / .plt / 延迟绑定）→ 090 GOT与PLT延迟绑定
- [x] x64 System V 调用约定最小示例 → 089 x64调用约定
- [x] ELF/PE 文件格式结构解析 → 087/088
- [x] Mach-O 文件格式（macOS 侧）→ 165 Mach-O文件解析
- [x] 静态特征匹配（YARA 规则语法与匹配语义）→ 091
- [x] 用 Ghidra 脚本批量重命名函数 → 261 Ghidra脚本化批量重命名
- [x] 反汇编器原理（x86 指令长度解码 / length disassembler）→ 162 x86-64指令编码
- [x] 动态调试原理（断点如何实现 / ptrace）→ 163 ptrace断点调试器
- [x] 栈溢出与 ROP 原理 → 164 栈溢出与ROP
- [x] 控制流图与结构化反编译 → 166 控制流图与结构化反编译
- [x] ARM64 指令集速览 → 257 ARM64指令集与调用约定
- [x] Ghidra 反编译器内部流水线（15 阶段 / Heritage / p-code）→ 258 Ghidra反编译流水线
- [x] 二进制差分与函数匹配（BinDiff 指纹与相似度口径）→ 260 二进制差分与函数匹配
- [ ] 增量式重新分析对已有用户命名的保留边界（重跑分析会不会动 USER_DEFINED）
- [ ] 二进制差分工具工程化对比（BinDiff / Diaphora / BinDiffNG 的相似度口径差异）

## 参考资料（已读）

- [Ghidra — GitHub 官方仓库（NSA Research Directorate）](https://github.com/NationalSecurityAgency/ghidra)
- [elf(5) — Linux man page（man7.org），087/090 demo 依据](https://man7.org/linux/man-pages/man5/elf.5.html)
- [PE Format — Microsoft Learn 官方规范，088 demo 依据](https://learn.microsoft.com/en-us/windows/win32/debug/pe-format)
- [System V ABI AMD64 psABI（refspecs.linuxfoundation.org PDF），089 demo 依据](https://refspecs.linuxfoundation.org/elf/x86_64-abi-0.95.pdf)
- [Linkers part 4: Shared Libraries — Ian Lance Taylor，090 demo 依据](https://www.airs.com/blog/archives/41)
- [YARA 官方文档 writing-rules，091 demo 依据](https://yara.readthedocs.io/en/stable/writingrules.html)
- [X86-64 Instruction Encoding — OSDev Wiki，162 demo 依据](https://wiki.osdev.org/X86-64_Instruction_Encoding)
- [Intel 64 and IA-32 Architectures SDM Vol.2 — felixcloutier 镜像，162/163/164 demo 依据](https://www.felixcloutier.com/x86/)
- [ptrace(2) — Linux man page（man7.org），163 demo 依据](https://man7.org/linux/man-pages/man2/ptrace.2.html)
- [Stack frame layout on x86-64 — Eli Bendersky，164 demo 依据](https://eli.thegreenplace.net/2011/09/06/stack-frame-layout-on-x86-64)
- [Smashing The Stack For Fun And Profit — Aleph1, Phrack #49/14，164 demo 依据](http://phrack.org/issues/49/14.html)
- [The Geometry of Innocent Flesh on the Bone — Hovav Shacham, CCS 2007，164 demo 依据](https://hovav.net/ucsd/dist/geometry.pdf)
- [OSX ABI Mach-O File Format Reference — aidansteele，165 demo 依据](https://github.com/aidansteele/osx-abi-macho-file-format-reference)
- [Bytecode-Level Analysis and Optimization of Java Classes — Nystrom, Cornell §2.1，166 demo 依据](http://www.cs.cornell.edu/nystrom/papers/nystrom-ms-thesis.pdf)
- [Structuring Decompiled Graphs — Cristina Cifuentes, LNCS 1994，166 demo 依据](https://link.springer.com/content/pdf/10.1007%2F3-540-61053-7_55.pdf)
- [A64 ISA — Index by Encoding: Data Processing -- Register（ddi0602），257 demo 依据](https://developer.arm.com/documentation/ddi0602/2023-03/Index-by-Encoding/Data-Processing----Register)
- [Learn the architecture — A64 ISA Guide 102374 §Procedure Call Standard，257 demo 依据](https://developer.arm.com/documentation/102374/0103/Procedure-Call-Standard)
- [AAPCS64 — Procedure Call Standard for the Arm 64-bit Architecture（abi-aa），257 demo 依据](https://raw.githubusercontent.com/ARM-software/abi-aa/main/aapcs64/aapcs64.rst)
- [Decompiler Analysis Engine — Ghidra 官方反编译器文档总览（Main Work Flow 15 阶段 / p-code），258 demo 依据](https://ghidradocs.com/11.4_PUBLIC/docs/DecompilerDoxygen/html/index.html)
- [SSA Construction and Data Flow — Heritage 系统（支配边界 phi 放置 / 支配树重命名），258 demo 依据](https://deepwiki.com/NationalSecurityAgency/ghidra/2.4-ssa-construction-and-data-flow)
- [Headless Analyzer README — 官方完整参数表与 4×4 合并规则，259 demo 依据](https://ghidradocs.com/12.0_PUBLIC/support/analyzeHeadlessREADME.html)
- [Understanding BinDiff — google/bindiff docs/concepts.md（算法清单 / confidence / similarity 权重），260 demo 依据](https://raw.githubusercontent.com/google/bindiff/main/docs/concepts.md)
- [graph_util.h — CalculateMdIndexInternal 精确公式与默认权重（镜像读取），260 demo 依据](https://github.com/codingman/bindiff/blob/main/graph_util.h)
- [FlatProgramAPI — 官方 Javadoc（createLabel/createFunction/setEOLComment 签名与语义），261 demo 依据](https://ghidradocs.com/12.1_PUBLIC/docs/GhidraAPI_javadoc/api/ghidra/program/flatapi/FlatProgramAPI.html)
- [SourceType — 官方 Javadoc（USER_DEFINED > IMPORTED > ANALYSIS > DEFAULT 优先级原文），261 demo 依据](https://ghidradocs.com/11.0_PUBLIC/docs/GhidraAPI_javadoc/api/ghidra/program/model/symbol/SourceType.html)
- [Symbol Management — Ghidra 官方文档（Batch Renaming / 命名规则 / 自动生成名），261 demo 依据](https://mintlify.wiki/NationalSecurityAgency/ghidra/guide/symbols)
- [Java Scripts — Ghidra 官方脚本文档（类层次 / 脚本元数据 / 事务写法），261 demo 依据](https://mintlify.wiki/NationalSecurityAgency/ghidra/scripting/java-scripts)

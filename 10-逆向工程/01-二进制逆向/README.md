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

## 待研究

- [ ] Ghidra headless 自动化分析流程（analyzeHeadless）
- [ ] Ghidra 反编译输出与真实源码的差异（伪 C 的失真点）
- [x] ELF 动态链接机制（.got / .plt / 延迟绑定）→ 090 GOT与PLT延迟绑定
- [x] x64 System V 调用约定最小示例 → 089 x64调用约定
- [x] ELF/PE 文件格式结构解析 → 087/088
- [x] Mach-O 文件格式（macOS 侧）→ 165 Mach-O文件解析
- [x] 静态特征匹配（YARA 规则语法与匹配语义）→ 091
- [ ] 用 Ghidra 脚本批量重命名函数
- [x] 反汇编器原理（x86 指令长度解码 / length disassembler）→ 162 x86-64指令编码
- [x] 动态调试原理（断点如何实现 / ptrace）→ 163 ptrace断点调试器
- [x] 栈溢出与 ROP 原理 → 164 栈溢出与ROP
- [x] 控制流图与结构化反编译 → 166 控制流图与结构化反编译

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

# 10 逆向工程

> 逆向工程（Reverse Engineering）：从编译产物、二进制与协议中还原软件的结构、算法与行为，是安全研究、漏洞挖掘、恶意样本分析与协议分析的基础能力。2026-09-12 经用户确认新增为顶层类目。

## 子领域

| 子目录 | 说明 |
| --- | --- |
| [01-二进制逆向/](./01-二进制逆向/) | 反汇编/反编译（Ghidra 等）、可执行文件格式、指令集、动态调试 |
| [02-移动端逆向/](./02-移动端逆向/) | Android/iOS 静态分析、Frida 动态插桩、加密协议分析 |
| [03-协议逆向/](./03-协议逆向/) | 网络协议还原（NetT/ExeT 两族）、字段边界与语义推断、加密流量指纹（JA3/JA4） |

## 待拓展子类目（由自动巡检按类目拓展规则逐步补建）

- [x] 03-协议逆向（网络协议还原、私有协议与加密流量分析）→ 2026-09-14 建目录 + README（含 PRE 方法谱系与 JA3/JA3S 指纹）
- [ ] 04-加壳与混淆（UPX / VMProtect、脱壳、去混淆）
- [ ] 05-游戏逆向（Unity/IL2CPP、反作弊对抗，与 `01-游戏开发` 互补）
- [ ] 06-恶意样本分析（沙箱、行为提取、家族归类）

## 待研究

- [ ] Ghidra 反编译器原理与脚本化分析
- [ ] Frida hook 原理（QuickJS 注入 + 双向通信通道）
- [x] ELF 与 PE 文件格式对比 → 087 ELF文件解析 / 088 PE文件解析（对比表见 165 Mach-O README §对比）
- [ ] ARM64 指令集速览
- [x] Mach-O 文件格式（macOS 侧）→ 165 Mach-O文件解析
- [x] x86-64 变长指令解码 → 162 x86-64指令编码
- [x] 软件断点与 ptrace 调试原理 → 163 ptrace断点调试器
- [x] 栈溢出利用链与 ROP → 164 栈溢出与ROP
- [x] 控制流图与结构化反编译 → 166 控制流图与结构化反编译
- [x] 报文序列对齐切字段（NW/SW）→ 192 报文序列对齐（03-协议逆向）
- [x] 信息熵字段边界检测（BinaryInferno）→ 193 信息熵字段边界检测（03-协议逆向）
- [x] 协议状态机恢复（Veritas P-PSM）→ 194 协议状态机恢复（03-协议逆向）
- [x] JA3/JA4 TLS 握手指纹计算器 → 195 JA3与JA4指纹（03-协议逆向）
- [x] Polyglot 污点分析四大启发式 → 196 Polyglot污点启发式（03-协议逆向）

## 参考资料（已读）

- [Ghidra — NSA 研究理事会维护的开源 SRE 框架](https://github.com/NationalSecurityAgency/ghidra)
- [Frida 官方文档 — 动态插桩工具包](https://frida.re/docs/home/)
- [X86-64 Instruction Encoding — OSDev Wiki（162）](https://wiki.osdev.org/X86-64_Instruction_Encoding)
- [Intel SDM Vol.2 — felixcloutier 指令参考镜像（162/163/164）](https://www.felixcloutier.com/x86/)
- [ptrace(2) — man7.org（163）](https://man7.org/linux/man-pages/man2/ptrace.2.html)
- [OSX ABI Mach-O File Format Reference — aidansteele（165）](https://github.com/aidansteele/osx-abi-macho-file-format-reference)
- [Bytecode-Level Analysis and Optimization of Java Classes — Nystrom, Cornell §2.1（166）](http://www.cs.cornell.edu/nystrom/papers/nystrom-ms-thesis.pdf)
- [Structuring Decompiled Graphs — Cristina Cifuentes, LNCS 1994（166）](https://link.springer.com/content/pdf/10.1007%2F3-540-61053-7_55.pdf)
- [JA3: A method for profiling SSL/TLS Clients — salesforce/ja3（03-协议逆向）](https://github.com/salesforce/ja3)
- [Protocol Reverse-Engineering Methods and Tools: A Survey — Computer Networks（03-协议逆向）](https://www.sciencedirect.com/science/article/pii/S0140366421004382)
- [BinPRE: Enhancing Field Inference in Binary Analysis Based PRE — arXiv 2409.01994（03-协议逆向）](https://arxiv.org/pdf/2409.01994v1)

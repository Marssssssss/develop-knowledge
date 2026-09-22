# 10 逆向工程

> 逆向工程（Reverse Engineering）：从编译产物、二进制与协议中还原软件的结构、算法与行为，是安全研究、漏洞挖掘、恶意样本分析与协议分析的基础能力。2026-09-12 经用户确认新增为顶层类目。

## 子领域

| 子目录 | 说明 |
| --- | --- |
| [01-二进制逆向/](./01-二进制逆向/) | 反汇编/反编译（Ghidra 等）、可执行文件格式、指令集、动态调试 |
| [02-移动端逆向/](./02-移动端逆向/) | Android/iOS 静态分析、Frida 动态插桩、加密协议分析 |
| [03-协议逆向/](./03-协议逆向/) | 网络协议还原（NetT/ExeT 两族）、字段边界与语义推断、加密流量指纹（JA3/JA4） |
| [04-固件与嵌入式逆向/](./04-固件与嵌入式逆向/) | 固件镜像头（uImage/FIT）、SquashFS/JFFS2+UBI、Cortex-M 向量表与基址定位、MCUboot 验签 |

## 待拓展子类目（由自动巡检按类目拓展规则逐步补建）

> 编号以「目录实际占用」为准；本表愿望清单里原写的 04-加壳与混淆 因索引表第 57 项（固件与嵌入式逆向）先落成 04，顺延为 05。

- [x] 03-协议逆向（网络协议还原、私有协议与加密流量分析）→ 2026-09-14 建目录 + README（含 PRE 方法谱系与 JA3/JA3S 指纹）
- [x] 04-固件与嵌入式逆向（镜像头 / 闪存文件系统 / 基址定位 / 安全启动）→ 2026-09-22 建目录 + README + 5 demo（591-595）
- [ ] 05-加壳与混淆（UPX / VMProtect、脱壳、去混淆）
- [ ] 06-游戏逆向（Unity/IL2CPP、反作弊对抗，与 `01-游戏开发` 互补）
- [ ] 07-恶意样本分析（沙箱、行为提取、家族归类）

## 待研究

- [x] Ghidra 反编译器原理与脚本化分析 → 258 Ghidra反编译流水线（15 阶段/Heritage）+ 259 Ghidra-headless自动化 + 261 Ghidra脚本化批量重命名
- [x] Frida hook 原理（QuickJS 注入 + 双向通信通道）→ 312 Frida-Java层hook + 313 Frida-Interceptor-native-hook + 316 Objection与Frida（02-移动端逆向）
- [x] DEX/Dalvik 指令与 Stalker 跟踪 → 482 DEX文件格式解析 + 483 smali与Dalvik指令编码 + 484 FridaStalker指令级跟踪（02-移动端逆向）
- [x] iOS 砸壳与移动端反调试对抗 → 485 iOS砸壳与加密镜像 + 486 反调试与越狱检测对抗（02-移动端逆向）
- [x] ELF 与 PE 文件格式对比 → 087 ELF文件解析 / 088 PE文件解析（对比表见 165 Mach-O README §对比）
- [x] ARM64 指令集速览 → 257 ARM64指令集与调用约定（01-二进制逆向）
- [x] Mach-O 文件格式（macOS 侧）→ 165 Mach-O文件解析
- [x] x86-64 变长指令解码 → 162 x86-64指令编码
- [x] 软件断点与 ptrace 调试原理 → 163 ptrace断点调试器
- [x] 栈溢出利用链与 ROP → 164 栈溢出与ROP
- [x] 控制流图与结构化反编译 → 166 控制流图与结构化反编译
- [x] 二进制差分（BinDiff 指纹与相似度口径）→ 260 二进制差分与函数匹配（01-二进制逆向）
- [x] 报文序列对齐切字段（NW/SW）→ 192 报文序列对齐（03-协议逆向）
- [x] 信息熵字段边界检测（BinaryInferno）→ 193 信息熵字段边界检测（03-协议逆向）
- [x] 协议状态机恢复（Veritas P-PSM）→ 194 协议状态机恢复（03-协议逆向）
- [x] JA3/JA4 TLS 握手指纹计算器 → 195 JA3与JA4指纹（03-协议逆向）
- [x] Polyglot 污点分析四大启发式 → 196 Polyglot污点启发式（03-协议逆向）
- [x] Discoverer 递归聚类与 FD 格式区分符 → 372 Discoverer递归聚类（03-协议逆向）
- [x] AutoFormat 上下文感知执行监视与并行字段 → 373 AutoFormat执行上下文（03-协议逆向）
- [x] Tupni 加权 k-Set Packing 与循环内记录边界 → 374 Tupni记录序列（03-协议逆向）
- [x] ReFormat 加密报文相位划分与数据生命周期 → 375 ReFormat加密报文（03-协议逆向）
- [x] bit 级字段切分（位级相关性判据与常量位能力边界）→ 376 bit级字段切分（03-协议逆向）
- [x] 固件整包解构（镜像头 → 基址 → 文件系统 → 验签）→ 591 uImage与FIT + 592 SquashFS + 593 JFFS2与UBI + 594 Cortex-M向量表 + 595 MCUboot（04-固件与嵌入式逆向）
- [ ] 加壳与去混淆入门（UPX 壳结构与手工脱壳边界）

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
- [RFC 1035 §4.1.1 — DNS 头部标志位布局，bit 级切分的真值来源（376）](https://www.rfc-editor.org/rfc/rfc1035.txt)
- [Discoverer: Automatic Protocol Reverse Engineering from Network Traces — USENIX Security 2007（372）](https://www.usenix.org/legacy/events/sec07/tech/full_papers/cui/cui.pdf)
- [Tupni: Automatic Reverse Engineering of Input Formats — CCS 2008（374）](https://www.microsoft.com/en-us/research/wp-content/uploads/2016/02/tupni-ccs08.pdf)
- AutoFormat: Automatic Protocol Format Reverse Engineering through Context-Aware Monitored Execution — Zhiqiang Lin, Xuxian Jiang, Dongyan Xu, Xinyuan Zhang, **NDSS 2008**（373；PDF 实读 17 页，下载链接未回溯核对故不著录 URL）
- ReFormat: Automatic Reverse Engineering of Encrypted Messages — Zhi Wang, Xuxian Jiang, Weidong Cui, Xinyuan Wang, **ESORICS 2009**（375；PDF 实读 16 页，下载链接未回溯核对故不著录 URL）
- [AAPCS64 — Procedure Call Standard for the Arm 64-bit Architecture（257）](https://raw.githubusercontent.com/ARM-software/abi-aa/main/aapcs64/aapcs64.rst)
- [A64 ISA Index by Encoding（ddi0602，257）](https://developer.arm.com/documentation/ddi0602/2023-03/Index-by-Encoding/Data-Processing----Register)
- [Decompiler Analysis Engine — Ghidra 官方反编译器文档（258）](https://ghidradocs.com/11.4_PUBLIC/docs/DecompilerDoxygen/html/index.html)
- [SSA Construction and Data Flow — Heritage 系统（258）](https://deepwiki.com/NationalSecurityAgency/ghidra/2.4-ssa-construction-and-data-flow)
- [Headless Analyzer README — 官方参数表与 4×4 合并规则（259）](https://ghidradocs.com/12.0_PUBLIC/support/analyzeHeadlessREADME.html)
- [Understanding BinDiff — google/bindiff docs/concepts.md（260）](https://raw.githubusercontent.com/google/bindiff/main/docs/concepts.md)
- [graph_util.h — CalculateMdIndexInternal 精确公式（260）](https://github.com/codingman/bindiff/blob/main/graph_util.h)
- [FlatProgramAPI — 官方 Javadoc（261）](https://ghidradocs.com/12.1_PUBLIC/docs/GhidraAPI_javadoc/api/ghidra/program/flatapi/FlatProgramAPI.html)
- [SourceType — 官方 Javadoc，符号来源优先级链（261）](https://ghidradocs.com/11.0_PUBLIC/docs/GhidraAPI_javadoc/api/ghidra/program/model/symbol/SourceType.html)
- [Symbol Management — Ghidra 官方文档，Batch Renaming 与命名规则（261）](https://mintlify.wiki/NationalSecurityAgency/ghidra/guide/symbols)
- [Frida 官方文档 — Android 与 JavaScript API（312/313/316）](https://frida.re/docs/android/)
- [APK signature scheme v2 / v3 — AOSP 官方中国镜像（314）](https://source.android.google.cn/docs/security/features/apksigning/v2)
- [MASTG-TECH-0012: Bypassing Certificate Pinning — OWASP MAS（315）](https://mas.owasp.org/MASTG/techniques/android/MASTG-TECH-0012)
- [objection 官方 README / Wiki / pinning.ts 源码（315/316）](https://github.com/sensepost/objection)
- [Dalvik 可执行文件格式 / 字节码 / 指令格式 — AOSP 官方中国镜像（482/483）](https://source.android.google.cn/docs/core/dalvik/dex-format)
- [Frida JavaScript API — Stalker 一节（484）](https://frida.re/docs/javascript-api/)
- [frida-gum 源码 gum/gumevent.h — GumEventType 与六个事件结构体（484）](https://github.com/frida/frida-gum/blob/main/gum/gumevent.h)
- [xnu 源码 EXTERNAL_HEADERS/mach-o/loader.h — encryption_info_command_64 与 MH_* 常量（485）](https://github.com/apple-oss-distributions/xnu/blob/main/EXTERNAL_HEADERS/mach-o/loader.h)
- [MASTG-TEST-0046 Android 反调试检测与绕过（486）](https://mas.owasp.org/MASTG/tests/android/MASVS-RESILIENCE/MASTG-TEST-0046/)
- [MASTG-TEST-0354 Runtime Use of Hook Detection Techniques（486）](https://mas.owasp.org/MASTG/tests/ios/MASVS-RESILIENCE/MASTG-TEST-0354/)
- [MASTG-TEST-0240 / 0241 越狱检测的静态与运行时形态（486）](https://mas.owasp.org/MASTG/tests/ios/MASVS-RESILIENCE/MASTG-TEST-0240/)
- [Flattened Image Tree Specification v1.0（fitspec.osfw.foundation，591）](https://fitspec.osfw.foundation/)
- [u-boot `include/image.h` — legacy_img_hdr 与 IH_* 枚举（591）](https://github.com/u-boot/u-boot/blob/master/include/image.h)
- [Linux `include/linux/crc32.h` — 明确 "does not invert"（591/593）](https://github.com/torvalds/linux/blob/master/include/linux/crc32.h)
- [Linux `fs/squashfs/squashfs_fs.h`（592）](https://github.com/torvalds/linux/blob/master/fs/squashfs/squashfs_fs.h)
- [Linux `include/uapi/linux/jffs2.h`（593）](https://github.com/torvalds/linux/blob/master/include/uapi/linux/jffs2.h)
- [Linux `drivers/mtd/ubi/ubi-media.h`（593）](https://github.com/torvalds/linux/blob/master/drivers/mtd/ubi/ubi-media.h)
- [CMSIS_5 `core_cm3.h` — SCB->VTOR 位域（594）](https://github.com/ARM-software/CMSIS_5/blob/master/CMSIS/Core/Include/core_cm3.h)
- [Zephyr `arch/arm/core/cortex_m/vector_table.S`（594）](https://github.com/zephyrproject-rtos/zephyr/blob/main/arch/arm/core/cortex_m/vector_table.S)
- [MCUboot `docs/design.md` §Image format（595）](https://github.com/mcu-tools/mcuboot/blob/main/docs/design.md)

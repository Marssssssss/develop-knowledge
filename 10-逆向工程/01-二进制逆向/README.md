# 二进制逆向

> 从可执行文件还原软件逻辑。静态侧核心工具为 Ghidra：由 NSA 研究理事会（Research Directorate）开发并开源的软件逆向工程（SRE）框架，具备反汇编、汇编、反编译、图谱（graphing）与脚本能力，支持大量处理器指令集与可执行格式，可交互式或自动化（headless）运行，脚本可用 Java 或 Python 编写。

## 核心研究主题

- **静态分析**：反汇编与反编译（Ghidra / IDA / radare2）、交叉引用、函数与数据类型识别
- **可执行文件格式**：ELF（Linux）/ PE（Windows）/ Mach-O（macOS）结构与加载过程
- **指令集**：x86 / x64 调用约定与栈帧、ARM / AArch64
- **动态分析**：调试器（gdb / x64dbg / lldb）、trace 与插桩
- **脚本化与自动化**：Ghidra headless 批处理、自定义分析脚本（Java / Python）
- **实战**：crackme、CTF 逆向题

## 待研究

- [ ] Ghidra headless 自动化分析流程（analyzeHeadless）
- [ ] Ghidra 反编译输出与真实源码的差异（伪 C 的失真点）
- [ ] ELF 动态链接机制（.got / .plt / 延迟绑定）
- [ ] x64 System V 调用约定最小示例
- [ ] 用 Ghidra 脚本批量重命名函数

## 参考资料（已读）

- [Ghidra — GitHub 官方仓库（NSA Research Directorate）](https://github.com/NationalSecurityAgency/ghidra)

# Ghidra headless 自动化

> Ghidra 的 headless 模式是「把逆向流程塞进 CI」的关键入口:批量导入、跑脚本、
> 开关分析、决定程序去留。它的坑几乎全在**选项组合**上 —— 单个选项都好懂,
> 组合起来的行为(谁能删程序、什么情况下改动会被丢弃)必须照官方 README 逐条对齐。

## 一、简介

`analyzeHeadless` 是 Ghidra 的非 GUI 版本,能做四类事:

1. 创建/填充项目(单文件或整目录导入,可递归);
2. 对已导入或新导入的程序执行分析;
3. 运行非 GUI 脚本(可依赖程序,也可不依赖程序);
4. 用脚本**决定程序处置**(继续/中止/删除),输出分类日志。

对逆向工程的意义:把「样本入队 → 静态分析 → 特征提取 → 结果落库」变成可重复的批处理,
而不是手工点 GUI。

## 二、命令行骨架(官方语法)

```
analyzeHeadless <project_location> <project_name>[/<folder>] | ghidra://<server>[:<port>]/<repo>[/<folder>]
    [[-import <dir>|<file>]+] | [-process [<project_file>]]
    [-preScript <Name.ext> [<arg>]*] [-postScript <Name.ext> [<arg>]*]
    [-scriptPath "p1;p2"] [-propertiesPath "p1;p2"]
    [-noanalysis] [-readOnly] [-recursive [<depth>]] [-deleteProject] [-overwrite]
    [-analysisTimeoutPerFile <sec>] [-max-cpu <n>] [-okToDelete] [-loader <name>] ...
```

硬约束(照抄 README,不是"看起来合理"):

| 约束 | 内容 |
| --- | --- |
| 项目定位 | `project_location + project_name` 或 `ghidra://` 仓库 URL,**必居其一** |
| 导入/处理 | `-import` 与 `-process` **不能同时出现**;`-process` 只能一次,`-import` 可重复 |
| 脚本名 | 只写**脚本名**(含扩展名),**不含路径**;多个脚本重复用同名选项,按命令行顺序执行 |
| `-readOnly` | `-import` 的文件不保存;`-process` 的改动被丢弃;并**忽略 `-overwrite`** |
| `-deleteProject` | 只对**本次 `-import` 新建**的项目生效,已存在项目**永不删除** |
| `-max-cpu` | 传 0 或负数**等价于 1** |
| `-recursive [depth]` | 导入目录默认深度 0、导入文件默认深度 1;**0 = 不进入容器文件**(zip/tar/.a) |
| 通配符 | `-import` 由 shell 展开(Windows 只对文件生效);`-process` 由 headless 展开且只支持 `*`/`?`,**需用单引号**防止 shell 抢跑 |

## 三、原理详解

### 3.1 preScript / postScript:位置决定能力

| | `-preScript` | `-postScript` |
| --- | --- | --- |
| 时机 | 分析**之前** | 分析**之后** |
| 能开关分析 | **能**(`enableHeadlessAnalysis`) | 不能(分析阶段已过) |
| 能查超时 | 不适用 | 能(`analysisTimeoutOccurred()`) |
| 共同点 | 只写脚本名;每组按命令行顺序执行;都可对 `currentProgram` 调 `setTemporary()` 阻止保存 | 同左 |

`analysisTimeoutOccurred()` 的可用条件是**三条同时满足**:设了 `-analysisTimeoutPerFile`
+ 分析已启用且完成 + 当前脚本是 postScript。这条"组合条件"是新手最容易误用的地方。

### 3.2 HeadlessContinuationOption:4×4 合并表

脚本用 `setHeadlessContinuationOption()` 决定程序去留,四种取值两两组合的**官方合并表**:

| Script1 \ Script2 | ABORT | ABORT_AND_DELETE | CONTINUE_THEN_DELETE | CONTINUE |
| --- | --- | --- | --- | --- |
| **ABORT** | ABORT | ABORT | ABORT | ABORT |
| **ABORT_AND_DELETE** | ABORT_AND_DELETE | ABORT_AND_DELETE | ABORT_AND_DELETE | ABORT_AND_DELETE |
| **CONTINUE_THEN_DELETE** | **ABORT_AND_DELETE** | ABORT_AND_DELETE | CONTINUE_THEN_DELETE | CONTINUE_THEN_DELETE |
| **CONTINUE** | ABORT | ABORT_AND_DELETE | CONTINUE_THEN_DELETE | CONTINUE |

读懂它需要三条补充规则(官方脚注):

1. **Script1 设为 ABORT / ABORT_AND_DELETE 时 Script2 根本不运行**(除非是被 Script1 调用的子脚本)
   —— 这解释了为什么第一行全是 ABORT;
2. **合并表不满足交换律**:`merge(CTD, ABORT) = ABORT_AND_DELETE`,而 `merge(ABORT, CTD) = ABORT`;
3. 选项**在当前脚本跑完后才生效**:设 `ABORT` 不会立刻掐断本脚本,而是掐断紧随其后的分析与脚本;
   子脚本设的选项在主脚本跑完后生效;单脚本内多次调用以**最后一次**为准。

本 demo 用**规则**而非查表实现 `merge()`,再逐格与官方表比对 —— 16 格全中才说明规则反推正确。

### 3.3 两种模式下的处置差异

| 选项 | `-import` 模式 | `-process` 模式 |
| --- | --- | --- |
| ABORT | 不跑后续;程序**已导入** | 不跑后续;改动**被保存** |
| ABORT_AND_DELETE | 不跑后续;程序**不导入** | 不跑后续;程序**被删除** |
| CONTINUE_THEN_DELETE | 跑完后续;程序**不导入** | 跑完后续;程序**被删除** |
| CONTINUE | 跑完后续;程序**已导入** | 跑完后续;改动**被保存** |

安全门:`-process` 下要删程序**必须**显式给 `-okToDelete`,否则只打印警告;
`-process` + `-readOnly` 时**无论如何不能删**。

### 3.4 脚本参数的消费顺序:headless 下最常炸的地方

* Ghidra 7.2 起可把脚本专属参数直接写在命令行上,脚本内用 `getScriptArgs()` 取回;
* **headless 下 `askXxx()` 优先消费参数数组而非 `.properties`**,第 1 个 `askXxx` 取第 1 个值……
  **数组取空后再调 `askXxx()` 会抛 `IndexOutOfBoundsException`**;
* `.properties` 的 key 是「**空格拼接的参数串**」,且写出时**不含 `defaultValue` 参数**,
  因此 key 与脚本源码里的默认值字符串并不完全一致(照抄默认值当 key 会匹配不上);
  以 `#` 或 `!` 开头的行是注释;
* 在 GUI 里能跑的脚本,在 headless 下可能因**调用了 GUI 专属方法**而抛 `ImproperUseException`;
  需要引用 headless 专属方法时脚本应继承 `HeadlessScript`(它继 `GhidraScript` 继 `FlatProgramAPI`)。

## 四、环境与运行

无第三方依赖;不需要安装 Ghidra(纯语义模型)。

```bash
python headless_semantics.py    # 合并表 16 格 + 安全门 + 命令行校验,全部断言实跑
go run .                        # 同题 Go 实现(本机无 Go 工具链,走人工审查)
```

## 五、关键代码

* `headless_cli.py::build_command()` —— 拼串**之前**先做组合校验,错误以 `CliError` 抛出;
* `headless_cli.py::plan()` —— 把选项变成执行序列(`import → preScript → analysis → postScript → 保存/删除`);
* `headless_semantics.py::merge()` / `outcome()` —— 合并规则与安全门模型;
* `headless_cli.py::consume_script_args()` / `parse_properties()`。

## 六、性能边界

* 批量导入是 I/O 与反编译主导;**关掉分析**(`-noanalysis`)或只跑 preScript 能省掉最大的开销,
  适合"先入库、后按需分析"的两段式流水线;
* `-recursive <depth>` 只影响**容器文件**(zip/tar/.a)的递归深度,目录递归本身由 `-recursive`
  是否出现决定;导入目录默认深度 0 意味着"不解压归档",这是刻意的性能保护;
* `-max-cpu` 控制 headless 可用核数,但**单个反编译器进程是单线程的**(官方文档说明反编译器
  以单线程 agent 形式运行),所以并行度来自多进程/多程序而非单函数多线程。

## 七、注意事项与常见坑

1. **`-import` 与 `-process` 互斥**;写脚本封装时最容易随手把两者一起传。
2. **`-process` 删除程序必须带 `-okToDelete`**,否则脚本"跑了但没删",日志里只有警告。
3. **`-readOnly` 会静默忽略 `-overwrite`** —— 想覆盖导入必须去掉 `-readOnly`。
4. **`-deleteProject` 只删"本次新建"的项目**,拿它清理旧项目是无效操作。
5. **项目被 GUI 打开时 headless 无法运行**(`.lock` 文件保护)。
6. **`askXxx()` 与 `getScriptArgs()` 的优先关系**会造成参数越界;跨 GUI/headless 复用的脚本
   要显式判 `getScriptArgs().length`。
7. **脚本名带路径会被拒**;自定义脚本目录用 `-scriptPath` 传,并在 Unix 下把
   `$GHIDRA_HOME`/`$USER_HOME` 转义为 `\$GHIDRA_HOME`,否则被 shell 提前展开。
8. **本 demo 的参数建模是"每个脚本一组参数"**(Python 版保留原样;Go 版用 `strings.Fields`
   切分空格,故带空格的单个参数需要引号 —— 真实 CLI 保留引号语义)。
9. 未覆盖:`-loader`/`-processor`/`-cspec` 的语言与编译器规格 ID 取值,以及 Ghidra Server
   的 `-connect`/`-commit`/`-keystore` 认证流程。

## 八、参考资料(已读)

* [Headless Analyzer README — 官方完整参数表、脚本参数/getScriptArgs、askXxx 与 .properties、HeadlessScript、程序处置与 4×4 合并表(含多脚本合并矩阵)](https://ghidradocs.com/12.0_PUBLIC/support/analyzeHeadlessREADME.html)
* [Headless Analyzer README 9.1 版 —— 同文档早期版本,用于对照参数演进(-processor/-cspec 示例)](https://ghidradocs.com/9.1_PUBLIC/support/analyzeHeadlessREADME.html)
* [Headless Scripting Capabilities — GhidraClass 课件(通配符的 Unix/Windows 差异、-import 目录展开规则)](https://www.ghidradocs.com/9.2.4_PUBLIC/docs/GhidraClass/Intermediate/HeadlessAnalyzer.html)
* [FlatProgramAPI — 官方 Javadoc(createLabel/set*Comment 等脚本常用 API 的签名与语义)](https://ghidradocs.com/12.1_PUBLIC/docs/GhidraAPI_javadoc/api/ghidra/program/flatapi/FlatProgramAPI.html)
* [Ghidra Scripting for Analysis and Machine Learning Applications — class.malware.re(analyzeHeadless 实际调用形态、-process 与 -noanalysis 组合)](https://class.malware.re/2021/03/21/ghidra-scripting-feature-extraction.html)

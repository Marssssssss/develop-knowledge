# Ghidra 脚本化批量重命名

## 一、简介

逆向一个中等规模二进制,反汇编器给出的名字是清一色的 `FUN_00401000`。想让它可读,
就得把函数名批量改成有意义的标识符 —— 手工一个个改既慢又不可复现。

本 demo 用 Python + Go 复刻 Ghidra 脚本化改名的完整机制,不依赖 Ghidra 本体也能实跑:

- **符号表语义**:`createLabel` / `createFunction` / `setEOLComment` 的行为,
  尤其是 `SourceType` 优先级链 —— 为什么脚本改的名不会被后续分析覆盖;
- **批量流程**:`计划 → 预演(dry-run) → 事务提交`,可反复执行而不产生副作用;
- **参数与事务**:`getScriptArgs()` 的固定解析顺序、写操作必须包在事务里。

## 二、原理详解

### 2.1 脚本机制:FlatProgramAPI 与 GhidraScript

官方脚本文档给出的类层次:

```
FlatProgramAPI
      └── GhidraScript
              └── YourScript
```

- **FlatProgramAPI** 提供常见操作的简化方法(地址操作、内存访问、函数操作、符号管理、数据类型),
  类注释里有一句很说明设计意图的话:

  > NO METHODS SHOULD EVER BE REMOVED FROM THIS CLASS. NO METHOD SIGNATURES SHOULD EVER BE
  > CHANGED IN THIS CLASS. This class is used by GhidraScript. Changing this class will
  > break user scripts. That is bad. Don't do that.

  —— 这就是 `createSymbol` 从 7.4 起被标 `@Deprecated` 却依然保留、并由
  `createLabel` 取代的原因。

- **GhidraScript** 在此之上加了:交互方法(`ask` 系列)、输出方法(`print`/`println`)、
  状态变量、**脚本参数**。

脚本元数据写在注释里:`//@category`、`//@author`、`//@keybinding`、`//@menupath`、`//@toolbar`。

**参数消费顺序是个坑**:`askXxx()` 类交互方法会**先从脚本参数里取值**,取剩下的才轮到
`getScriptArgs()`。所以非交互脚本必须按固定顺序解析参数,否则 headless 下参数会错位。

### 2.2 符号来源与优先级链

官方 `SourceType` 枚举的 `isHigherPriorityThan` 说明里写得很直白:

> USER_DEFINED objects are higher priority than IMPORTED objects which are higher priority
> than ANALYSIS objects which are higher priority than DEFAULT objects.

四个来源的含义:自动分析产生(ANALYSIS)、默认名(DEFAULT)、导入元数据(IMPORTED)、
用户命名(USER_DEFINED)。文档另有一句结论:

> Symbol source determines priority - user symbols won't be overwritten by analysis.

**这条规则是批量改名脚本的安全基础**:脚本用 `USER_DEFINED` 写入的名字,后续重新分析不会
把它冲掉;反过来,脚本也不应该覆盖已有更高优先级的符号。本 demo 的
`ghidra_symbols.py::Program.create_label` 就实现了这一条 —— 用 ANALYSIS 去覆盖
USER_DEFINED 会直接抛 `PriorityError`。同类符号上的 Python 与 Go 断言完全一致。

### 2.3 命名规则与"哪些名字该改"

官方文档给出的命名规则:以字母或下划线开头、只含字母/数字/下划线、**不能有空格**、区分大小写,
且"Label names must be unique within their namespace"。违反规则的名字在 Ghidra 里会被拒绝,
所以批处理脚本必须**先校验再提交**。

该改的只有自动生成名:

> Ghidra creates default symbols: FUN_ for functions, DAT_ for data, LAB_ for labels,
> SUB_ for subroutines

所以 `build_plan` 默认只挑这些前缀(且**前缀判断区分大小写** —— 小写 `sub_` 会被当成
用户命名而跳过,这既是保护也是坑)。用户已经命名的符号一律跳过。

### 2.4 批量重命名三段式

Ghidra 自带 `BatchRename.java` 脚本做模式化批量改名,官方文档给出的流程是:

> Select Symbols → Apply Pattern(Define naming pattern)→ **Preview changes** →
> Apply to selection

本 demo 把它拆成三个可单独断言的动作,关键在于 **计划是纯函数**:

| 阶段 | 动作 | 是否改程序 |
| --- | --- | --- |
| `build_plan` | 按包含/排除正则筛出候选,套模板算出新名 | 否 |
| `resolve_conflicts` | 目标名已被别的地址占用 → 按官方 makeUnique 口径拼地址后缀 | 否 |
| `apply_plan(dry_run=True)` | 只报告 `planned` | 否 |
| `apply_plan(dry_run=False)` | 在**单个事务**里逐条改名 | 是 |

"名没变就不进计划"这条看似随意,实际是**幂等**的来源:同一份脚本可以反复执行,
第二次的计划必然为空。官方对重名的口径是:

> if the name is a duplicate, the address will be concatenated to name to make it unique

本 demo 的 `unique_name` 就是这条(`renamed` → `renamed_4000`)。

### 2.5 为什么必须用事务

官方脚本示例里,所有写操作都是同一个形状:

```
int txId = currentProgram.startTransaction("Write Data");
try { ...; currentProgram.endTransaction(txId, true); }
catch (Exception e) { currentProgram.endTransaction(txId, false); }
```

批量改名要写几十上百个符号,**中途任何一个名字非法都会让程序处于半成品状态**。
本 demo 第 7 节专门演示了这一点:第一条改名已经写进去了,第二条抛异常 ——
只有整批回滚才能回到干净状态,否则符号表里会留下一半新名一半旧名的烂摊子。

## 三、与其它做法的对比

| 做法 | 可复现 | 可预演 | 出错影响 | 适用场景 |
| --- | --- | --- | --- | --- |
| 手工在 UI 里逐个改 | 差 | 无 | 局部 | 少量关键函数 |
| UI 批量改名 + 模式 | 中 | 有(预览) | 选中集合 | 交互式一次性整理 |
| **脚本(BatchRename.java 类)** | **强** | **dry-run** | **整批回滚** | 反复跑、纳入流水线 |
| headless + postScript | 最强 | dry-run | 整批回滚 | CI/无人值守批量处理 |

## 四、环境与运行

Python 3.8+(本机 3.13.12),仅标准库;Go 1.18+。四个自检互相独立,退出码 0 即全通:

```bash
python symbols_check.py     # 命名规则 / SourceType 优先级 / 重名 / 事务回滚 / 写注释
python rename_batch.py      # 参数解析 / 计划 / dry-run / 提交 / 幂等 / 冲突 / 排除表
go run *.go                 # Go 同题实现(结构与数值须与 Python 一致)
```

## 五、关键代码

```python
def build_plan(program, include, template, exclude=None, only_default=True):
    """生成改名计划。返回 [(addr, old, new), ...],按地址升序 —— 顺序确定才好复现。"""
    for sym in program.all_symbols():
        if only_default and not is_default_name(sym.name):
            continue                       # 用户命名的符号绝不碰
        new = include.sub(template, sym.name).replace("{addr}", "%08x" % sym.addr)
        if new == sym.name:
            continue                       # 名没变就不进计划,保证幂等
```

```python
    tx = Transaction(program, "batch rename").begin()
    try:
        for addr, old, new in plan:
            program.rename_symbol(addr, old, new, "USER_DEFINED")
    except Exception as e:                 # 任一异常 → 整批回滚
        tx.rollback()
```

## 六、性能边界

- 计划阶段对每个符号跑一次正则,复杂度 `O(符号数 × 正则长度)`,几千个符号是毫秒级;
- 冲突检测用哈希表查重,不是两两比较 —— 否则 `O(n²)`;
- 真正的开销在 Ghidra 侧:**每个符号的写入都触发一次数据库变更事件**,
  所以官方才要求整批包在一个事务里,而不是每条一个事务;
- 事务用快照实现回滚,`O(符号数)` 内存;真实 Ghidra 走数据库事务日志,不必复制全表。

## 七、注意事项与常见坑

1. **`askXxx()` 会先吃掉脚本参数**。用 `askString()` 的脚本在 headless 下不会弹窗,
   而是直接从 `getScriptArgs()` 里取 —— 参数顺序错了会静默取到别的值。
2. **覆盖高优先级符号会失败/无效果**。脚本里务必用 `SourceType.USER_DEFINED`,
   否则改完名可能被下一次分析复原。反过来,直接覆盖用户的命名会被拒绝。
3. **默认名前缀判断区分大小写**。`sub_00401000` 不是官方默认名,所以"只改默认名"的
   脚本第二次跑时不会再把它们当候选 —— 这一条既保护了用户命名,也意味着
   **首次批量改名后就无法用同一规则再批量处理**了,需要放宽 `only_default`。
4. **模板替换语义两种语言不同**。Python 用 `re.sub`(`\1` 反向引用,引用不存在的组会抛
   `invalid group reference`);Go 的 `ReplaceAllString` 用 `$1`,且引用不存在的组会
   **静默展开成空串** —— 于是会产出 `impl_` 这种看似合法、其实丢了一半信息的名字。
   Go 版必须自己补校验,或至少在 dry-run 里人工核对。
5. **`{addr}` 这种自定义占位符要放在正则替换之后**。先替换 `{addr}` 再跑正则,
   地址里的 `0-9a-f` 会被正则当成待匹配内容,结果依赖模板写法,很容易出现"偶尔对偶尔错"。
6. **改名要连带函数名**。符号表里的 label 与 `Function` 对象是两个东西:
   `rename_symbol` 只改 label 的话,`getFunctionAt(addr).getName()` 还是旧名。
7. **重名不等于错误**。同地址创建同名 label 官方是幂等的(返回既有符号),
   所以脚本里不必先查询再创建;但**跨地址**重名必须处理,否则会拿到空名或异常。

## 八、参考资料

1. `FlatProgramAPI` javadoc(createLabel 三个重载、createFunction、setEOLComment、
   removeSymbol、createSymbol 的弃用说明与类注释):
   <https://ghidradocs.com/12.1_PUBLIC/docs/GhidraAPI_javadoc/api/ghidra/program/flatapi/FlatProgramAPI.html>
   <https://ghidra.re/ghidra_docs/api/ghidra/program/flatapi/FlatProgramAPI.html>
2. `SourceType` javadoc(四个枚举常量与 `isHigherPriorityThan` 的优先级原文):
   <https://ghidradocs.com/11.0_PUBLIC/docs/GhidraAPI_javadoc/api/ghidra/program/model/symbol/SourceType.html>
3. Ghidra 官方脚本文档 —— 类层次、脚本元数据、事务写法、FlatProgramAPI 方法分类:
   <https://mintlify.wiki/NationalSecurityAgency/ghidra/scripting/java-scripts>
4. Symbol Management 官方文档 —— Batch Renaming(`BatchRename.java`)、命名规则、
   符号来源优先级、自动生成名(`FUN_`/`DAT_`/`LAB_`/`SUB_`):
   <https://mintlify.wiki/NationalSecurityAgency/ghidra/guide/symbols>
5. Symbol Table 插件帮助与 Beginner 学生手册(Ghidra 里实际的符号来源筛选项与默认名前缀):
   <https://www.ghidradocs.com/9.1_PUBLIC/help/Base/help/topics/SymbolTablePlugin/symbol_table.htm>
   <https://ghidradocs.com/10.0.1_PUBLIC/docs/GhidraClass/Beginner/Introduction_to_Ghidra_Student_Guide.html>

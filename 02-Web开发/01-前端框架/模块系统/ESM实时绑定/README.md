# ES Modules：实时绑定与两阶段求值

## 一、简介

ESM 与 CommonJS 最本质的差别不是语法，而是**绑定语义**：

- ESM 的 `import` 拿到的不是值的拷贝，而是**指向导出模块那个绑定槽位的引用**（live binding）；
- 模块执行分 **Link**（建绑定、解析 import、提升函数声明）与 **Evaluate**（真正跑代码）两步，循环依赖因此不会死锁，但也可能掉进 TDZ。

本 demo 用 Python（+ Go 对照）实现一个最小模块 loader，把 ECMA-262 里的算法与规范给出的两个错误传播示例变成可执行判据（28 断言全通过）。

代码：`esm_modules.py` + `selfcheck_esm.py` + `esm_modules.go`。

## 二、原理详解

### 2.1 两阶段与状态机

Cyclic Module Record 的状态：`unlinked → linking → linked → evaluating → evaluated`。

- **Link**（`InnerModuleLinking`）用 DFS + stack 遍历依赖图，规范明说这个 stack 是 *"to avoid infinite loops with circular dependencies"*；遇到已在 linking 中的模块直接返回。
- **Evaluate**（`InnerModuleEvaluation`）同样是 DFS，但**依赖先求值**（后序），所以求值顺序对调用方是可预测的。

### 2.2 函数提升来自 InitializeEnvironment

`InitializeEnvironment` 阶段做两件事：

1. 为每个 `import` 建立 indirect binding（指向导出模块那个 Binding）；
2. 为 `var` / `function` / `let` / `const` 建立绑定，其中**函数声明在此阶段就完成初始化**。

所以「声明前调用」能成功，而 `let` / `const` 只是 `CreateMutableBinding` 而没有 `InitializeBinding` —— 这就是 TDZ 的来源。本 demo 直接断言：link 之后函数绑定 `initialized == true`，`let` 绑定 `initialized == false`。

### 2.3 live binding 到底"活"到什么程度

```
b.x = 1            a: import {x} from 'b'; let y = x + 1
b.x = 10   →  a 里读 x 得到 10（活）
               a 里读 y 仍然是 2（y 是求值那一刻算出来的快照）
```

**活的是绑定，不是推导出来的值。** 这个区别在调试"为什么我改了导出变量，那个常量没变"时非常关键。

同时，导入侧对绑定赋值会抛 `TypeError`——绑定对导入方是不可写的，但导出方用 `let` 声明的仍可写。

### 2.4 循环依赖

Link 阶段靠 stack 判定回边，不会无限递归；Evaluate 阶段依赖优先，于是：

- **函数互相调用可行**（函数在 Link 阶段就初始化了）；
- **读对方尚未初始化的 `let` 会抛 `ReferenceError`** —— 因为被依赖方先求值，此时入口模块的 `let` 还在 TDZ。

### 2.5 错误记录的范围（规范给了明确示例）

求值错误场景下 A ← B ← C：*"the exception will be recorded in both A and B's [[EvaluationError]] fields … C will also become evaluated but, in contrast to A and B, will remain without an [[EvaluationError]]"*。

原因很直接：异常只记录到**此刻仍在 evaluating 的栈上模块**，而 C 已经求值完毕出栈了。链接错误同理：*"A and B become unlinked. Note that C is left as linked"*。

本 demo 用两组对偶断言把这两句规范原文锁住。

### 2.6 ResolveExport 的三种结果

返回 `ResolvedBinding Record {[[Module]], [[BindingName]]}`；找不到或检测到循环 → `null`；`export *` 冲突 → `ambiguous`。注意 `export *` **也会产生依赖**（会进 `[[RequestedModules]]`），所以只有先链接这些模块，歧义检测才有意义。

## 三、对比

| | 绑定语义 | 循环依赖 | 求值时机 |
| --- | --- | --- | --- |
| ESM | live binding（引用同一槽位） | 静态分析，Link 阶段解决 | 依赖优先（后序） |
| CommonJS | 值拷贝（`exports.x = v` 时的快照） | 运行时拿到可能是残缺的 `exports` | 首次 `require` 时同步执行 |

## 四、环境

- Python 3.8+（标准库）
- Go 1.20+（对照）

## 五、运行方式

```bash
python selfcheck_esm.py
# esm_modules: 28/28 assertions passed
```

## 六、注意事项与常见坑

1. **导出表必须先于依赖链接建立。** 循环依赖 A↔B 时，B 解析 `import {x} from 'A'` 的那一刻 A 还没跑完 InitializeEnvironment；若把导出表放在最后建，循环引用会误判成"没有该导出"。
2. **import 侧要包一层不可写视图**，不能直接把导出模块的 Binding 塞给导入方——否则 `let` 导出的绑定在导入侧也能写，与规范不符。
3. **`export *` 也是依赖**。只把 `import` 当依赖去 link，`export *` 的模块会一直停在 unlinked，歧义检测永远返回 null。
4. **错误场景下没有模块"走完"求值**，但栈上模块的状态仍是 `evaluated` 且带 `[[EvaluationError]]`——不要拿"是否出现在求值顺序里"当成功判据。
5. **写测试时模块名要和 import 里写的一致。** 开发期因为 `imports=[("g", ...)]` 而模块叫 `good`，直接变成"找不到模块"的另一种错误路径，把真正想测的绑定缺失掩盖掉了。

## 七、性能边界

- Link 是 O(模块数 + 依赖边数) 的 DFS；Evaluate 同量级。
- 循环依赖会让强连通分量整体延后求值，分量越大，TDZ 风险越高。
- `ResolveExport` 带 `resolveSet` 防环，深层 `export *` 链会让单次解析成本线性上升。

## 八、参考资料（实际读过）

- ECMA-262 · Modules：Cyclic Module Record 状态机、`InnerModuleLinking` / `InnerModuleEvaluation` / `InitializeEnvironment` 步骤、`ResolveExport` 的三种返回、求值错误与链接错误的传播示例、`export *` circularity — https://tc39.es/ecma262/multipage/ecmascript-language-scripts-and-modules.html

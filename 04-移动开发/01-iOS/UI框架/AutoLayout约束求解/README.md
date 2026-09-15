# Auto Layout 约束求解:Cassowary 线性约束与优先级

> 04-移动开发 / 01-iOS / UI框架 / AutoLayout约束求解 —— demo 227(Swift / Objective-C / Python)

## 简介

Auto Layout 把界面布局表达成**一组线性方程**,交给布局引擎求解:

```
item1.attribute1 = multiplier × item2.attribute2 + constant      ← Apple 文档原式
```

引擎不"把右边的值赋给左边",而是**同时解出两侧所有属性**,使关系成立。每个视图在每个维度上通常需要 2 条约束才能唯一确定位置(Apple 文档 *Equation Solving* 一节)。

- **约束(constraint)**:一条线性等式或不等式,含 6 个部分(item1 / attribute1 / 关系 / multiplier / item2 / attribute2 / constant)。
- **优先级(priority)**:1..1000 的整数,**1000 = required**(必须满足),其余都是 optional(可被打破)。
- **CHCR**:Content Hugging(把视图拉向内容)与 Compression Resistance(把视图推离内容),默认优先级分别是 **250 / 750**。
- **内在内容尺寸(intrinsic content size)**:label、button 等控件自带的"自然尺寸",引擎把它翻译成一对约束。
- **Cassowary**:Apple 在 macOS Lion 起采用的增量线性约束求解器(源自华盛顿大学 Badros / Borning / Stuckey),也是本 demo 要实现的算法。

## 原理详解

### 一、约束解剖:6 个部分

| 部分 | 含义 | 例 |
| --- | --- | --- |
| item1 | 第一个项,必须是视图或布局指南 | 红色视图 |
| attribute1 | 第一项上被约束的属性 | leading |
| relationship | `=` / `>=` / `<=` | 相等 |
| multiplier | attribute2 的系数 | `1.0` |
| item2 | 第二项,**可为空** | 蓝色视图 |
| constant | 加到 attribute2 上的偏移 | `8.0` |

```
Red.leading = 1.0 × Blue.trailing + 8.0
View.height = 0.0 × NotAnAttribute + 40.0      ← 固定高度:第二项留空、乘数 0
View.height = 2.0 × View.width + 0.0           ← 纵横比:同一项的两个属性
```

**反转规则**(Apple 文档 Listing 3-2):等式两侧可以互换,但**乘数取倒数、常量取反**;`0.0` 与 `1.0` 保持不变。`height = 2.0 × width` 反转成 `width = 0.5 × height`。

### 二、优先级阶梯与"力"的隐喻

| 优先级 | 含义 | 谁在用 |
| --- | --- | --- |
| 1000 | required | 必须满足,否则引擎打破某条并打印冲突日志 |
| 750 | high | **压缩阻力**默认值 |
| 500 | medium | — |
| 250 | low | **内容拥抱**默认值 |

Apple 明确建议:优先级只需聚集在 250 / 500 / 750 / 1000 附近(上下浮动一两点避免并列),用满 1000 档说明布局逻辑需要重审。**未被满足的可选约束不会消失,它像一股"力"把视图拉向它**——这正是"最接近该约束的解"的实现方式。

CHCR 本身就是一对可选约束(Apple 文档 Listing 3-5):

```
View.height >= IntrinsicHeight     @750   压缩阻力:别把内容压扁
View.height <= IntrinsicHeight     @250   内容拥抱:别把自己撑大
```

### 三、Cassowary 怎么解:required 与"差多少"

| 概念 | 作用 |
| --- | --- |
| slack / surplus 变量 | 把不等式化成等式,`x + s₁ = y - 5` |
| **error 变量 e⁺ / e⁻** | 记录一条非 required 约束"差多少";`e⁺ = e⁻ = 0` 表示完全满足 |
| 目标函数 | 最小化 Σ(强度 × 误差),强度大的约束先被满足 → 形成约束层级 |
| stay 约束 | weak 地把变量按在原值,即"别乱动" |
| edit 约束 | strong 地钉住被拖动的变量,拖动即改它的常量 |

`x ≤ y - 5 ≤ 20` 的转换(论文原文示例):先拆成 `x + s₁ = y - 5`、`x + s₂ = 20`,再化成 `x = 20 - s₂`、`y = 20 - s₂ + s₁ + 5`;令 `s₁ = s₂ = 0` 得 `x = 20 ∧ y = 25`。

```
模型(本 demo)                          真实 Cassowary
──────────────────────────────────────────────────────────────
required  → 硬约束(两阶段第一阶段)      required → 直接进表
非 required → error 变量 + 加权目标      同左(强度是"符号权重")
每次 solve 重建表                        表增量维护(dual simplex)
自由变量拆 x⁺ - x⁻(列数 ×2)              允许自由变量
```

本 demo 保留误差变量、强度分层、edit / stay 与单纯形,简化了对偶单纯形的增量维护——因此**拖动一个变量后重解的主元数并不会更少**(自检里如实打印:`冷启动 5 → 重解 5`),这正是 Cassowary 相比"朴素重解"的价值所在。

### 四、引擎视角:歧义与冲突

- **歧义(ambiguous)**:存在无穷多解(等 hugging 的两个视图就是典型)。
- **不可满足(unsatisfiable)**:required 约束互相矛盾,引擎打印日志并打破其中一条。
- 非模糊且可满足的布局:每个视图每个维度通常需要 2 条约束(父视图尺寸已知的前提下)。

## 对比 / 选型

| 方案 | 求解器 | 特点 |
| --- | --- | --- |
| UIKit Auto Layout | Cassowary | 表达式强、性能敏感,约束数上千后布局耗时明显 |
| 直接调 Cassowary(kiwi 等) | Cassowary | 无视图层开销,可自定义权重与 edit 变量 |
| UIStackView | Auto Layout 封装 | 内部生成约束,省手写;嵌套过深会放大布局成本 |
| SwiftUI | 布局协议 + 内部求解 | 同样受"优先级 + 歧义"语义支配 |

## 环境准备

- Python 3.9+(纯标准库,**本机可直接跑**)
- Swift 5.x / macOS(本仓库不实际运行,人工审查)
- Objective-C:clang + Foundation

## 运行方式

```bash
python3 python/cassowary_check.py                      # 23 条断言,纯标准库
swiftc swift/SwiftSimplex.swift swift/main.swift -o layout && ./layout
clang -fobjc-arc -framework Foundation objc/ObjCLayoutScenarios.m -o layout && ./layout
```

> 源文件按 `核心 + 场景` 拆分只为守住「单源文件 ≤300 行」;Swift 侧场景文件必须叫
> `main.swift`(Swift 只允许 main.swift 写顶层语句),ObjC 侧内核是**实现头文件**。

## 关键代码片段

求解内核里最关键的三个事实,分别在 `python/solver_core.py` / `swift/SwiftSimplex.swift` / `objc/LALayoutSolver.h`:

```python
# 1) 非 required:补一对误差变量,强度进目标 —— 这就是"约束层级"的落地
em, ep = sp.col(c.strength), sp.col(c.strength)
row[em] = row.get(em, 0.0) + 1.0
row[ep] = row.get(ep, 0.0) - 1.0
sp.row(row, -const)

# 2) 目标行存 reduced cost:最小化时 r_j < 0 才进基(Bland 规则防循环)
enter = next((j for j in sorted(allowed) if objectives[0][j] < -1e-9), -1)

# 3) 自由变量拆成 x⁺ − x⁻,把"可正可负"塞回标准形
values[v] = z[up[v]] - z[un[v]]
```

## 性能与边界

- **歧义是合法结果**:等 hugging 时目标函数在可行域上**完全平坦**(自检实测两个端点解代价同为 `3e+10`),引擎返回哪个顶点取决于内部实现细节——Apple 文档因此明确要求把 textField 的 hugging 调到比 label 低(IB 自动把 label 置 251)。
- **不要给 CHCR 设 required**:视图尺寸出错通常比意外冲突更好;若确实要"永远按内在尺寸",用 999 而非 1000,保留一个泄压阀。
- **不要用满优先级档位**:250/500/750/1000 附近即可,相邻档之间留出间隔避免并列歧义。
- **约束数量与布局耗时**:约束越多、嵌套越深,一次 layout pass 越贵;Apple 建议用 `NSLayoutConstraint.activate([...])` 批量激活而非逐条 add。
- **精度**:本 demo 用 `eps = 1e-9` 判定零;真实引擎同样存在浮点容差,因此"理论上为 0 的偏差"实测可能是 `1e-16` 量级。

## 注意事项与常见坑

| 现象 | 原因 | 规避 |
| --- | --- | --- |
| 控制台打印 "Unable to simultaneously satisfy constraints" | 两条 required 互相矛盾 | 把其中一条降级到 999/750,或删掉冗余约束 |
| 布局"随机"偏向某一侧 | 存在歧义(如等 hugging),解不唯一 | 拉开 CHCR 优先级;或补一条确定性的约束 |
| label 被拉伸/截断 | hugging 或 CR 优先级低于同排的 textField | 按 Apple 建议调低 textField 的 hugging |
| 二次 layout pass 导致掉帧 | 一次布局中改了约束,触发重新求解 | 批量改约束,避免在 `layoutSubviews` 里反复 activate |
| stack view 设 CHCR 无效 | stack view 没有内在内容尺寸 | 调它**内容物**相对外部项的 CHCR,或加显式约束 |
| 用 `constant` 表达位置关系 | 位置属性不能用常量(缺少上下文) | 位置只能相对另一项表达 |
| 把尺寸属性约束到位置属性 | 属性类别不兼容 | 只用同类属性组合(尺寸↔尺寸、位置↔位置) |

## 参考资料(实际读过的来源)

- [Apple — Auto Layout Guide: Anatomy of a Constraint](https://developer.apple.com/library/archive/documentation/UserExperience/Conceptual/AutolayoutPG/AnatomyofaConstraint.html) —— 方程 6 要素、反转规则(Listing 3-2)、属性兼容规则、优先级阶梯与"未满足约束像一股力"、CHCR 默认 250/750 及其处理指南、内在内容尺寸 vs 适配尺寸、方程求解与歧义/不可满足。
- [Badros, Borning, Stuckey — The Cassowary Linear Arithmetic Constraint Solving Algorithm (TOCHI 8(4), 2001)](http://www.badros.com/greg/papers/cassowary-tochi.pdf) —— 约束层级与强度、比较器(locally-error-better / weighted-sum-better)、适配单纯形、slack 与 error 变量、`addEditVar / beginEdit / suggestValue / endEdit / resolve` 协议、stay 与 `addPointStays` 的权重折半。
  - 说明:PDF 正文使用了无 ToUnicode 映射的内嵌字体,本地抽取为乱码,以上内容读自该 PDF 的可检索正文片段(同一 PDF 另有 [UniMelb 镜像](https://people.eng.unimelb.edu.au/pstuckey/papers/cassowary-tochi.pdf))。
- [UW Cassowary Constraint Solving Toolkit](https://constraints.cs.washington.edu/cassowary) —— 项目与论文索引、v0.60 发行说明、被 macOS Lion 采用的时间线。
- [kiwi(高效 C++ Cassowary 实现)](https://github.com/nucleic/kiwi) 与 [@lume/kiwi 文档](https://github.com/lume/kiwi/blob/master/docs/Kiwi.md) —— 强度符号常量(required / strong / medium / weak = create(a,b,c))、`createConstraint / addEditVariable / suggestValue / updateVariables` API 形态;本 demo 的强度压平方式与之同构。
- [@withremyinc/cassowary-layout](https://www.npmjs.com/package/@withremyinc/cassowary-layout) —— 可伸缩行示例(窗宽 300、两个 weak 宽度偏好 50/100 → `box1Right=50, box2Left=200, box2Right=300`),本 demo 场景 [3] 复现了它。

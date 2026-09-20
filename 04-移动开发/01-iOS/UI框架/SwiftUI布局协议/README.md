# SwiftUI 布局协议:尺寸协商与自定义容器

SwiftUI 的布局和 UIKit 相反:**父视图不能命令子视图多大**,只能**提议**;子视图看完提议
**自己报一个尺寸回来**。自定义容器(`Layout` 协议)就是插在这条协商链里的一段代码 ——
它同时扮演「被父视图提议的子视图」和「向子视图提议的父视图」。

`python/layout_protocol.py` 把这条协商链做成可执行模型(26 条断言),
`swift/LayoutProtocol.swift` 是同题的 Swift 实现。

## 1. 一次协商的全部参与者

```
父视图 ──proposal──► 布局容器 sizeThatFits(proposal, subviews, cache) ──► CGSize
                            │
                            ├─ 向每个子视图提 .unspecified / .zero / .infinity / 具体值
                            └─ 看它们的响应,决定自己报多大、把空间怎么分
父视图 ──bounds────► 布局容器 placeSubviews(in:proposal:subviews:cache:) ──► 逐个 place
```

`Layout` 协议只需要两个方法:

| 方法 | 作用 |
| --- | --- |
| `sizeThatFits(proposal:subviews:cache:)` | 报告**整个容器**的尺寸 |
| `placeSubviews(in:proposal:subviews:cache:)` | 给每个**子视图**分配位置 |

其余(`explicitAlignment`、`spacing`、`layoutProperties`、`makeCache`)都有默认实现,
不写也能编译 —— 模型断言 24–26 验的正是这一点。

## 2. 三种特殊提案(官方文档写死的语义)

文档说容器通常「提几种尺寸、看响应、再决定怎么分空间」。三种提案各有确定含义:

| 提案 | 子视图的响应 | 断言 |
| --- | --- | --- |
| `.zero` | **最小**尺寸 | 01 |
| `.infinity` | **最大**尺寸 | 03 |
| `.unspecified` | **理想**尺寸 | 02 |
| 具体值 `p` | 按 `[min, max]` 夹住 | 04–06 |

一个 `min=44, ideal=44, max=44` 的按钮:提 10 还是 44,提 999 也是 44 ——
**「提议」不是「命令」,超出 `[min, max]` 的部分对子视图无效**。

`replacingUnspecifiedDimensions(by:)` 只替换**未指定**的那一轴;已指定的轴保持原值(07)。

## 3. 尺寸是自下而上流出来的

模型里的 `HStackLayout` 演示了两趟协商(断言 08–10):

1. 先给每个子视图提 `.unspecified`,拿到各自的理想宽度;
2. 若父视图给的 `proposal.width` 小于理想宽之和,就**按剩余空间重新提议**一轮,
   取被压缩后的响应。

所以「HStack 报多宽」不是父视图说了算,是**子视图对第二轮提议的响应之和**。
`placeSubviews` 只负责把算好的位置写回每个代理:第一个子视图落在 bounds 原点,
第二个按「前一个的宽度 + spacing」顺延(12–13)。

## 4. `makeCache` 的价值可以量化

`makeCache(subviews:)` 的结果会**同时**喂给 `sizeThatFits` 和 `placeSubviews`。
把子视图的测量结果存进去,`placeSubviews` 就不必再问一遍:

| 实现 | 4 个子视图被测量的总次数 | 断言 |
| --- | --- | --- |
| 有 cache | **4** | 14 |
| 无 cache | **8** | 15 |

**测量次数正好减半**(16)。这也是为什么文档专门列出「create and manage a cache to store
computed values across different layout protocol calls」这一条:自定义容器里最贵的操作
就是反复问子视图「你多大」。

## 5. `ViewThatFits`:按**提供顺序**挑,而不是挑「最合适的」

文档原话:它「evaluates its child views in the order you provide them」并选中
**第一个理想尺寸在受约束轴上放得下**的子视图。于是:

* 顺序是**偏好顺序**,通常从大到小;
* 因为某个视图可能只在**一个**轴上放得下,「大→小」并不总成立。

模型三个子视图(理想宽 200 / 100 / 50)在三种提议下的选择:

| proposal.width | 选中 | 断言 |
| --- | --- | --- |
| 200 | 第 1 个(最宽) | 17 |
| 100 | 第 2 个 | 18 |
| 50 | 第 3 个 | 19 |

`ViewThatFits(in: .horizontal)` 只约束水平轴:一个高 500 的子视图在
`ViewThatFits(in: .horizontal)` 下照样被选中(20),换成两轴都约束就放不下(21)。

## 6. `AnyLayout`:换布局类型而不销毁状态

文档明确:`AnyLayout` 用于「dynamically changing the type of a layout container **without
destroying the state of the subviews**」。模型里换布局前后 `subviews` 数组是同一批对象
(22),只是摆放结果从「y 相同、x 递增」变成了「x 相同、y 递增」(21、23)。

`HStackLayout` / `VStackLayout` 是内置 `HStack` / `VStack` 的**遵守 Layout 协议版本** ——
用 `AnyLayout` 时要用这两个,而不是 `HStack` / `VStack`。

## 7. 运行方式

```bash
cd python && python selfcheck_layout_protocol.py    # 26 条断言
```

Swift 侧 `swift/LayoutProtocol.swift` 只作人工对照(本机无 Swift 工具链)。

## 8. 关键代码

```python
def size_that_fits(self, proposal):
    out = []
    for axis in (0, 1):
        p = (proposal.width, proposal.height)[axis]
        lo, mid, hi = self.min[axis], self.ideal[axis], self.max[axis]
        if p is None:   out.append(mid)     # unspecified → ideal
        elif p == 0:    out.append(lo)      # zero → minimum
        elif p == INF:  out.append(hi)      # infinity → maximum
        else:           out.append(max(lo, min(hi, p)))
    return Size(out[0], out[1])
```

## 9. 性能与适用边界

* `sizeThatFits` 与 `placeSubviews` 在**每一轮布局**都会被调用,而且是自底向上跑满整棵树;
  里面做任何 O(n²) 的事都会直接体现在卡顿上 —— 所以才要 cache。
* 自定义 `Layout` 走的是和内置容器**同一条**协商路径,没有额外的兜底;
  `sizeThatFits` 返回值与实际 `placeSubviews` 摆放不一致时,SwiftUI 不会替你纠正。
* 模型只建模了单趟协商与三种特殊提案;真实 SwiftUI 还有 `LayoutValueKey` 自定义布局值、
  `layoutPriority`、`Animatable` 插值,这些不在模型范围内。

## 10. 注意事项与常见坑

1. **`sizeThatFits` 里不要直接放置子视图**,定位只属于 `placeSubviews`;反过来
   `placeSubviews` 也不该改尺寸。
2. **别在 `placeSubviews` 里重新测量** —— 那等于放弃 cache,测量次数翻倍。
3. 有默认参数值的布局,调用点要写 `MyLayout() { ... }`,因为 `callAsFunction` 找的是
   **零参数 init**;写不出就自己补一个 `init()`。
4. **`ViewThatFits` 没有任何兜底逻辑**:它只会选第一个「放得下」的,不会挑最接近的,
   所以子视图顺序写反了等于白写。
5. `.infinity` 提案下,max 无上限的视图会返回巨大尺寸;容器若直接求和就会把父视图撑爆 ——
   测量阶段通常只应提 `.unspecified` 或具体值。
6. `Layout` 同时遵守 `Sendable`,`Cache` 里不要塞不可 Sendable 的东西。

## 参考资料

* Apple《Layout》协议文档
  <https://developer.apple.com/tutorials/data/documentation/swiftui/layout.json>
* Apple《ProposedViewSize》(`.zero` / `.infinity` / `.unspecified` 与
  `replacingUnspecifiedDimensions(by:)`)
  <https://developer.apple.com/tutorials/data/documentation/swiftui/proposedviewsize.json>
* Apple《ViewThatFits》
  <https://developer.apple.com/tutorials/data/documentation/swiftui/viewthatfits.json>
* Apple《AnyLayout》
  <https://developer.apple.com/tutorials/data/documentation/swiftui/anylayout.json>
* Apple《LayoutSubview》《LayoutSubviews》
  <https://developer.apple.com/tutorials/data/documentation/swiftui/layoutsubview.json>
  <https://developer.apple.com/tutorials/data/documentation/swiftui/layoutsubviews.json>

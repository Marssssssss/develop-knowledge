# iOS · UI框架

研究 UIKit 与 SwiftUI 的布局、状态与渲染链路。

## 子领域

| 子目录 | 知识点 |
| --- | --- |
| [AutoLayout约束求解/](./AutoLayout约束求解/) | Cassowary 求解:线性等式 + 优先级(required 1000 / CHCR 250·750)、强度阶梯、表格单纯形 + Bland 规则 |
| [SwiftUI状态管理/](./SwiftUI状态管理/) | `@State` 单一数据源 + `@Observable` 宏(ObservationRegistrar / access / mutation)、依赖图粗粒度 vs 细粒度追踪 |
| [SwiftUI布局协议/](./SwiftUI布局协议/) | Layout 协议尺寸协商:`ProposedViewSize` 三提案、`sizeThatFits` / `placeSubviews`、`makeCache`、`ViewThatFits`、`AnyLayout` |
| [Combine背压/](./Combine背压/) | Combine 的 Publisher / Subscriber / Demand 背压:欠量累加、零欠量不产出、`sink` 的 unlimited、`flatMap(maxPublishers:)` |

## 待研究

- [x] Auto Layout 原理(约束求解 Cassowary)→ demo 227
- [x] SwiftUI 状态管理(`@State` / `@Observable`)→ demo 228
- [x] SwiftUI 布局系统(Layout 协议 / ViewThatFits / 尺寸协商)→ demo 453
- [x] Combine 框架(Publisher / Subscriber / Operator / 背压)→ demo 454
- [ ] UIKit vs SwiftUI 生命周期对照(viewDidLoad vs `.onAppear`)
- [ ] `UIHostingController` / `UIViewRepresentable` 桥接开销
- [ ] SwiftUI `LayoutValueKey` 自定义布局值与 `layoutPriority`
- [ ] SwiftUI 动画与 `Animatable`(含 Layout 的 Animatable 一致)
- [ ] `LazyVStack` / `List` 的复用与预取策略

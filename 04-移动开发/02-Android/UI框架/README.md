# Android · UI框架

研究 Android 的界面构建体系:声明式 Compose 的重组与状态模型,以及命令式 View 体系的测量/布局/绘制。

## 子领域

| 子目录 | 知识点 |
| --- | --- |
| [Compose重组/](./Compose重组/) | `mutableStateOf` 快照系统、`remember` / `derivedStateOf`、lambda modifier 的稳定性收益、Backwards write 反模式 |

## 两套体系的对照

| 维度 | View 体系 | Compose |
| --- | --- | --- |
| 构建方式 | 命令式(`findViewById` + 改属性) | 声明式(函数描述 UI) |
| 状态载体 | `View` 自身字段 | `State<T>` + 快照系统 |
| 更新机制 | `invalidate()` → 遍历 → 重绘 | 重组(只重跑读到变化状态的函数) |
| 布局 | `measure` / `layout` 递归 | `MeasurePolicy` + 单次测量约束传递 |
| 复用 | `RecyclerView` + ViewHolder | `LazyColumn` + 按 key 复用 |

## 待研究

- [ ] Compose 快照系统(`Snapshot` / `MutableSnapshot` / 全局快照与提交时机)
- [ ] `LazyColumn` 的复用与 `key` 语义
- [ ] Compose Navigation(类型安全路由 + 嵌套图)
- [ ] 自定义 `Layout` 与 `SubcomposeLayout` 的测量差异
- [ ] `View` 的 measure / layout / draw 三趟流程与 `requestLayout` 传播
- [ ] Compose 与 View 互操作(`AndroidView` / `ComposeView` 的性能边界)
- [ ] 基线配置文件(Baseline Profile)与 Compose 启动优化
- [ ] `Modifier` 链的求值顺序与 `Modifier.Node` 机制

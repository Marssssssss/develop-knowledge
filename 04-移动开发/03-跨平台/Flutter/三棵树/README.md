# Flutter 三棵树 — Widget / Element / RenderObject

## 简介

Flutter 的 UI 框架围绕 **三棵协同工作的树** 组织:**Widget**(不可变配置)、**Element**(持久化实例)、**RenderObject**(可变渲染状态)。理解这棵树是掌握 Flutter 性能调优、自定义 widget、布局调试的基础。

**关键概念清单**:
- **Widget**: 不可变的 UI 配置描述;轻量、频繁创建;`build()` 返回
- **Element**: 树中特定位置的实例,跨帧持久;持有 BuildContext + 关联 RenderObject
- **RenderObject**: 实际负责 layout / paint / composite;可变,持有 size + paint state
- **Box Constraint 模型**: 父→子传约束,子→父返 size;O(n) 单遍 DFS 完成布局

**历史背景**: 三棵树模型源自 React 的"虚拟 DOM"思想,但 Flutter 把三层职责明确分离,避免 React 那种"diff 整棵树"的开销。

## 原理详解

### 1. Widget 树(immutable config)

> 权威资料 docs.flutter.dev/resources/architectural-overview:
> "In Flutter, widgets ... are represented by immutable classes that are used to configure a tree of objects."
> "any change to the widget tree ... causes a new set of widget objects to be returned. But this doesn't mean the underlying representation has to be rebuilt."

- `build()` 方法返回一棵新的 widget 树,描述"我希望 UI 长什么样"
- state 变化触发 `setState()` → 框架再次调 `build()` → 新 widget 树
- 框架浅比较新旧 widget(runtimeType + key + final props),相等则复用 element

**为何 widget 这么轻**: 一棵 Material 应用每帧可能创建 500+ widget;Widget 不可变 + 浅比较是 Flutter 设计的核心,确保频繁 rebuild 成本可控。

### 2. Element 树(persistent instance)

> 权威资料 docs.flutter.dev/resources/architectural-overview:
> "The element tree is persistent from frame to frame, and therefore plays a critical performance role, allowing Flutter to act as if the widget hierarchy is fully disposable while caching its underlying representation."

**Element 生命周期**:

```
createElement (new widget)
        │
        ▼
      mount (inserted into tree, attach render object)
        │
        ▼
    ┌────────┴────────┐
    ▼                 ▼
  update           activate
(新旧 widget 同型)  (复用 inactive element)
    │                 │
    ▼                 ▼
  deactivate ◄────────┘
(从树中移除,但 element 留着等下帧)
    │
    ▼
  unmount (defunct — 永不再用)
```

**两种 Element**:
- `ComponentElement`: 中间节点,持有其他 Element 作为 children
- `RenderObjectElement`: 叶子节点,持有 `RenderObject`,参与布局绘制

### 3. RenderObject 树(mutable layout/paint)

> 权威资料 docs.flutter.dev/resources/architectural-overview:
> "During the build phase, Flutter creates or updates an object that inherits from RenderObject for each RenderObjectElement in the element tree."

**核心方法**:
- `performLayout()`: 接收父 BoxConstraints,计算自身 size,递归调 child 的 `performLayout()`
- `paint()`: 把 layout 结果绘制到 Canvas(PictureLayer/ClipLayer 等)
- `compositeFrame()`: RenderView 根节点触发,合成整帧 Scene 交给 GPU

### 4. 渲染管线 build → layout → paint → composite

```
[vsync 信号 / 状态变化 / 纹理就绪]
        │
        ▼
1. BUILD   — widget.build() → element.update → renderObject.update
        │
        ▼
2. LAYOUT  — DFS render tree, 父传约束 → 子返 size
        │
        ▼
3. PAINT   — RenderObject.paint() → 生成 Layer (PictureLayer/ClipLayer)
        │
        ▼
4. COMPOSITE — SceneBuilder 合成 Scene → Window.render() → GPU
```

**Impeller**: 自 Flutter 3.10 起默认渲染器(C++),与 App 一起发布,不依赖设备 GPU 驱动版本。Web 平台仍用 Skia (WASM build)。

### 5. Box Constraint 模型

> 权威资料 docs.flutter.dev/resources/architectural-overview:
> "To perform layout, Flutter walks the render tree in a depth-first traversal and passes down size constraints from parent to child."
> "Children respond by passing up a size to their parent object within the constraints the parent established."
> "The box constraint model is very powerful as a way to layout objects in O(n) time"

**三种约束类型**:
- **tight**: minW == maxW 且 minH == maxH(RenderView 给屏幕)
- **loose**: min == 0, max 有限(子可自由取尺寸)
- **bounded**: min/max 都有限(子必须在范围内)

**关键规则**:
- 父决定约束范围(甚至可以强制尺寸 = tight)
- 子**必须**遵循父的 max 约束
- 子在父约束内自定 size,返给父

## 对比 / 选型

| 框架 | 渲染模型 | 三棵树 | 性能调优点 |
| --- | --- | --- | --- |
| **Flutter** | 自绘 (Skia/Impeller) | Widget / Element / RenderObject | 控制 `setState` 范围,避免无关 widget 重建 |
| **React Native** | 平台 native + C++ 中间层 | 无明确三棵树 (Shadow Tree 类似 Element) | 减少跨线程调用 |
| **Android 原生** | View 系统 | View tree | 减少 View 嵌套,避免过度绘制 |
| **iOS 原生** | UIView/CALayer | View/Layer 树 | offscreen rendering 标记 |

## 环境准备

- **Dart SDK**: 3.0+(运行 standalone demo)
- **操作系统**: 任意(纯 Dart,不依赖 Flutter SDK)

无 Flutter SDK 依赖;demo 模拟三棵树的语义。

## 运行方式

```bash
# 5 个独立 demo
dart 04-移动开发/03-跨平台/Flutter/三棵树/dart/widget_tree.dart
dart 04-移动开发/03-跨平台/Flutter/三棵树/dart/element_persistence.dart
dart 04-移动开发/03-跨平台/Flutter/三棵树/dart/render_object_layout.dart
dart 04-移动开发/03-跨平台/Flutter/三棵树/dart/box_constraint_model.dart
dart 04-移动开发/03-跨平台/Flutter/三棵树/dart/build_to_composite.dart
```

## 关键代码片段

**Widget 比较 / 复用**(`widget_tree.dart`):

```dart
final tree1 = ContainerW(color: 'blue', child: RowW([TextW('A'), TextW('B')]));
final tree2 = ContainerW(color: 'blue', child: RowW([TextW('A'), TextW('B')]));
// tree1 == tree2  -> true (runtimeType + key + props 全等)
// 框架走 update 分支,element 和 renderObject 复用
```

**Element 持久化**(`element_persistence.dart`):

```dart
// Build #1: 创建 element
FakeElement eText = updateChild(null, TextW('B'), parent);

// Build #2: text prop 变化 → 旧 element 被 unmount,新 element mount
eText = updateChild(eText, TextW('B-CHANGED'), parent);
// 同一个 eText 变量,但 runtime identity 已变(若 text 变了)
```

**Layout 约束传递**(`box_constraint_model.dart`):

```dart
// RenderView: tight (1080x1920)
final screen = BoxConstraints.tighten(width: 1080, height: 1920);
// Container: 透传约束(loose)
child._constraints = screen.loosen();
child.performLayout();
// 子节点在 [0, 1080] x [0, 1920] 内自由决定 size
```

## 性能与边界

| 指标 | 说明 |
| --- | --- |
| Widget 创建开销 | O(1) per widget(纯对象分配),常见 500+ widgets/frame 不卡 |
| Element 创建开销 | 比 widget 重(持有 BuildContext + renderObject 引用),要避免 |
| Layout 复杂度 | O(n) 单遍 DFS;只重算 dirty region |
| 帧预算 | 60Hz = 16.6 ms / 120Hz = 8.3 ms;超出掉帧 |
| Impeller 设备支持 | iOS 全部 / Android API 21+(可 fallback Skia);Web 用 Skia WASM |

## 注意事项与常见坑

1. **Widget 不可变**: 不要在 widget 内部存可变 state,改用 `StatefulWidget` + `setState`
2. **Element 不能跨 widget type 复用**: 同一位置 runtimeType 变了,element 重建(状态丢失!)
3. **GlobalKey 是昂贵的**: 用 GlobalKey 复用 element 会让 Flutter 走复杂路径,生产慎用
4. **Layout O(n) 不代表 layout 本身便宜**: RenderObject 内部算法可能是 O(n²)(如 IntrinsicHeight 双 pass)
5. **Paint 比 layout 更耗**: 大量 paint 操作(高斯模糊/复杂 gradient)是性能大头
6. **RepaintBoundary**: 用此 widget 包裹复杂子树,可阻止 paint 扩散到无关区域
7. **Const constructor**: 用 `const Widget(...)` 让 Flutter 复用同一 widget 实例,省内存

## 参考资料(实际阅读过的权威来源)

- [Flutter Architectural Overview](https://docs.flutter.dev/resources/architectural-overview) — 官方对 embedder / engine / framework 分层 + Widget vs Element vs RenderObject 的完整定义 + 渲染管线 4 阶段 + Box Constraint 模型 + Impeller vs Skia
- [Flutter API — Element class](https://api.flutter.dev/flutter/widgets/Element-class.html) — Element 完整生命周期(createElement → mount → update → activate → deactivate → unmount)的官方定义
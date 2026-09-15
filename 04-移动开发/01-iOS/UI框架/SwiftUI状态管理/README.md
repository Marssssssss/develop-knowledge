# SwiftUI 状态管理与 Observation 依赖追踪

> 04-移动开发 / 01-iOS / UI框架 / SwiftUI状态管理 —— demo 228(Swift / Objective-C / Python)

## 简介

SwiftUI 的界面是**状态的函数**:`body` 读状态 → 生成视图树;状态变了,框架只重算**读过它的那部分**。这套机制分两层:

- **状态容器**:`@State` / `@Binding` / `@Bindable` / `@StateObject` / `@Environment`,决定"值存在哪、谁能改"。
- **依赖追踪**:`@Observable`(Observation 框架,iOS 17+)记录"谁读了谁",把失效范围压到**属性级**;此前 Combine 时代的 `ObservableObject` + `@Published` 只能做**对象级**广播。

- **@State**:值类型的单一数据源,存储由 SwiftUI 管理,跨 `body` 求值保留。
- **@Binding**:对某个存储槽的读写投影(`$state`),让子视图能改父视图的状态。
- **@Observable**:宏,编译期为类型补上 `Observable` 一致性。
- **ObservationRegistrar**:记录属性访问与变更,`withObservationTracking` 的底座。
- **objectWillChange**:Combine 时代的广播点,粒度是整个对象。

## 原理详解

### 一、@State 的存储语义(三条都是从文档抠出来的)

1. **"SwiftUI manages the property's storage. When the value changes, SwiftUI updates the parts of the view hierarchy that depend on the value."** —— 存储不跟着结构体走,`body` 重新求值(结构体被重新实例化)时旧值保留。
2. **"A State property always instantiates its default value when SwiftUI instantiates the view. For this reason, avoid side effects and performance-intensive work when initializing the default value."** —— 默认值表达式**每次实例化都会求值**;要推迟到首次出现,用 `.task {}`(或 `@State private var x: T?` + 在 task 里赋值)。
3. **"Declare state as private ... share the state with any subviews that also need access, either directly for read-only access, or as a binding for read-write access."** —— 传值 = 只读;传 `$state` = 可写。

```
父视图                      子视图
@State private var isPlaying  @Binding var isPlaying
        │                              ▲
        └── PlayButton(isPlaying: $isPlaying) ──┘   (二者指向同一存储槽)
```

### 二、@Observable 的细粒度追踪

```swift
@Observable class Book { var title = "A sample book"; var isAvailable = true }
struct BookView: View { var book: Book; var body: some View { Text(book.title) } }
```

文档原话:**"SwiftUI updates the subview anytime an observable property of the object changes, but only when the subview's body reads the property"** —— 上面这个 `BookView` 在 `title` 变化时更新,在 `isAvailable` 变化时**不更新**。

底层靠 `withObservationTracking`:

```swift
withObservationTracking {
    for car in cars { print(car.name) }      // ← 只追踪这里读过的属性
} onChange: {
    print("Schedule renderer.")              // ← 读过的属性变了才回调
}
```

文档明确:**"the function only tracks properties read in its apply closure"**,所以上面这段不会因为 `needsRepair` 变化而回调。

### 三、粗粒度 vs 细粒度

| 维度 | ObservableObject + @Published(Combine) | @Observable(Observation) |
| --- | --- | --- |
| 追踪粒度 | 对象级:任何属性变化都发 `objectWillChange` | 属性级:只通知读过该属性的视图 |
| 需要 `@Published` | 需要 | 不需要(宏自动处理) |
| 视图包装 | `@StateObject` / `@ObservedObject` / `@EnvironmentObject` | 直接 `@State` / 普通属性传参 / `@Bindable` |
| 局部属性绑定 | 需要 `Binding(get:set:)` 手写 | `@Bindable var book: Book` + `$book.title` |
| 追踪时机 | 变更前广播(willSet) | 也是变更前(注册器的 `willSet`),但读集合是**求值期实测**的 |

> **经典陷阱**(文档原文):*"It's possible to store an object that conforms to the ObservableObject protocol in a State property. However the view will only update when the reference to the object changes ... The view will not update if any of the object's published properties change."* —— 把 `ObservableObject` 塞进 `@State`,改属性不刷新,只有换掉整个引用才刷新;正确做法是 `@StateObject` 或改用 `@Observable`。

## 环境准备

- Python 3.9+(纯标准库,**本机可直接跑**)
- Swift 5.9+ / Xcode 15+(Observation 框架自 iOS 17 / macOS 14 起)
- Objective-C:clang + Foundation

## 运行方式

```bash
python3 python/observation_check.py
swift swift/SwiftObservationState.swift
clang -fobjc-arc -framework Foundation objc/ObjCStateTracking.m -o state && ./state
```

## 关键代码片段

```python
# python/observation_check.py —— 注册器的三条语义
def access(self, key):            # apply 闭包求值期:记下这次读了谁
    if self.reading is not None:
        self.reading.add(key)

def track(self, apply, on_change): # 求值 → 把读过的 key 登记到失效回调
    reads = set(); self.reading = reads
    apply(); self.reading = None
    for key in reads:
        self.deps.setdefault(key, []).append(on_change)

def mutation(self, key):           # 写入前:只通知读过该 key 的依赖(一次性)
    for cb in self.deps.pop(key, []):
        cb()
```

## 性能与边界

- **细粒度的收益只在"读得少"时兑现**:视图读的属性越少、更新越局部;一个 `body` 读遍整个对象,细粒度就退化成粗粒度。
- **`@State` 默认值会重复求值**:`@State private var vm = ViewModel()` 每次实例化都构造一次,高频刷新的视图会白烧 CPU(文档建议 `.task` 推迟)。
- **追踪是一次性的**:`withObservationTracking` 触发一次后需要重新登记;SwiftUI 在每次 `body` 求值时重新注册,所以写自己的追踪代码时必须自己负责"重新武装"。
- **跨 Actor / 线程**:文档注明 *"You can safely mutate state properties from any thread."*;`@State` 本身是 `Sendable` 的 `DynamicProperty`。
- **iOS 17 的分界线**:`@Observable` 在 iOS 17+ 可用;低版本仍须 `ObservableObject`。

## 注意事项与常见坑

| 现象 | 原因 | 规避 |
| --- | --- | --- |
| 改属性界面不刷新 | `ObservableObject` 放进了 `@State`(只监听引用变化) | 换 `@StateObject`,或改用 `@Observable` |
| 整个界面频繁重算 | 视图的 `body` 读了大对象的所有属性 | 拆分视图,让每个 `body` 只读自己需要的属性 |
| 子视图改了值父视图没反应 | 传的是值(只读)而不是 `$binding` | 传 `@Binding`,需要属性级绑定用 `@Bindable` |
| 默认值里的网络请求被执行多次 | `@State` 默认值每次实例化都求值 | `.task {}` 里赋值,或用可选类型 |
| 用 `@State` 存"半持久"数据 | `@State` 随视图移除而销毁 | 需要跨视图生命周期用 `@StateObject` / 环境对象 |
| 追踪代码只生效一次 | `withObservationTracking` 一次性 | 在回调里重新登记(或交给 SwiftUI) |

## 参考资料(实际读过的来源)

- [Apple — SwiftUI/State](https://developer.apple.com/documentation/swiftui/state) —— @State 的存储语义、"updates the parts of the view hierarchy that depend on the value"、private + Binding 的共享规则、默认值每次实例化都求值(及 `.task` 推迟)、存 observable object 的写法与 `@Bindable` 的用法、ObservableObject 放进 State 的陷阱原文。
- [Apple — Observation 框架](https://developer.apple.com/documentation/observation) —— `@Observable` 宏在编译期补 Observable 一致性、`withObservationTracking(_:onChange:)` 只追踪 apply 闭包读过的属性(car.name / needsRepair 的例子)、`ObservationRegistrar`、`ObservationIgnored` / `ObservationTracked` 宏、`Observations` 异步序列。
- [Apple — SwiftUI/State 的 "Store observable objects" 与 "Share observable state objects with subviews" 两节](https://developer.apple.com/documentation/swiftui/state) —— Book/BookView 例:"updates each time title changes but not when isAvailable changes"。

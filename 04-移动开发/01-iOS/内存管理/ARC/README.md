# Swift / Objective-C ARC 内存管理

> 04-移动开发 / 01-iOS / 内存管理 / ARC — demo 024

## 简介

ARC (Automatic Reference Counting) 是 Swift 与 Objective-C (从 iOS 5 / OS X 10.7 起默认启用) 的**编译期自动内存管理机制**。编译器在合适位置插入 `retain` / `release` 调用,运行时引用计数归零即调用 `deinit` / `dealloc` 回收实例。引用计数只适用于**类(class)**实例,值类型(struct/enum)由复制语义管理,不在 ARC 管辖范围。

- **关键概念**
  - 强引用 strong —— 默认引用行为,引用计数 +1,持有者存活时实例不被释放。
  - 弱引用 weak —— 不增加计数,目标释放后自动置 `nil`,必须是可选类型(`T?`),不可用于 `let`。
  - 无主引用 unowned —— 不增加计数且**不自动置 nil**,非可选(`T`),访问已释放实例触发运行时错误。
  - 强引用循环 strong reference cycle —— 互相强引用或闭包强引用 `self` 导致引用计数永远 ≥ 2,实例永不释放。
  - 捕获列表 capture list —— `[weak self]` / `[unowned self]` 在闭包定义处显式声明引用语义,打破 self ↔ closure 循环。

- **历史背景**
  ARC 由 Apple 在 2011 年随 iOS 5 引入 Objective-C(LLVM 编译器侧实现,2011 WWDC #322),Swift 自 2014 年首发即全面采用 ARC,并把"weak 是可选且自动置 nil"的语义直接写进类型系统(WWDC 2021 #10216 详细解释)。

## 原理详解

### 工作机制分步

1. **编译期扫描**:编译器遍历所有类的实例化与赋值语句,在每个引用边界插入 retain/release 指令。
2. **运行时计数**:每个 class 实例在堆上保存引用计数 `retainCount`(Swift 标准库内部字段,可通过 `CFGetRetainCount` 桥接读取,见 WWDC 2021 #10216)。
3. **赋值语义**:
   - 赋值语句 `b = a` → 编译器在 `b` 拿到引用前插 `retain(a)`,在 `b` 不再使用时插 `release`。
   - 局部变量作用域结束时,编译器在作用域末尾插 `release`(Swift 5.0 起 `setjmp`/`longjmp` 仍要保持平衡)。
4. **归零触发 deinit**:Swift 编译器保证 retain/release 配对;计数归零后运行时调用 `deinit` / Objective-C `dealloc`。
5. **强引用循环检测失败**:若 A 与 B 互相强引用,即使外部所有局部引用都断开,内部 `A → B` / `B → A` 仍持有,引用计数 ≥ 2,ARC 无法归零 → 内存泄漏。
6. **打破循环的两种路径**:
   - **weak**: 让一侧变成可空可选,引用计数不变,引用对象释放后自动归零。Swift Book:"ARC automatically sets a weak reference to nil when the instance that it refers to is deallocated."
   - **unowned**: 让一侧变成非可选且不自动置 nil,**仅在生命周期严格 ≥ 持有者时安全**(如 CreditCard 总是绑定在 Customer 上)。访问已释放的 unowned 引用触发运行时 crash。
7. **闭包捕获列表**:闭包默认对捕获的所有引用类型值强引用,与 self 形成 self ↔ closure 循环。捕获列表 `[weak self]` 或 `[unowned self]` 写在闭包参数前,改变引用语义。

### 核心 API / 关键字对照

| Swift 关键字 | Objective-C 属性 | 是否可选 | 是否自动置 nil | 引用计数 +1? | 适用场景 |
| --- | --- | --- | --- | --- | --- |
| `T`(默认) | `strong T *` | — | — | ✓ | 主引用、拥有所有权 |
| `weak var t: T?` | `__weak T *t` | 必选 | ✓(运行时清空) | ✗ | 委托、子节点(parent → child) |
| `unowned let t: T` | `__unsafe_unretained T *t` | 非可选 | ✗(悬挂指针) | ✗ | 同生命周期或更长(child → parent) |
| `unowned(unsafe) let t: T` | — | 非可选 | ✗ | ✗ | 极致性能、关闭运行时安全检查 |

> Swift Book 关于 `unowned(unsafe)`: *"for cases where you need to disable runtime safety checks — for example, for performance reasons."*
> Objective-C 历史上无 `unowned` 概念,`__unsafe_unretained` 是性能等价物;Swift `unowned` 默认是 `unowned(safe)`,访问时插入空指针检查。

### 引用计数等价示意

```
Class 实例
┌──────────┐
│ 类型 vtable │ ← ARC 不动
│ stored props │
│ retainCount │ ← 运行时增减,初始 1
└──────────┘
        ▲
        │ strong ref (retainCount +1)
        │
   持有者 (let/var/属性)
```

```
Person2 ──strong──▶ Apartment2 ──weak──▶ Person2     ← weak 边不计入 retainCount
   (rc=2)             (rc=1)                ↑
                                          self 局部变量 (rc=1)
```

```
闭包 ↔ strong self  ──────▶ 循环
   解决:[weak self]  ──────▶ 不参与 retainCount,deinit 正常
```

### 底层发生了什么(运行时视角)

- Swift ARC 由 **LLVM Swift 编译器前端**实现,所有 retain/release 在 IR 层可见;`retainCount` 字段对 Swift 是不公开 API,但可通过 `CFGetRetainCount(T)` 桥接到 CoreFoundation 的 `CFTypeRef` 计数。
- Objective-C ARC 由 **LLVM Objective-C ARC pass** 实现,同样在 IR 层插入 `objc_retain` / `objc_release`,对应 `id` 上的方法调用。
- WWDC 2021 #10216 强调:**Swift 对象生命周期是基于"最后使用"(use-based),编译器在最后使用之后立刻 release**;但开启 ARC 优化后,实际 deinit 可能晚于最后使用,这是"观察到的生命周期"区别于"保证的最小生命周期",靠 observed lifetimes 的代码可能在优化变化时坏掉。

## 对比 / 选型

| 维度 | Swift ARC | Objective-C ARC | C++ RAII(`std::shared_ptr`) |
| --- | --- | --- | --- |
| 计数单位 | 类的实例 + 闭包捕获 | 任意 `id`(包括 Block) | 任意对象,weak 用 `std::weak_ptr` |
| 入口 | 编译器强制 ARC(无 opt-out) | `-fobjc-arc` 编译选项 | `std::make_shared<T>` |
| weak 语法 | `weak var x: T?` | `__weak T *x` | `std::weak_ptr<T> x` |
| unowned 语法 | `unowned let x: T` / `unowned(unsafe)` | `__unsafe_unretained T *x` | 无内置等价 |
| 闭包循环 | `[weak self] in` 捕获列表 | `__weak typeof(self) wself = self` + 块内 strengthen | `weak_ptr`,无原生块 |
| 调试工具 | Memory Graph Debugger + Instruments Leaks/Allocations | 同 + `xcrun leaks` | ASan / LeakSan |
| 跨线程 | 与 GCD 兼容,atomic 引用计数 | 同 | `shared_ptr` atomic 计数需显式 `std::atomic` |

## 环境准备

- 操作系统:macOS / iOS(本 demo 无 GUI 依赖,macOS 命令行即可)
- 语言版本:Swift 5.0+(捕获列表语义) / Objective-C 2.0(ARC 起)
- 依赖:Swift 标准库、Foundation、CoreFoundation(`CFGetRetainCount` 在 `CFBase.h`)

## 运行方式

### Swift

```bash
swift SwiftARC.swift
```

预期 stdout:

```
[1] Person1 Alice init
retainCount(p1) = 3
retainCount(p1) = 2
retainCount(p1) = 1
[1] Person1 Alice deinit

[2] Person2 John init
[2] Apartment2 4A init
[2] Person2 John deinit
[2] Apartment2 4A deinit

[3] Customer3 Alice init
[3] CreditCard3 #1234567890123456 init
[3] CreditCard3 #1234567890123456 deinit
[3] Customer3 Alice deinit

[4] HTMLElement4 <p> init
asHTMLWeakSelf() = <p>hello, world</p>
[4] HTMLElement4 <p> deinit

(预期: div 未 deinit,因闭包持有 self 强引用形成循环)
```

### Objective-C

```bash
clang -fobjc-arc -framework Foundation ObjCARC.m -o objc_arc
./objc_arc
```

## 关键代码片段

### Swift: weak 打破 Person / Apartment 循环

```swift
class Person2 {
    let name: String
    var apartment: Apartment2?
    init(name: String) { self.name = name; print("[2] Person2 \(name) init") }
    deinit { print("[2] Person2 \(name) deinit") }
}
class Apartment2 {
    let unit: String
    weak var tenant: Person2?     // <-- 关键: weak 可选 + 不计数
    init(unit: String) { self.unit = unit; print("[2] Apartment2 \(unit) init") }
    deinit { print("[2] Apartment2 \(unit) deinit") }
}

let john: Person2? = Person2(name: "John")
let unit4A: Apartment2? = Apartment2(unit: "4A")
john!.apartment = unit4A
unit4A!.tenant = john             // 弱引用,不增加 john 的 retainCount
// john=nil, unit4A=nil 后二者都 deinit
```

### Swift: 闭包捕获列表 `[weak self]`

```swift
class HTMLElement4 {
    let name: String
    lazy var asHTMLWeakSelf: () -> String = {
        return "<\(self.name)>\(self.text ?? "")</\(self.name)>"
    } // 默认仍是强引用 —— 改为如下版本:
    func setWeakSelfClosure() {
        asHTMLWeakSelf = { [weak self] in
            guard let self = self else { return "<deinit>" }
            return "<\(self.name)>\(self.text ?? "")</\(self.name)>"
        }
    }
}
```

### Objective-C: `__weak` Block 捕获

```objc
- (void)setupWeakSelfBlock {
    __weak typeof(self) wself = self;
    _asHTMLWeakSelf = ^NSString *{
        __strong typeof(wself) sself = wself;   // 临时强引用防止中途释放
        if (!sself) return @"<deinit>";
        return [NSString stringWithFormat:@"<%@>%@</%@>",
                sself.name, sself.text ?: @"", sself.name];
    };
}
```

## 性能与边界

- **retain/release 成本**:每次 retain / release 是一次 atomic 操作(在 ObjC 引用计数下,Swift ARC 仍调用相同 runtime)。开启 `OS_OPTIMIZATION` 与 MRC 优化后,大多数情况下编译器会优化掉可证明的 retain/release 对(类似 C++ 优化 `std::shared_ptr` 的拷贝)。
- **引用计数上限**:CFRetain 在 `retainCount` 接近 `INT_MAX-1` 时会溢出触发断言。生产中应避免用单实例做长期容器(用 weak pool)。
- **`CFGetRetainCount` 不准确**:Swift / ObjC 都警告此 API 不能用来判断对象生命周期(ARC 优化下某些引用可能晚于最后使用才释放)。WWDC 2021 #10216 明确说 "object lifetimes are determined by the retain and release operations inserted by the Swift compiler... observed lifetimes may differ from their guaranteed minimum"。
- **weak 引用的额外成本**:weak 引用在对象释放时需要把 side table / global weak table 中对应条目清零(2 次引用写)。Swift 在 swift-frontend 内联了 `_swift_weakInit` / `_swift_weakDestroy`,典型额外开销约 1-5 倍于普通 retain。

## 注意事项与常见坑

| 现象 | 原因 | 规避方法 |
| --- | --- | --- |
| `weak var` 在 `let` 声明时报错 | weak 必须是 `var` 且可选,Swift 类型系统强制 | 用 `var x: T? = nil` + `weak var x: T?` 即可 |
| `weak` 引用在 `deinit` 中变成 nil | weak 的目的就是允许访问时为 nil | 在闭包里 `guard let self = self else { return }` |
| `unowned` 访问触发 EXC_BAD_ACCESS | unowned 不自动置 nil,对象已释放 | 改用 `weak` 或重新设计对象生命周期 |
| `[unowned self]` 闭包内访问 self crash | 同上 | 改 `[weak self]` + `guard let` |
| lazy var 触发 `self` 与闭包循环 | lazy 闭包默认强引用 self | 用 `[weak self]` 捕获列表 |
| Objective-C Block 强引用 self | Block 默认对捕获值强引用 | 用 `__weak typeof(self) wself = self` |
| `Timer.scheduledTimer(...)` + closure 强引用 self | timer 持有 closure,closure 持有 self | 用 `weak self` + 在 `deinit` 里 `invalidate()` |
| `NotificationCenter` block observer 强引用 self | observer token 持有 block | 保留 token,`deinit` 调 `removeObserver(token)` |
| `ARC` 看不到的"泄漏"(Foundation 单例持有) | 单例持有导致永不释放 | 检查 `NotificationCenter.default` / `FileManager.default` 等 |
| `CFGetRetainCount` 与实际释放时机不符 | ARC 优化可能延后 release | **不要**靠此 API 判断对象生命周期,看 deinit 日志 |

> ⚠️ 生产环境强烈建议开启 Memory Graph Debugger + Instruments (Leaks / Allocations) 做静态扫描;ARC 不解决 retain cycle,只保证"非循环强引用都能释放"。

## 参考资料(实际阅读过的权威来源)

- [Swift Book — Automatic Reference Counting](https://docs.swift.org/swift-book/documentation/the-swift-programming-language/automaticreferencecounting/) — Swift 官方手册对 strong / weak / unowned / 闭包捕获列表的完整定义、Person/Apartment 与 HTMLElement 示例、unowned(safe) 与 unowned(unsafe) 区分的原文。
- [WWDC 2021 #10216 "ARC in Swift: Basics and beyond"](https://developer.apple.com/videos/play/wwdc2021/10216/) — Apple 官方对 Swift ARC "use-based lifetime"、observed vs guaranteed lifetime 的详细解释。
- [Apple Transitioning to ARC Release Notes](https://developer.apple.com/library/archive/releasenotes/ObjectiveC/RN-TransitioningToARC/Introduction/Introduction.html) — Objective-C ARC 历史背景、编译器行为、与 MRC 的差异。
- [Apple — Finding memory leaks](https://developer.apple.com/documentation/xcode/finding-memory-leaks) — Memory Graph Debugger + Instruments Leaks 使用。
- [Apple — Memory Debugger](https://developer.apple.com/documentation/xcode/memory-debugger) — Xcode 中检测循环引用的工具。
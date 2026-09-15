# iOS 启动优化 · dyld pre-main 四阶段

把 `main()` 之前的加载过程拆成可量化的四段,并回答三个工程问题:**时间花在哪、动哪个变量
最划算、哪个数字根本数不到**。Python 侧是可断言的成本模型,ObjC 侧是 `+load` / `+initialize`
的**顺序与次数**实测(全部是 runtime 硬保证),Swift 侧给出语言层面就把这件事解决掉的对照。

## 1. 四阶段是什么

Apple WWDC 2016 Session 406《Optimizing App Startup Time》把 pre-main 拆成:

| 阶段 | 做什么 | 成本与什么成正比 |
| --- | --- | --- |
| **Load dylibs** | 递归加载依赖的 dylib:读 Mach-O、验签、对每个 segment `mmap()` | 库的**个数**(每个一份固定开销)。iOS App 通常依赖 100~400 个库,系统库已进 dyld shared cache |
| **Rebase / Bind** | ASLR 把镜像放在随机基址:修正「镜像内部指针」(Rebase,偏 IO)与「指向镜像外部的符号」(Bind,偏 CPU) | Rebase ∝ 内部指针数;Bind ∝ **外部符号引用数**(两本账) |
| **ObjC setup** | 注册所有 ObjC 类、把 category 方法插进方法列表、selector 去重 | 类数 + category 数 + selector **引用次数** |
| **Initializers** | 跑每个类/分类的 `+load`、C/C++ 构造器、非平凡类型的静态全局对象 | 你写的初始化代码量 |

一句话:**前三段是"你声明了多少东西"的代价,第四段是"你写了多少初始化代码"的代价** ——
只有第四段能靠纯重构直接砍掉。

## 2. 本模型给出的量级关系(`python/startup_check.py`,24 条断言)

系数是**合成值**,只保留形状(哪一项与什么成正比、哪一项是固定开销),不预测真实毫秒数。
真实机上唯一可信的数字来源是 `DYLD_PRINT_STATISTICS=1`。

baseline(180 dylib / 1200 类 / 210 个 `+load`)拆出来大致是:

```
Load dylibs=2160   Rebase/Bind=970   ObjC setup=1384   Initializers=946   合计=5460
```

四条被断言过的关系:

1. **dylib 数量是严格线性**:100 → 400 个,Load dylibs 正好 ×4。每个 dylib 的固定开销与它
   多大无关 —— 这是"嵌入 6 个三方动态库"这类做法的直接代价。
2. **Rebase 与 Bind 是两本账**:类数 ×4 时 Rebase 严格 ×4(镜像内指针跟着涨),而 Bind
   **一点不动**(类和外部符号无关)。所以"合并动态库"能省 Load dylibs,却不会自动省 Bind。
3. **selector 的代价在"引用次数",不在"唯一个数"**:引用 ×10 时唯一 selector 只涨 1.87 倍
   (去重把小表压瘪了),引用项却是唯一项的 2 倍多。
4. **成本可加**:三个优化项一起改的收益 = 三项各自收益之和,互不折扣 —— 所以排序即优先级。

## 3. 最省的一段:`+load` → `+initialize`(或干脆懒加载)

`Initializers` 段的成本是三样东西之和:`+load` 方法体、C/C++ 构造器、非平凡静态全局对象。
把 210 个 `+load` 换成懒加载后,该段从 **946 降到 316**(剩的全是没动的构造器与静态全局对象),
而且 **pre-main + 首屏的总量也更小** —— 因为 210 个类里只有 30 个真的在首屏前被用到,
其余的**永远不必初始化**。

`swift/` 用可运行的代码演示了为什么 Swift 在这件事上天生占便宜:Swift 的全局常量与存储型
类型属性一律**懒初始化**,且由 `dispatch_once` 保证只求值一次:

```swift
let expensiveGlobal: String = { probe.bumpGlobal(); return "..." }()   // 声明时并不求值
enum Config { static let expensive: String = { probe.bumpType(); return "..." }() }
```

实测:在 `main` 入口处两个闭包都**一次没跑**;首次访问才各跑一次;重复访问不再求值。
Swift Blog《Files and Initialization》给的正是这个理由:
*"startup time in Swift scales cleanly with no global initializers to slow it down"*。
ObjC 的 `+load` 恰好相反 —— 无条件、在 pre-main 全部执行完。

## 4. `+load` 与 `+initialize` 的硬保证(`objc/LaunchOrder.m`)

这个文件不测快慢,只测**顺序与次数**,因为它们全是 runtime 的硬保证:

| 事实 | 断言 |
| --- | --- |
| 父类 `+load` 先于子类 `+load` | `SuperClass` 的 +load 索引 < `SubWithLoad` 的 +load 索引 |
| 类的 `+load` 先于**它自己的分类**的 `+load` | `SuperClass` < `SuperClass(Extra)` |
| 类与分类同时实现 `+load` 时**两者都会被调用**(不覆盖) | 两个各计 1 次 |
| `+load` **不被继承** | 子类没实现时,父类的 `+load` 只跑 1 次,而不是被子类"继承"到第 2 次 |
| `+load` 是**直接按 IMP 调用**,不走 `objc_msgSend` | 上一条的原因;因此 `+load` 里"能做的事很少" |
| `+initialize` 是**懒**的 | `main` 开始时没被发过消息的类,其 `+initialize` 一次都没跑;连 `[Cls class]` 都不触发 |
| `+initialize` **会被继承** | 子类没实现时,父类那份实现会以 `self == SubClass` 再跑一次 |
| 所以必须判 `self == [Parent class]` | 否则一个类的初始化会被执行两遍,而且第二遍的对象是错的 |
| `__attribute__((constructor))` 也在 main 之前 | 但它与 `+load` 的相对顺序**不保证**,只保证都早于 `main` |

顺带一个被广泛忽略的事实:分类之间、类之间的 `+load` 顺序取决于**编译顺序**
(Compile Sources),跨类的 `+load` 先后**不应该被代码依赖**。

## 5. 四段之外:缺页

`launch path` 上每触及一个尚未驻留的页就要一次 page fault。二进制重排(binary reordering)
把启动会走的代码与数据排在一起,减少的正是这一项 —— 而它**不在四段里**,
`DYLD_PRINT_STATISTICS` 也看不到,只能用 Instruments 的 Page Fault 计数看。

所以「启动耗时 = pre-main 四段之和」是个**不完整的等式**,至少还要加缺页与 `main()` 之后的全部工作。

## 6. 目录与运行

```
python/startup_check.py   # 成本模型 + 24 条断言 ← 本目录唯一被实际跑过的自检
swift/StartupModel.swift  # 四阶段模型 + lazy 探针
swift/main.swift          # 4 个场景
objc/LaunchOrder.m        # +load / +initialize 的顺序与次数(10 条断言)
```

```bash
python3 python/startup_check.py                                     # → 断言 24 通过 / 0 失败
swiftc StartupModel.swift main.swift -o startup-model && ./startup-model
clang -fobjc-arc -framework Foundation LaunchOrder.m -o launch-order && ./launch-order
```

环境:Python 3.8+(仅标准库);Swift 5.7+ / macOS 13+;ObjC 用 ARC。
**`main.swift` 里不能再写 `@main`**,同一模块内两者冲突。

## 7. 常见坑

1. **把 `+load` 当初始化入口。** 它在 pre-main 单线程执行,**无条件**、**不看用不用得到**,
   而且是启动路径上最贵的一段业务代码。除了 Method Swizzling 这类必须在类加载时做的事,
   一律改用懒加载。
2. **以为 `+load` 会像普通方法一样继承。** 不会。它是按 IMP 直接调用的,子类不实现就没有。
3. **`+initialize` 里不判 `self`。** 子类没实现时父类的实现会被替子类再跑一次,
   不判 `self == [Parent class]` 就会重复初始化,且第二次的 `self` 是错的类。
4. **依赖跨类 `+load` 的先后顺序。** 顺序由编译顺序决定,不是语言保证。
5. **只看 `DYLD_PRINT_STATISTICS` 就以为看全了启动耗时。** 它只覆盖 pre-main 四段,
   不含缺页,也不含 `main()` 之后 —— 而 `didFinishLaunching` 里的 SDK 初始化常常才是大头。
6. **为了优化而优化。** 是否合并动态库、是否重排二进制,取决于测量结果;
   本模型只告诉你各项的**方向与形状**,不给系数。
7. **写断言时的自伤。** 本目录开发中出现过 1 次"实现对了、断言错了":"降到 1/3 以下"
   恰好卡在 33.4% 的边界上。**边界型断言要么改成有意义的量级判断,要么把实测值打出来。**

## 8. 参考资料

Apple 官方与提案(核心结论均出自这里):

1. *WWDC 2016 Session 406 · Optimizing App Startup Time* —— pre-main 四阶段的原始出处
   (Load dylibs / Rebase+Bind / ObjC setup / Initializers)与各阶段优化结论。
   https://developer.apple.com/videos/play/wwdc2016/406/
2. *The Swift Programming Language · Properties* —— 全局常量与变量**总是懒求值**;
   存储型类型属性首次访问才初始化,且**即使多线程同时访问也保证只初始化一次**。
   https://docs.swift.org/swift-book/LanguageGuide/Properties.html
3. *Swift Blog(已归档)· Files and Initialization* —— 懒初始化的设计理由原话:
   *"startup time in Swift scales cleanly with no global initializers to slow it down"*;
   并说明全局变量的懒初始化底层是 `dispatch_once`,所以「声明一个私有全局变量」就是单例。
4. *GNU Objective-C runtime 文档 · `+load`: Executing code before main* ——
   `+load` 的语义:"The `+load` implementation of all super classes of a class are executed
   before the `+load` of that class";以及 "`+load` is to be used only as a last resort"。
   https://mirrors.hust.edu.cn/git/gcc.git/tree/gcc/doc/objc.texi
5. *Objective-C Runtime 源码 `objc-loadmethod.mm`* 的调用顺序(经二手资料cross-check):
   `schedule_class_load` 递归先排父类;`call_load_methods` 先跑完所有类的 `+load`
   再跑分类的 `+load`;类之间/分类之间按编译顺序。

工程资料(用于确认各阶段的行为与实测口径):

6. *理解 Objective-C 中 load 方法的执行顺序* —— 四个顺序维度(父类/子类、类/分类、
   category 之间、类之间)与"编译顺序决定"的说法。
   https://bcdaka.github.io/posts/ceab5d65ab1b0a84f14ab4e26eb9e9db
7. *iOS 底层原理 Category load initialize 关联对象* —— `call_load_methods` 循环消费
   `loadable_classes` / `loadable_categories` 两个数组、按 IMP 直接调用、主类与分类共存。
   https://juejin.cn/post/7683031366587203610
8. *App 启动时间优化* —— `DYLD_PRINT_STATISTICS=1` 的用法与 pre-main 各阶段结论
   (「动态库加载越多,启动越慢;ObjC 类越多,启动越慢;`+load` 越多,启动越慢」)。
   https://blog.csdn.net/mpk_github/article/details/120545435
9. *iOS App 启动优化* —— Rebase(偏 IO)/ Bind(偏 CPU)的分工、ASLR 与 slide 的关系、
   `dispatch_set_target_queue` 之外的 pre-main 优化结论。
   https://www.51cto.com/article/692142.html
10. *App 启动流程与 dyld3* —— dyld shared cache 的定位、dyld2 → dyld3 的演进,
    以及"100~400 个库"这一数量级的来源。
    https://juejin.cn/post/7651508834532573238
11. *Swift globals and static members are atomic and lazily computed* —— 对 Properties 一章
    那两段 Note 的交叉验证,含 `let` 与 `var` 在线程安全上的差别。
    https://cur.at/F4yjiws?m=web

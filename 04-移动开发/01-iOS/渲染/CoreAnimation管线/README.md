# Core Animation 渲染管线

把「一帧怎么变成屏幕上的像素」拆成可量化的两半:**应用进程内的 Commit** 与
**render server + GPU 的 Render**,并回答三个问题:掉帧到底卡在哪一段、哪些写法会
凭空多出一次 pass、哪些性能开关是负收益。

## 1. Render Loop 的五段

Apple Tech Talk《Explore UI animation hitches and the render loop》给出的顺序:

```
Event ──► Commit(应用进程内)                      ──► render server ──► GPU ──► VSYNC 上屏
          Layout ─► Display ─► Prepare(打包+IPC)      线性流水线      合成
```

| 段 | 位置 | 做什么 |
| --- | --- | --- |
| **Layout** | 应用进程 | 逐个计算 frame/bounds,顺序是**父→子**;请求会被合并 |
| **Display** | 应用进程 | 自定义绘制的 layer 拿到 texture-backed Core Graphics context 画一遍 |
| **Prepare** | 应用进程 | 收集图层属性与动画参数、解码图片、打包 |
| **Commit** | 应用进程 | 经 IPC 把整棵变化的图层树交给 render server |
| **(render)** | render server + GPU | 排成线性流水线(父→子、兄→弟、由后至前)→ GPU 合成 |

**每个阶段的 deadline 都是下一个 VSYNC。** 60Hz 是 16.67ms,120Hz 只有 8.33ms。
错过叫 **hitch**,按整帧计:迟到 1 帧 = 16.67ms,2 帧 = 33.34ms。hitch 分两类 ——
**commit hitch**(应用进程内,你能改)与 **render hitch**(render server 内,通常只能靠
减少层数/离屏/混合来治)。而且整条流水线是**并行**的:应用准备第 N+1 帧的同时,
系统在渲染第 N 帧 —— 这是 deadline 一旦错过就立刻可见的原因。

## 2. 为什么几千个 layer 还能跑满 60fps(`python/render_loop_check.py`,29 条断言)

因为 **Layout / Display 都有独立的脏标记,而且同一帧内的重复请求会被合并**:

* 一个 10000 层的界面上改 50 个 layer 的 bounds:只访问这 **50** 个,其余 9950 个一次不碰
  —— 访问量少 **200 倍**;
* 把 20 个 layer 各标脏 **1000 次**,layout 仍然只访问 20 个 —— `dirty` 是布尔量,
  标 1 次与标 1000 次的成本**完全相同**。所以「频繁改属性」不一定慢,「改很多不同的 layer」才慢。

## 3. Layout 与 Display 是两条账(`objc/RenderTraps.m` 用真实 CALayer 数出来的)

`objc/RenderTraps.m` 不用模拟器,而是继承 `CALayer` 覆盖 `drawInContext:`,数它被调用的**次数**。
在 20 个子层上实测:

| 操作 | layout | `drawInContext:` |
| --- | --- | --- |
| 只改 `frame` | 会被标脏 | **0 次** |
| `setNeedsDisplay` | 不触发 | **恰好 1 次**;再 `displayIfNeeded` 不再重复 |
| 打开 `needsDisplayOnBoundsChange` 后改 `frame` | 被标脏 | **1 次** |
| 20 个子层各标脏两次 | — | **20 次**(不是 40 次) |
| 同一事务内改 5 个层 | — | **5 次**,合成一次 commit |

最后两行是关键:**Display 与 Layout 是独立的两条脏标记,但共用同一个 commit 边界** ——
所谓「主线程卡住 = 掉帧」(commit hitch)说的就是这一段。

`needsDisplayOnBoundsChange` 那一行解释了最容易被忽略的一处:`CATextLayer` / `UILabel`
内部在 bounds 变化时会自己 `setNeedsDisplay`,于是**「只是移动一个 Label」也要重绘**,
代价比第 1 行高一个数量级。

## 4. GPU 侧的三笔账

| 项 | 触发条件 | 代价 | 修法 |
| --- | --- | --- | --- |
| **离屏渲染** | `cornerRadius` + `masksToBounds`;阴影**没有** `shadowPath`;`mask`;模糊 | 每个 layer 一次额外 pass + 2× 缓冲内存 + 两次合成 | 预圆角图片;补 `shadowPath`(`CGPathCreateWithRoundedRect`);静态形状用 `CAShapeLayer` |
| **shouldRasterize** | 手动开启 | 一次光栅化 + **每次失效重建**,并额外占内存 | 只在「内容稳定复用」时开;模型里平衡点是 **5 帧**,复用 1 帧就失效 = 净收益 **-5.5** |
| **混合 / overdraw** | 半透明层相互重叠 | GPU 填充率 ∝ **覆盖像素 × 层数** | 声明 `opaque = YES`、给 layer 实心 `backgroundColor`、隐藏用 `isHidden` 而不是 alpha=0 |

`shouldRasterize` 那条值得展开:列表里给 cell 开它**常常是负收益** —— cell 快速复用时
缓存立刻失效,Instruments 的 `Color Hits Green and Misses Red` 会显示成一片红。

## 5. 目录与运行

```
python/render_loop_check.py  # 渲染循环成本模型 + 29 条断言 ← 本目录唯一被实际跑过的自检
swift/RenderLoop.swift       # 同一套系数的 Swift 实现
swift/main.swift             # 4 个场景(纯 Foundation,不需要 UIKit)
objc/RenderTraps.m           # 真实 CALayer 实验:数 drawInContext: 被调用几次
```

```bash
python3 python/render_loop_check.py                                 # → 断言 29 通过 / 0 失败
swiftc RenderLoop.swift main.swift -o render-loop && ./render-loop
clang -fobjc-arc -framework Foundation -framework QuartzCore RenderTraps.m -o render-traps && ./render-traps
```

环境:Python 3.8+(仅标准库);Swift 5.7+ / macOS 13+;ObjC 用 ARC。
ObjC 那份**只依赖 QuartzCore + CoreGraphics,不需要 UIKit**,所以 macOS 上也能编译运行;
`CALayer` 的布局与绘制在无窗口环境下依然可用,因此 `drawInContext:` 计数是真实数字。
**`main.swift` 里不能再写 `@main`**,同一模块内两者冲突。

## 6. 测量口径

模型只给形状,**不给系数**。真机测量用 Instruments:

* **Core Animation 模板**:`Color Offscreen-Rendered Yellow`(离屏)、
  `Color Blended Layers`(红=混合多,绿=基本不透明)、
  `Color Hits Green and Misses Red`(光栅化命中/失效)、`Flash Updated Regions`(哪里被重绘)
* **GPU Driver**:`Renderer Utilization` 高 = 填充率压力(混合/overdraw),
  `Tiler Utilization` 高 = 层数太多。两者都超过 50% 才值得动手
* **Time Profiler**:解决 commit 段的 Layout/Display 瓶颈
* 本模型里的帧预算、脏标记合并、离屏 pass 数量都是**结构性事实**;具体毫秒数请以实测为准

## 7. 常见坑

1. **只优化「画得快」,不管「画得少」。** 脏标记机制决定了收益的大头在"少标脏、少层数",
   而不是"每层画得更快"。
2. **给列表 cell 开 `shouldRasterize`。** 复用快 → 缓存立刻失效 → 净亏,还多占内存。
3. **圆角用 `cornerRadius + masksToBounds`。** 每个这样的 layer 一次离屏 pass。
   静态内容用预圆角图片或 `CAShapeLayer`。
4. **阴影不写 `shadowPath`。** 这是"最小改动最大收益"的一条:补一行就能去掉离屏。
5. **用 alpha=0 隐藏 view。** 它仍然参与合成与混合,应该用 `isHidden`。
6. **以为「主线程空闲」就不会掉帧。** 还有 render hitch:render server 来不及处理你的
   图层树照样掉帧,治法是减少层数、离屏与混合,而不是优化业务代码。
7. **在 120Hz 设备上沿用 60Hz 的预算。** 预算直接减半到 8.33ms,原来"刚刚够"的代码会开始掉帧。
8. **写断言时的自伤。** 本目录开发中出现过 2 次"实现对了、断言错了":
   `42 / 1.4 == 30` 的浮点比较(改成 `abs(cost - 30 * unit) < 1e-9`),
   以及一个"模型里没有对应量"的断言(把"标脏 1000 次"当成了模型可表达的输入)。
   **断言失败先怀疑断言。**

## 8. 参考资料

Apple 官方资料(核心结论均出自这里):

1. *Explore UI animation hitches and the render loop* — Apple Tech Talk。
   Render Loop 的分段(Event / Commit / render server / GPU / VSYNC)、
   「need layout 请求被合并」、父→子的 layout 顺序、commit hitch 与 render hitch 的区分、
   1 帧 = 16.67ms 与 2 帧 = 33.34ms。
   https://developer.apple.com/videos/play/tech-talks/10855/
2. *Core Animation Programming Guide · Layer Trees Reflect Different Aspects of the Animation
   State* — layer 树与 render tree、渲染在独立进程里做的原因、`drawInContext:` 与
   backing store 的关系。
   https://developer.apple.com/library/archive/documentation/Cocoa/Conceptual/CoreAnimation_guide/
3. *CALayer · Core Animation Programming Guide* — `needsDisplayOnBoundsChange`、
   `setNeedsDisplay` / `setNeedsLayout` 的脏标记语义、`shouldRasterize` 与
   `rasterizationScale`、`shadowPath` 的用途。
   https://developer.apple.com/documentation/quartzcore/calayer
4. *Optimizing App Startup Time (WWDC 2016 Session 406)* — 同一套 pipeline 在启动阶段的
   表现(与 `04-移动开发/01-iOS/启动优化/` 那篇互相印证)。
   https://developer.apple.com/videos/play/wwdc2016/406/

工程资料(用于交叉验证机制与调优口径):

5. *How iOS Renders Views & Animations and Boosts Performance* ——
   Layout / Create backing image / Prepare / Commit 四段划分、离屏渲染的常见触发条件、
   Instruments 各项开关(Color Offscreen-Rendered / Blended Layers / Hits & Misses)的读法。
   https://www.besthub.dev/articles/how-ios-renders-views-animations-and-boosts-performance-d2724edf91d5
6. *Mastering iOS Rendering Optimization* —— 16.67ms 预算的来历、
   `shadowPath` 与预圆角图片的具体写法、overdraw 的判定标准。
   https://swiftyn.com/learn/ios-concepts/mastering-ios-rendering-optimization
7. *CoreAnimation 渲染流程* —— Handle Events / Commit Transaction(Layout、Display、Prepare、
   Commit)/ Decode / Draw Calls / Render / Display 的逐步拆解,含「重写 `drawRect:` 会在 CPU 里
   直接把 layer 处理成 bitmap」这一离屏来源。
   https://cloud.tencent.com/developer/article/1858154
8. *iOS Core Animation: Advanced Techniques · Layer Performance Best Practices* ——
   层数管理、离屏渲染的替代方案(`CAShapeLayer`、预合成图片)、
   `shouldRasterize` 的使用边界与 `rasterizationScale`。
   https://deepwiki.com/qunten/iOS-Core-Animation-Advanced-Techniques/5.6-layer-performance-best-practices

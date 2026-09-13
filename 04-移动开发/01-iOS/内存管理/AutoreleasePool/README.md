# AutoreleasePool 自动释放池:AutoreleasePoolPage 双向链表

> 04-移动开发 / 01-iOS / 内存管理 / AutoreleasePool — demo 132(Swift / Objective-C)

## 简介

`@autoreleasepool {}` 是 Objective-C 的延迟释放机制:对象调用 `autorelease` 后引用计数**暂不 -1**,而是入栈登记,等池子 `pop` 时**逆序统一 release**。底层没有任何"池对象"结构——objc4 中它是 **以 `AutoreleasePoolPage` 为节点的双向链表**,每线程一份(TLS 指向 hot page),页内以**栈**存放对象指针,嵌套池靠**哨兵对象 POOL_BOUNDARY**(旧名 POOL_SENTINEL,即 `nil` 的别名)分隔。

## 原理详解

### clang 改写:`@autoreleasepool` 的真身

`clang -rewrite-objc` 后,`@autoreleasepool {}` 变成结构体 `__AtAutoreleasePool`:构造函数调 `objc_autoreleasePoolPush()`,析构函数调 `objc_autoreleasePoolPop(token)`。即一对 push/pop 包住作用域——**C++ RAII 而非运行时魔法**。

### AutoreleasePoolPage 结构(objc4 NSObject.mm)

```text
class AutoreleasePoolPage {
    magic_t const magic;          // 校验页完整性
    id *next;                     // 下一个可存放位置的指针(栈顶)
    pthread_t const thread;       // 所属线程
    AutoreleasePoolPage *parent;  // 父页(首页为 nil)
    AutoreleasePoolPage *child;   // 子页(尾页为 nil)
    uint32_t const depth;         // 页深度,首页 0
    uint32_t hiwat;               // high water mark 峰值标记
};
// SIZE = 4096 字节(PAGE_SIZE),头部 56B,其余 ~505 个槽位存对象指针
```

### push / autorelease / pop 三流程

1. **push** = `autoreleaseFast(POOL_BOUNDARY)`:往热页压入一个哨兵,**返回哨兵槽位地址作为 pool token**。
2. **autorelease** = `autoreleaseFast(obj)`:三分支——热页未满直接 `page->add(obj)` 压栈;已满走 `autoreleaseFullPage`(沿 child 找未满页,没有就 `new AutoreleasePoolPage(parent)` 挂链表);无热页走 `autoreleaseNoPage`(新建首页并先补一个哨兵防裸 pop)。
3. **pop(token)** = `page->releaseUntil(stop)`:从热页 `--next` 逐槽出栈,向每个对象发 `release`,直到 token 指向的哨兵槽位;跨页时沿 parent 回退,释放后空页按迟滞策略回收。

### 嵌套池

每个 `@autoreleasepool` 对应一个哨兵;**内层 pop 只释放内层哨兵之后入栈的对象**——token 就是边界地址。RunLoop 在主线程每个迭代 kCFRunLoopEntry 时 push、BeforeWaiting 时 pop,把上一轮迭代产生的临时对象按批次清掉。

### 关键行为速查

| 行为 | 机制 |
| --- | --- |
| 池与线程 | 一线程一份,TLS 存 hot page,跨线程不共享 |
| 对象释放顺序 | pop 逆序(LIFO),与入栈顺序相反 |
| 页满 | 新建 child page,`parent->child = this` 双向挂链 |
| 空页回收 | pop 后若页少于半满,杀掉多余空 child(迟滞,防抖动) |
| 大量循环产生临时对象 | 循环内嵌小 `@autoreleasepool` 防热页膨胀(经典图片解码场景) |
| Swift 桥接 | `autoreleasepool { }` 全局函数,内部同样是 push/pop |

## 环境

- Objective-C / Swift,macOS 或 iOS 工具链(仓库约定不实际运行,人工审查)
- 无第三方依赖

## 运行方式

```bash
# ObjC(仅供参考)
clang -framework Foundation objc/ObjCAutoreleasePool.m -o pool && ./pool
# Swift(仅供参考)
swift swift/SwiftAutoreleasePool.swift
```

## 关键代码

- `objc/ObjCAutoreleasePool.m`:复刻 `pageNew / pageAdd / autoreleaseFast 三分支 / poolPush / pageReleaseUntil / poolPop`,用 `AtAutoreleasePool` 结构体模拟 clang 改写;演示嵌套池(内层 pop 只释放内层)与页满分页(depth=0/1 两页)。
- `swift/SwiftAutoreleasePool.swift`:同构 Swift 版,`AtAutoreleasePool` 类以 `deinit` 表达析构 pop,`RetainBag` 模拟池持有的引用计数。

## 性能边界

- push/pop 与 autorelease 均为 **O(1)** 指针操作;pop 整体代价 = 池内对象数。
- 一页 4096B ≈ 505 槽位(64 位),真实负载下分页极少发生;**hot page 查询走 TLS 无锁**。
- `releaseUntil` 中 `-release` 可能再触发别的对象 autorelease(注释明确"Restart from hotPage() every time"),故实现每轮重取热页。

## 注意事项与常见坑

- **哨兵就是 nil**:`POOL_BOUNDARY` 传 nil 不代表"无对象",pop 靠槽位**地址**判断边界,不是值比较。
- **pop 可以传任意对象地址**(非哨兵)——objc4 允许 pop 到栈中任一位置,释放其上所有对象;`drain` 语义即 `pop(top)`。
- **ARC 下autorelease 没有消失**:编译器按调用约定(非持有返回值 NRC)自动插入,只是肉眼不可见。
- **不写 @autoreleasepool 会怎样**:非主线程无池时 autorelease 对象泄漏警告;主线程靠 RunLoop 迭代兜底,但长循环会堆积内存——**循环内手动开池**是正解。
- **Swift 里别把 autoreleasepool 当通用内存管理**:它只影响 ObjC 侧 autorelease 对象,Swift 值类型与普通强引用不受它管。

## 参考资料

- objc4 `NSObject.mm` — AutoreleasePoolPage / autoreleaseFast / releaseUntil 源码结构(经以下资料核对):
  - 面向信仰编程《自动释放池的前世今生——深入解析 autoreleasepool》 https://draven.co/autoreleasepool/
  - DeepWiki: objc4 Memory Management(AutoreleasePoolPage 结构与 POOL_BOUNDARY) https://deepwiki.com/apple-oss-distributions/objc4/2.3-memory-management
  - CSDN《Objective-C之Autorelease Pool底层实现原理记录》 https://blog.csdn.net/Deft_MKJing/article/details/82947706

# weak 弱引用实现:SideTables / weak_table / weak_clear_no_lock

> 04-移动开发 / 01-iOS / 内存管理 / Weak实现 — demo 133(Swift / Objective-C)

## 简介

`__weak` 指针不增加引用计数,且**所指对象释放时自动置 nil**(向 nil 发消息是 no-op,因而不会野指针崩溃)。这套语义不是编译器魔法,而是 objc runtime 用**全局 SideTables 哈希表体系**登记每一个 weak 变量地址,在对象 dealloc 时统一清零。Swift 的 `weak` 与 ObjC 共用同一套 runtime 设施。

## 原理详解

### 分层结构

```text
SideTables (StripedMap,全局)
 ├─ SideTable[0..7]            iOS 真机 8 张;macOS/模拟器 64 张
 │   ├─ slock      spinlock_t  每表一把自旋锁(缩小锁粒度)
 │   ├─ refcnts    RefcountMap 引用计数表(extra_rc 溢出时兜底)
 │   └─ weak_table weak_table_t
 │        └─ weak_entry_t[...] 按对象(referent)开地址哈希
 │             └─ referrers: weak 变量的地址数组(union:inline 4 槽 / out-of-line 哈希)
```

- **对象 → SideTable**:`indexForPointer = ((addr >> 4) ^ (addr >> 9)) % StripeCount`,多个对象可能共用一张表。
- **weak_referrer_t 本质是 `objc_object**`**——即"指向 weak 变量的指针",runtime 拿到它才能在释放时把变量**本身**写成 nil。

### 注册(weak 变量赋值)

`__weak WObject *w = obj;` → 编译器调 `objc_initWeak(&w, obj)` → `storeWeak<Dereference>(location, newObj)` → `weak_register_no_lock`:

1. 按对象地址定位 SideTable,`weak_entry_for_referent` 线性探测找 entry;
2. 找到 → `append_referrer` 追加(≤4 个走 inline_referrers,超出升级 out-of-line 开地址哈希并 rehash);
3. 没找到 → 新建 `weak_entry_t(referent, referrer)` 插入,必要时 `weak_grow_maybe` 扩容;
4. **全程持表锁,但不动引用计数**。对象若正在 dealloc(`SIDE_TABLE_DEALLOCATING`),注册直接 crash(不能 weak 一个垂死对象)。

### 注销(weak 变量改指向)

weak 变量被赋新值/nil 前,`objc_storeWeak` 先 `weak_unregister_no_lock` 从旧 entry 摘除自己的地址(尾部交换删除,entry 清空则整体移除)。

### 释放(dealloc → 全部置 nil)

```text
release 计数归零
→ _objc_rootDealloc → rootDealloc
   (非 weak/无关联对象/无 C++ 析构 → 快路径直接 free)
→ object_dispose → objc_destructInstance → clearDeallocating
→ sidetable_clearDeallocating → weak_clear_no_lock:
   取出 entry 的全部 referrers,逐个 *referrer = nil,再移除 entry
```

`objc_destructInstance` 同时处理**关联对象**释放,顺序在 weak 清零之后。

### 关键行为速查

| 行为 | 机制 |
| --- | --- |
| weak 不加引用计数 | storeWeak 只登记,不 retain |
| 释放自动置 nil | dealloc 管线里 weak_clear_no_lock 逐位写 nil |
| 多对象弱引用同一对象 | 同一 entry 的 referrers 数组(inline 4 → out-of-line) |
| 读 weak 变量 | `objc_loadWeakRetained`:临场 retain 再 autorelease,防读到一半被释放 |
| 对象 dealloc 中再 weak 它 | crash(注册时检查 DEALLOCATING 位) |
| StripedMap 分桶 | 8/64 张表 + 各自自旋锁,降低多线程争用 |

## 环境

- Objective-C / Swift,macOS 或 iOS 工具链(仓库约定不实际运行,人工审查)
- 无第三方依赖

## 运行方式

```bash
clang -framework Foundation objc/ObjCWeak.m -o weak && ./weak     # 仅供参考
swift swift/SwiftWeak.swift                                        # 仅供参考
```

## 关键代码

- `objc/ObjCWeak.m`:复刻 `indexForPointer` 分桶、`weakEntryForReferent` 线性探测、`weakRegister/weakUnregister`(append/交换删除)、`objRelease` 计数归零后 `weak_clear` 把 w1/w3 置 nil。
- `swift/SwiftWeak.swift`:同构 Swift 版,`UnsafeMutablePointer<WObject?>` 精确表达"weak 变量槽位地址";演示 slot2 改指向 B 时的 unregister→register 双步。

## 性能边界

- 注册/注销/查询均 **O(1)**(开地址哈希,mask 位与代替取模);inline 4 槽内零哈希开销。
- dealloc 时清零代价 = 该对象 weak 引用数;`objc_loadWeakRetained` 每次**临时 retain+autorelease**,高频读 weak 有 autorelease 池压力。
- SideTable 是全局静态的,`ExplicitInit` 惰性初始化,进程首个 weak 出现前零开销。

## 注意事项与常见坑

- **weak 变量本身是二级指针登记**:runtime 存的是"变量地址"而非对象副本——这就是为什么置 nil 能作用到你的局部变量上。
- **对象正在 dealloc 时注册 weak 会直接 crash**,不是返回 nil。
- **Swift 的 `unowned` 不走这套表**:它是裸指针 + 对象头检查,对象释放后访问 `unowned` 直接 trap(fatal error),不会置 nil。
- **Tagged Pointer 没有 weak 语义**(不进 SideTable,也不参与引用计数)。
- **weak 不能打断循环引用里的"环"本身**:它只是让一方不持有,环的另一半必须由 strong/weak 设计决策解开。

## 参考资料

- objc4 `NSObject.mm`(storeWeak / weak_register_no_lock / weak_clear_no_lock)与 `objc-weak.mm`,经以下资料核对:
  - BestHub《Understanding the Implementation of weak Pointers in the Objective-C Runtime》 https://www.besthub.dev/articles/understanding-the-implementation-of-weak-pointers-in-the-objective-c-runtime-c854b3020d7e
  - DeepWiki: objc4 Memory Management(weak_table_t / SideTable / dealloc 管线) https://deepwiki.com/apple-oss-distributions/objc4/2.3-memory-management
  - CSDN《iOS-weak 底层原理》 https://blog.csdn.net/weixin_61639290/article/details/131920032

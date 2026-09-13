# iOS · 内存管理

研究 iOS / macOS 平台下的 Swift 与 Objective-C 内存管理机制,核心是 **ARC(Automatic Reference Counting)** 及其弱/无主引用变体。

## 子领域

| 子目录 | 知识点 |
| --- | --- |
| [ARC/](./ARC/) | Swift / Objective-C ARC(strong / weak / unowned + 闭包捕获列表) |
| [AutoreleasePool/](./AutoreleasePool/) | AutoreleasePoolPage 双向链表 + 哨兵嵌套池 + 分页 |
| [Weak实现/](./Weak实现/) | SideTables / weak_table / weak_clear_no_lock 自动置 nil |

## 已完成 demo

| ID | 路径 | 知识点 | 语言 |
| --- | --- | --- | --- |
| 024 | `ARC/` | ARC(strong / weak / unowned + 闭包捕获列表) | Swift / Objective-C |
| 132 | `AutoreleasePool/` | AutoreleasePoolPage 双向链表:clang 改写 __AtAutoreleasePool、POOL_BOUNDARY 哨兵嵌套、页满扩 child page、pop 逆序 release(objc4 NSObject.mm) | Swift / Objective-C |
| 133 | `Weak实现/` | weak 底层:StripedMap 分桶 SideTable、weak_entry inline 4 槽→out-of-line、dealloc 管线 weak_clear_no_lock 全部置 nil(objc4 objc-weak 体系) | Swift / Objective-C |

## 待研究

- [ ] 值类型(struct / enum)与引用类型的 ARC 边界
- [ ] ARC 优化(use-based lifetime vs observed lifetime,WWDC 2021 #10216)
- [ ] 桥接与 unowned(unsafe) 的悬挂指针风险
- [ ] Memory Graph Debugger 实战排错
- [ ] Tagged Pointer 与引用计数的 exemption
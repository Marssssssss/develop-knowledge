# iOS · 运行时

研究 Objective-C Runtime 机制:消息发送、isa 体系、KVO 动态子类、Category 加载与关联对象、类布局与脏内存分离。

## 子领域

| 子目录 | 知识点 |
| --- | --- |
| [消息机制/](./消息机制/) | objc_msgSend 快速/慢速查找 + 动态解析与三段转发 |
| [KVO/](./KVO/) | isa-swizzling / NSKVONotifying_ 派生子类 / setter 重写 |
| [Category与关联对象/](./Category与关联对象/) | attachCategories 前插合并 + AssociationsManager 两层哈希 |
| [类布局与脏内存/](./类布局与脏内存/) | class_data_bits_t 的 FAST 位域 + class_rw_t / class_rw_ext_t 与 extAlloc |

## 已完成 demo

| ID | 路径 | 知识点 | 语言 |
| --- | --- | --- | --- |
| 134 | `消息机制/` | objc_msgSend 三段式:cache_t 哈希缓存(3/4 翻倍清空)→ lookUpImpOrForward 继承链二分 → resolve/forwardingTarget/forwardInvocation 三段兜底(objc4 源码) | Swift / Objective-C |
| 135 | `KVO/` | KVO isa-swizzling:objc_allocateClassPair 派生 NSKVONotifying_ + setter 重写 will/did + -class 伪装 + _isKVOA(Apple KVO Guide) | Swift / Objective-C |
| 136 | `Category与关联对象/` | category_t → attachCategories 倒序前插(后编译者胜)+ 关联对象两层哈希 DISGUISE→key→{policy,value}(objc4 objc-references.mm) | Swift / Objective-C |
| 684 | `类布局与脏内存/` | class_data_bits_t 把 class_ro_t*/class_rw_t* 与 3 个 FAST 标志塞进同一个字(真机 `FAST_DATA_MASK=0x0f00007ffffffff8` 比非真机少 bit39–46);`has_rw_pointer` 在 32 位退化为 `flags & RW_REALIZED`;`setData` 只留旧字的低 3 位;`flags(bits)` 用 strip 故意不验签;ro_or_rw_ext 是最低位标签的 PointerUnion;`extAlloc` 的 `version` 元类为 7;深拷贝**只对方法生效**且逐个 attachLists 会把顺序反转,属性与协议从不深拷贝 | Python / Go |

## 待研究

- [ ] Runtime Method Swizzling 与 method_exchangeImplementations 安全姿势
- [x] class_ro_t / class_rw_t / clean memory 与 dirty memory → demo 684
- [ ] 非指针 isa(nonpointer isa)位域布局
- [ ] objc_class 结构演进(objective-c 1.0 → 2.0 ABI)
- [ ] Swift Metadata 与 ObjC runtime 互操作(@_objcRuntimeName / dynamic)

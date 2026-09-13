# iOS · 运行时

研究 Objective-C Runtime 机制:消息发送、isa 体系、KVO 动态子类、Category 加载与关联对象。

## 子领域

| 子目录 | 知识点 |
| --- | --- |
| [消息机制/](./消息机制/) | objc_msgSend 快速/慢速查找 + 动态解析与三段转发 |
| [KVO/](./KVO/) | isa-swizzling / NSKVONotifying_ 派生子类 / setter 重写 |
| [Category与关联对象/](./Category与关联对象/) | attachCategories 前插合并 + AssociationsManager 两层哈希 |

## 已完成 demo

| ID | 路径 | 知识点 | 语言 |
| --- | --- | --- | --- |
| 134 | `消息机制/` | objc_msgSend 三段式:cache_t 哈希缓存(3/4 翻倍清空)→ lookUpImpOrForward 继承链二分 → resolve/forwardingTarget/forwardInvocation 三段兜底(objc4 源码) | Swift / Objective-C |
| 135 | `KVO/` | KVO isa-swizzling:objc_allocateClassPair 派生 NSKVONotifying_ + setter 重写 will/did + -class 伪装 + _isKVOA(Apple KVO Guide) | Swift / Objective-C |
| 136 | `Category与关联对象/` | category_t → attachCategories 倒序前插(后编译者胜)+ 关联对象两层哈希 DISGUISE→key→{policy,value}(objc4 objc-references.mm) | Swift / Objective-C |

## 待研究

- [ ] Runtime Method Swizzling 与 method_exchangeImplementations 安全姿势
- [ ] class_ro_t / class_rw_t / clean memory 与 dirty memory
- [ ] 非指针 isa(nonpointer isa)位域布局
- [ ] objc_class 结构演进(objective-c 1.0 → 2.0 ABI)
- [ ] Swift Metadata 与 ObjC runtime 互操作(@_objcRuntimeName / dynamic)

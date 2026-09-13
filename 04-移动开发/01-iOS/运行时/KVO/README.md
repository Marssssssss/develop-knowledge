# KVO 键值观察:isa-swizzling 动态子类机制

> 04-移动开发 / 01-iOS / 运行时 / KVO — demo 135(Swift / Objective-C)

## 简介

KVO(Key-Value Observing)是 Cocoa 对观察者模式的实现:`addObserver:forKeyPath:options:context:` 后,被观察属性变更会自动回调观察者的 `observeValueForKeyPath:ofObject:change:context:`。其底层是 **isa-swizzling**——注册观察时,runtime 动态派生一个 `NSKVONotifying_<原类名>` 子类,重写被观察属性的 setter,并把**对象的 isa 指向派生类**;赋值走派生 setter,插入 `willChangeValueForKey:` → 原 setter → `didChangeValueForKey:` → 通知观察者。

## 原理详解

### addObserver 时 runtime 做的五件事

1. **动态派生子类**:`objc_allocateClassPair(A, "NSKVONotifying_A")` + `objc_registerClassPair`——若已存在(同类其他对象被观察过)则直接复用缓存;
2. **isa 调包**:`object_setClass(obj, 派生类)`——对象从此是派生类实例,消息查找优先命中派生方法;
3. **重写被观察属性 setter**:内部调 `_NSSetIntValueAndNotify` 等类型专属函数,执行 `willChangeValueForKey:` → `[super setter:]` → `didChangeValueForKey:`;
4. **重写 `-class`**:返回**原类**——让你在调试时以为 isa 没变(`object_getClass` 才能看到真相);
5. **重写 `-_isKVOA`**:返回 YES,私有标志位,供 runtime 内部快速判定。

### 通知链

```text
obj.age = 20
→ NSKVONotifying_Person setAge:(派生 setter)
   → willChangeValueForKey:@"age"        ← 通知系统"即将变化",收集 oldValue
   → [super setAge:20]                   ← 原类真正赋值
   → didChangeValueForKey:@"age"         ← 内部同步回调所有观察者
        → observeValueForKeyPath:ofObject:change:context:
```

`change` 字典的 `NSKeyValueChangeKindKey`(setting=1 / insertion=2 / removal=3 / replacement=4)与 `NSKeyValueChangeNewKey` / `OldKey` 由注册时的 `options` 决定收集哪些。

### removeObserver 与生命周期

移除观察后 **isa 复原为原类**(派生类**不销毁**,留在缓存供下次复用)。两个经典坑:

- **重复移除/未移除**:observer 比 observed 先释放 → 向野指针发回调崩溃;重复 remove → `NSRangeException`。iOS 11+ 可用 block API `observe(_:options:changeHandler:)` 返回的 token 自动管理;
- **父类私有观察**:context 传 NULL 且父类也 observe 同一 keyPath 时无法区分归属——Apple 文档建议传唯一 context 并在回调里优先判断。

### 手动通知与依赖键

- `automaticallyNotifiesObserversForKey:` 返回 NO 关闭自动 setter 通知,自己配对调 will/did(属性由多 ivar 派生时必须手动);
- `keyPathsForValuesAffecting<Key>` / `+keyPathsForValuesAffectingValueForKey:` 声明依赖键:fullName 依赖 firstName+lastName,后两者变化自动触发前者观察者。

## 环境

- Objective-C(真实 runtime API)/ Swift(语义复刻),macOS 或 iOS 工具链(仓库约定不实际运行,人工审查)

## 运行方式

```bash
clang -framework Foundation objc/ObjCKVO.m -o kvo && ./kvo    # 仅供参考
swift swift/SwiftKVO.swift                                     # 仅供参考
```

## 关键代码

- `objc/ObjCKVO.m`:**用真实 runtime API 复刻**——`objc_allocateClassPair` 派生 `NSKVONotifying_Person`、`class_addMethod` 重写 `setAge:`(will → `objc_msgSendSuper` 调父类 → did)、`object_setClass` 调包与复原、`-class` 伪装与 `_isKVOA` 标志。
- `swift/SwiftKVO.swift`:Swift 无法运行时派生类,以"可热替换方法表"表达 isa——派生表继承原表、重写 `setAge:` 槽位、`displayName` 伪装原类名、派生表缓存复用(与真实 KVO 一致,移除后不销毁)。

## 性能边界

- 首次 addObserver:派生类构建 **一次性开销**(同类后续对象直接复用);
- 被观察后的 setter 调用:多一层派生方法查找 + will/did 两次字典操作 + 同步回调——比裸 setter 慢一个数量级,**高频写入属性慎上 KVO**;
- 通知是**同步**的:didChange 在赋值线程内联回调,观察者重活需自行 dispatch。

## 注意事项与常见坑

- `[obj class]` 会被派生类**重写欺骗**,验证 isa 调包要用 `object_getClass(obj)` 或 LLDB。
- **直接改 ivar 不触发 KVO**(`obj->_age = 20` 绕过 setter);KVC `setValue:forKey:` 会触发。
- **Swift 原生属性不能被 KVO**:只有 `@objc dynamic` 继承 NSObject 的属性才行;Swift 值类型用 `@Observable` 宏 / Combine 的 `ObservableObject` 是正统替代。
- 手动 will/did **必须配对**,只调 will 不调 did 会导致后续观察全部失效。
- 依赖键的观察者收到的是"被依赖键变化"的通知,change 字典内容取决于被依赖键的注册 options。

## 参考资料

- Apple KVO Programming Guide 语义 + objc4 `NSObject.mm` observe 机制,经以下资料核对:
  - 稀土掘金《KVO 添加 observer 时,Runtime 会做哪些事情?》(五步分解) https://juejin.cn/post/7605131777175650367
  - CSDN《iOS 开发中的 KVC 和 KVO》(isa-swizzling 与 -class 伪装验证) https://blog.csdn.net/tugele/article/details/79663795
  - CSDN《iOS-KVO 底层原理——利用 Runtime 自定义 KVO》(重写 setter/dealloc/_isKVOA 清单) https://blog.csdn.net/weixin_39742727/article/details/111903192

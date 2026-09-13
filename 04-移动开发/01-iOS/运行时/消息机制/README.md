# objc_msgSend 消息机制:快速查找 / 慢速查找 / 动态解析与三段转发

> 04-移动开发 / 01-iOS / 运行时 / 消息机制 — demo 134(Swift / Objective-C)

## 简介

`[obj foo]` 在编译期被改写为 `objc_msgSend(obj, @selector(foo))`。ObjC 方法调用不是静态绑定,而是**运行时按 SEL 找 IMP(函数指针)再跳转**的流水线:**汇编快速路径查 cache_t 哈希缓存 → C++ 慢速路径 lookUpImpOrForward 沿继承链查方法列表 → 都失败进入动态方法解析 → 快速转发 → 完整转发 → doesNotRecognizeSelector 抛异常**。整个设计目标是让"绝大多数消息"只走前两步。

## 原理详解

### 阶段一:汇编快速路径(CacheLookup)

`objc_msgSend` 用**汇编**实现(arm64: `objc-msg-arm64.s`),原因:极高频调用必须零函数调用开销、直接控制寄存器、保证调用约定稳定。流程:

1. 检查 receiver 是否 nil / Tagged Pointer(nil 直接返回 0,所以向 nil 发消息是 no-op;Tagged Pointer 从全局表取类);
2. 从对象首 8 字节取 isa,掩码还原出类对象;
3. 在类的 **cache_t** 中查找:`index = hash(sel) & mask`(位与代替取模),开放寻址线性探测,环形回绕;
4. 命中 → 直接 `jmp *imp`,全程纳秒级,**无锁**(并发冲刷靠 restartable ranges 机制)。

**cache_t 扩容策略**:容量恒为 2 的幂(arm64 初始 4),占用超 **3/4** 直接**翻倍并清空**所有旧缓存(不 rehash)——方法调用时间局部性极强,旧缓存大概率不再需要。

### 阶段二:慢速路径(lookUpImpOrForward)

缓存未命中(`_objc_msgSend_uncached` → `__class_lookupMethodAndLoadCache3` → `lookUpImpOrForward`,持 runtimeLock):

1. 本类 `class_rw_t` 方法列表查找——**方法列表在类 realize 时已按 SEL 排序,用二分查找 O(log n)**;
2. 未找到 → `superclass` 上行,**每级先查父类缓存再查父类方法列表**,直到根类;
3. 命中后**回填最初接收消息的类的缓存**(不是找到方法的那个父类),下次走快速路径;
4. 继承链查尽仍无 → IMP = `_objc_msgForward_impcache`,进入兜底。

### 阶段三:动态解析与消息转发(三段兜底)

```text
慢速路径未命中
→ ① resolveInstanceMethod: / resolveClassMethod:   ← class_addMethod 动态补 IMP,返回后 retry 一轮查找
→ ② forwardingTargetForSelector:                   ← 返回备用接收者,整条消息转发(零签名开销)
→ ③ methodSignatureForSelector: + forwardInvocation: ← 构造 NSInvocation,可改目标/改参数/多发
→ doesNotRecognizeSelector:                        ← 抛 NSInvalidArgumentException 崩溃
```

- ① 是"把方法补在**本类**上";② 是"换人干";③ 是"拆开信封重新寄"——`@dynamic` 属性、Core Data 动态存取、多重代理(MulticastDelegate)分别落在这三段。
- 元类走 `resolveClassMethod` 后还会再试一次 `resolveInstanceMethod`(类对象本身也是实例)。

### SEL / IMP / isa 三要素

- **SEL**:方法名映射的运行时常量指针,全局唯一,同名同参 SEL 相同(故 ObjC 无函数重载);
- **IMP**:函数指针 `id (*IMP)(id, SEL, ...)`;
- **isa 链**:实例方法存类,类方法存元类,根元类 isa 指向自身。

## 环境

- Objective-C / Swift,macOS 或 iOS 工具链(仓库约定不实际运行,人工审查)

## 运行方式

```bash
clang -framework Foundation objc/ObjCMsgSend.m -o msg && ./msg   # 仅供参考
swift swift/SwiftMsgSend.swift                                    # 仅供参考
```

## 关键代码

- `objc/ObjCMsgSend.m`:`cacheInit/cacheInsert(3/4 翻倍清空)/cacheGet(位与+回绕探测)`、`methodListFind` 二分、`lookUpImpOrForward` 继承链上行 + 缓存回填、`Receiver` 真实实现 `resolveInstanceMethod` 与 `forwardingTargetForSelector` 两钩子。
- `swift/SwiftMsgSend.swift`:同构 Swift 版;`MessagingHooks` 协议表达 NSObject 三个可重写钩子,演示 hello 缓存命中、dynamic 动态解析、fetch 快速转发、unknown 走到完整转发。

## 性能边界

- 缓存命中:**约几纳秒**,2 次内存读 + 一次间接跳转,比 C++ 虚表慢但同数量级;
- 慢速路径:O(log n) 二分 × 继承链深度,且持锁——**首次调用某一消息的开销**是它的,这也是为何热路径方法第一调用后稳定走缓存;
- 消息转发三段**只发生在"方法不存在"时**,正常业务不应依赖;用它做 AOP/多重代理需自担 CPU 成本(每条消息一次完整查找失败)。

## 注意事项与常见坑

- **缓存回填在接收类,不是实现类**——否则子类调用父类方法永远缓存 miss。
- **同名 SEL 全局唯一**:`@selector(foo:)` 与任何类的 `foo:` 是同一个 SEL,method swizzling 才能跨类生效。
- **`resolveInstanceMethod` 返回 YES 后 runtime 会 retry 查找**:不真正 addMethod 只返回 YES 会死循环一轮后继续走 ②③。
- **`forwardingTargetForSelector` 返回的对象自身必须有该方法**,否则递归进入它的三段兜底。
- **Swift 默认不走消息机制**:直接派发(值类型/extension 非协议方法)、虚表派发(类继承)、消息派发(仅 `@objc dynamic` 或继承 ObjC 的 override)三种并存——`dynamic` 修饰符就是为 swizzling 强制消息派发而生。
- 汇编快速路径**无锁**,依赖内核支持的 restartable range 处理缓存并发冲刷,普通业务代码永远不该直接改 cache_t。

## 参考资料

- objc4 `objc-msg-arm64.s`(CacheLookup)、`objc-runtime-new.mm`(lookUpImpOrForward / resolveMethod_locked),经以下资料核对:
  - DeepWiki: objc4 Message Dispatch(汇编快速路径/慢速路径/转发全流程) https://deepwiki.com/apple-oss-distributions/objc4/2.2-message-dispatch
  - CSDN《iOS——消息传递底层实现》(objc_msgSend 汇编逐行注释) https://blog.csdn.net/m0_73974920/article/details/140550237
  - Laucp's Blog《深入了解 Objective-C 消息发送与转发过程》(lookUpImpOrForward 源码级) https://chipengliu.github.io/2019/06/02/objc-msgSend-forward/
  - 稀土掘金·字节 YouthCamp《iOS 深度解析》(cache_t 扩容 3/4 策略、二分查找细节) https://youthcamp.bytedance.com/post/7612314005001502760

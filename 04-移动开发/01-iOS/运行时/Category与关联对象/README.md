# Category 分类与关联对象:attachCategories 前插合并 + AssociationsManager 两层哈希

> 04-移动开发 / 01-iOS / 运行时 / Category与关联对象 — demo 136(Swift / Objective-C)

## 简介

Category(分类)在**运行时**把方法合并进宿主类,为已有类动态添加行为——但**不能添加实例变量**(对象内存布局编译期已定,运行期插入 ivar 会破坏布局)。关联对象(Associated Objects)补上这块短板:把任意键值**挂在 runtime 全局哈希表**里而非对象内存里,模拟"给分类加属性"。

## 原理详解

### Category 的编译产物与加载

`clang -rewrite-objc` 后,分类变成 `category_t` 结构,放进 Mach-O 的 `__DATA, __objc_catlist` section:

```text
struct category_t {
    const char *clsName;              // 宿主类名
    classref_t cls;
    method_list_t *instanceMethods;   // 实例方法
    method_list_t *classMethods;      // 类方法
    protocol_list_t *protocols;
    property_list_t *instanceProperties;  // 只有属性声明,没有 ivar 与 setter 实现
};
```

加载链路:`dyld` → `_objc_init` 注册回调 → `map_images` → `_read_images` 收集所有 category_t → `load_categories_nolock` → **`attachCategories`**:把分类的方法列表**前插**到宿主类(及元类)方法列表最前面(memmove + memcpy 拼接)。因此:

- 分类方法与原方法**同名 → 分类生效**(查找从前往后第一个命中);这本质是"遮蔽"不是 override,`[super method]` 拿到的仍是原实现;
- **多个分类同名方法 → 后编译者胜**(attach 倒序遍历,后编译的插得越靠前);
- 分类属性只生成 getter/setter **声明**,没有实现与 ivar——必须用关联对象补实现。

### +load 与 +initialize

| | +load | +initialize |
| --- | --- | --- |
| 时机 | map_images 阶段,runtime 加载类/分类时 | 类**首次收到消息**时(lookUpImpOrForward 里 lazy 触发) |
| 次数 | 每类/每分类各一次 | 每类一次(子类未实现会继承执行) |
| 顺序 | 父类 → 本类 → 分类(同类按编译序) | 父类 → 子类 |
| 是否需手动调 super | 不需要 | 不需要 |

### 关联对象:两层哈希

```text
AssociationsManager(全局单例,持锁)
 └─ AssociationsHashMap                          一级:DISGUISE(object 指针) → 二级表
     └─ ObjectAssociationMap                     二级:key(静态地址/@selector) → 条目
         └─ ObjcAssociation { policy, value }
```

- **DISGUISE**:`DisguisedPtr` 把指针**取负**存为整数,骗过内存泄漏检测工具;
- **objc_setAssociatedObject(obj, key, value, policy)**:二级表已有该 key → 覆盖(旧值在**锁外** release);没有 → 新建二级表插入,并给对象类打 `hasInstancesHaveAssociatedObjects` 标记;**value 传 nil 即移除**该 key;
- **dealloc**:对象释放走 `objc_destructInstance` → `_object_set_associative_reference` 全清,RETAIN/COPY 策略的值在此 release——**关联值生命周期跟随对象**;
- policy 五档:ASSIGN / RETAIN_NONATOMIC / RETAIN / COPY_NONATOMIC / COPY(atomic 档对应属性 atomic 语义)。

## 环境

- Objective-C / Swift,macOS 或 iOS 工具链(仓库约定不实际运行,人工审查)

## 运行方式

```bash
clang -framework Foundation objc/ObjCCategory.m -o cat && ./cat   # 仅供参考
swift swift/SwiftCategory.swift                                    # 仅供参考
```

## 关键代码

- `objc/ObjCCategory.m`:`MiniCategory` 复刻 category_t;`attachCategories` 倒序遍历 + `memmove` 前插;`methodLookup` 演示"后编译 CatB 的 description 获胜";`miniSet/Get/RemoveAssociatedObject` 复刻两层哈希 + DISGUISE + nil 移除 + dealloc 全清。
- `swift/SwiftCategory.swift`:同构 Swift 版;`MethodList.attach` 前插、`AssociationsManager` 以 `~a &+ 1` 表达 DisguisedPtr 取负语义。

## 性能边界

- 分类方法**合并发生在启动期一次性完成**,此后消息查找与普通方法完全同价(O(log n) 二分 + 缓存),零运行时额外开销;
- 但**大量分类会拉长启动**(map_images 阶段串行合并);
- 关联对象读写:**两次哈希查找 + 全局锁**——比真 ivar 慢百倍,只适合低频元数据(分类属性、delegate 桥接),不要塞热路径状态;
- 关联值 release 在**锁外**执行(旧值先记下、解锁后释放),避免锁内触发任意 dealloc 死锁。

## 注意事项与常见坑

- **分类"覆盖"原方法没有编译警告**,同名方法静默遮蔽——命名加前缀是铁律;
- 同名分类方法**执行顺序由编译顺序(链接顺序)决定**,不同 Xcode 配置可能不同——不要依赖;
- **分类不能加 ivar**,也不能 override;需要存储 + override 时该用子类;
- 关联对象 key 用**静态变量地址**或 `@selector()` 保证唯一,`&_key` 与 `_key` 写错会取到不同地址导致 get 永远 nil;
- `objc_removeAssociatedObjects` 会**清掉包括系统在内的全部关联**(UIKit 内部也用它),业务代码几乎永远应该用 `setAssociatedObject(..., nil, ...)` 精确移除;
- **Swift extension ≠ Category 完全等价**:extension 不能有存储属性、不能 override、同名方法冲突直接编译错误(比 ObjC 安全),但 `@objc` extension 编译后同样是 category_t。

## 参考资料

- objc4 `objc-runtime-new.mm`(attachCategories / +load)、`objc-references.mm`(_object_set_associative_reference),经以下资料核对:
  - 美团技术团队《深入理解 Objective-C:Category》(category_t 编译产物与加载全流程) https://tech.meituan.com/2015/03/03/diveintocategory.html
  - DeepWiki: Objective-C Runtime(Categories 加载与 Associated Objects 两层哈希) https://deepwiki.com/apple-oss-distributions/distribution-macOS/7.1-objective-c-runtime
  - CSDN《OC 语言基础特性》(attachCategories 倒序前插 + 关联对象二级结构) https://blog.csdn.net/wjm041006/article/details/161357861

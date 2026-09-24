# class-dump 与 Objective-C 运行时结构还原

> 砸壳拿到解密 Mach-O 之后,头文件还原(class-dump)走的是**纯静态**路径:
> 在 `__DATA_CONST` 段里找 `__objc_classlist`,顺着指针把 class_ro_t / method_list_t /
> ivar_list_t / property_list_t 逐字段读回来。本 demo 按 class-dump 源码
> (CDObjectiveC2Processor.m)的**读取顺序**构造假镜像并还原,结构与 objc4 运行时头文件对照。

## 1. class-dump 的三条入口

```text
__objc_protolist → protocolAtAddress:isa→name→protocols→实例方法→类方法
__objc_classlist → loadClassAtAddress(每 8 字节一个类指针)
__objc_catlist   → loadCategoryAtAddress
```

## 2. objc_class 与 data 指针的低位标志(64 位)

读取序:`isa | superclass | cache | vtable | data | r1 | r2 | r3`,其中 data 字段:

```c
uint64_t value = readPtr();
isSwiftClass   = (value & 0x1) != 0;   // bit0:Swift 类桥接
data           = value & ~7;           // 低 3 位全是标志,掩掉才是 ro 地址
```

ro(read-only,镜像内)指针指向 `class_ro_t`;运行时 realize 后才换成堆上的 `class_rw_t`
(可变、能挂分类方法)——静态 dump 只看 ro。

## 3. class_ro_t 字段序(64 位,72 字节)

| 偏移 | 字段 |
| --- | --- |
| 0/4/8/12 | flags / instanceStart / instanceSize / reserved |
| 16..64 | ivarLayout / **name** / **baseMethodList** / baseProtocols / **ivars** / weakIvarLayout / baseProperties(7 指针) |

**flags 的 bit0 = RO_META**:`#define RO_META (1<<0)`(objc-runtime-new.h:359)。
类与其元类同名("Person"),静态还原靠这个 flag 区分——
`isa` 指向元类、元类的 ro 带 RO_META,这就是 isa 链的终点判定。

## 4. 方法/成员变量/属性表

- `method_list_t` 头:`entsize | count`;**entsize 低 2 位是 fixup 标记,解析必须 `& ~3`**;
  经典 `method_t = {SEL name; const char *types; IMP imp}` 三指针,entsize=24;
  (新 ABI 用相对偏移压到 12 字节,本 demo 按经典布局,与所读 objc4/class-dump 版本一致)
- `ivar_t`:`offset* | name | type | alignment_raw | size`;
  **对齐特例**:`alignment_raw == ~(uint32_t)0` 时取 `1<<WORD_SHIFT`(64 位=8),
  否则 `1<<alignment_raw`;
- `property_t = {name, attributes}` 两指针;属性编码如 `Ti,V_age`(类型 i,背后 ivar _age)。

## 5. category 八指针序

`name → class → instanceMethods → classMethods → protocols → instanceProperties → v7 → v8`
——方法在运行时才并进 class_rw_t,静态视角永远挂在 category 上。

## 6. 动态侧对照

Frida 里 `ObjC.classes` 是**已 realize** 的视图(rw):能直接调方法、看分类;
class-dump 是 ro 视图:能看到 imp 原始偏移,配合 IDA 定位函数。
两者字段语义同源,本 demo 的结构常量两边通用。

## 自检

`python selfcheck_objcdump.py` —— 9 项断言:classlist 顺序 / data 低位标志(bit0=Swift,
实测藏 0b101)/ ro 字段序 / RO_META 元类识别 / entsize `&~3` 掩码 / ivar 对齐 ~0 特例 /
属性表 / catlist 与 protolist 字段序。Go 侧 `objcdump.go` 为同语义复刻(静态审查)。

## 参考资料(实读)

- [class-dump 源码 CDObjectiveC2Processor.m — nygard/class-dump](https://github.com/nygard/class-dump/blob/master/Source/CDObjectiveC2Processor.m)(loadClasses/loadCategories/loadProtocols 与全部读取序)
- [objc4 runtime/objc-runtime-new.h — opensource-apple/objc4](https://github.com/opensource-apple/objc4/blob/master/runtime/objc-runtime-new.h)(method_t/class_ro_t/class_rw_t/ivar_t/RO_META/FAST_DATA_MASK)
- 本目录 [iOS砸壳与加密镜像/](../iOS砸壳与加密镜像/)(前置:拿到解密镜像)

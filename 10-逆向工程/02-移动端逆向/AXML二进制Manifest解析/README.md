# AXML:Android 二进制 Manifest 解析

> APK 里的 `AndroidManifest.xml` 不是文本 XML,而是 **AXML**:一棵 chunk 链。
> 元素名、属性名、字符串值全部走**共享字符串池**,属性值是 **typed value**。
> 逆向里改 Manifest(去签名校验、放开 debuggable、改组件导出)必须先看懂这层二进制。

## 1. chunk 骨架(ResourceTypes.h 常量)

```text
ResChunk_header = type(u16) + headerSize(u16) + size(u32)   // size = 完整跳距
顶层 0x0003 RES_XML_TYPE
 ├─ 0x0001 RES_STRING_POOL_TYPE     字符串池
 ├─ 0x0100/0x0101 START/END_NAMESPACE   prefix/uri 两个池索引
 ├─ 0x0102 RES_XML_START_ELEMENT    headerSize=36
 └─ 0x0180 RES_XML_RESOURCE_MAP     属性名 → 资源 ID 映射
```

**size 字段就是跳距**:未知/损坏 chunk 可以安全越过——这是解析器的容错底线。

## 2. 字符串池:双编码

28 字节池头:`stringCount | styleCount | flags | stringsStart | stylesStart`。

- **UTF-16**(默认):`u16 长度 + UTF-16LE + NUL(u16)`;长度超 0x7FFF 时高位置 1、
  再跟一个 u16 拼接;
- **UTF-8**(`UTF8_FLAG = 1<<8`):`u16 字符长 + u8 字节长 + UTF-8 + NUL`;
- `SORTED_FLAG = 1<<0` 独立声明池是否按值排序;
- **池索引数组的值是相对 stringsStart 的偏移**(不是相对 chunk 头)——高频踩坑点。

## 3. START_ELEMENT(0x0102)

```text
node 头:chunk(8) + lineNumber(4) + comment(4)
attrExt:ns | name | attributeStart(=20) | attributeSize(=20) | attributeCount
        | idIndex | classIndex | styleIndex        ← 三索引 1-based,0=无
属性数组:每项 20 字节 = ns | name | rawValue | Res_value{size=8,res0=0,dataType,data}
```

- `idIndex/classIndex/styleIndex` 是**1-based 属性序号**:框架解析器据此免扫全表
  直取 `android:id`/`class`/`style`;
- 属性**没有文档顺序语义**,只有 attrExt 里的序号。

## 4. typed value:编译后的属性值

| dataType | 值 | 例 |
| --- | --- | --- |
| 0x03 STRING | data=池索引 | `package="com.demo.app"`(rawValue 通常也在) |
| 0x10 INT_DEC | 数值 | `versionCode=2` |
| 0x12 INT_BOOLEAN | 非 0 即真 | `debuggable=0xFFFFFFFF` |
| 0x01 REFERENCE | 资源 ID | `@0x7F0A0001`,**rawValue 缺失**(编译期就不再是字符串) |

改字符串值时若长度变化,记得同步:池头 stringsStart、后续 chunk 偏移、
顶层 size——AXML 没有重定位表,任何一处失配整棵树错位。

## 自检

`python selfcheck_axml.py` —— 8 项断言:chunk 常量与三段头 / UTF-16 与 UTF-8
双池解码 / stringsStart 相对偏移 / 命名空间 / typed value 四类分派 / rawValue 缺失 /
1-based 三索引 / size 跳距容错。构造与解析共用同一套常量,字节级对拍。
Go 侧 `axml.go` 为同语义复刻(静态审查)。

## 参考资料(实读)

- [ResourceTypes.h(chunk 常量/池头/attrExt/Res_value 枚举)— AOSP frameworks/base](https://github.com/LineageOS/android_frameworks_base/blob/lineage-19.1/libs/androidfw/include/androidfw/ResourceTypes.h)(AOSP 同源镜像)
- 本目录 [APK签名校验/](../APK签名校验/)(APK 结构前置)

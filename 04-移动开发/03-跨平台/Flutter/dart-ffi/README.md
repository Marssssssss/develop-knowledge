# dart:ffi 原生互操作:ABI 相关整数类型与 C 库绑定

## 简介

`dart:ffi`(**f**oreign **f**unction **i**nterface)让 Dart Native 平台上的
移动 / 命令行 / 服务端程序直接调用 C API,并读写原生内存。它和平台通道解决的是
**不同层次**的问题:

- 平台通道跨的是 **Dart ↔ 宿主语言(Kotlin/Swift/C++)** 的边界,消息要编解码;
- FFI 直接跳到 **C ABI** 上,没有消息队列,但代价是**类型与内存布局必须由你自己对齐**。

关键概念:

- **`DynamicLibrary`** —— 打开 `.so`/`.dylib`/`.dll`,按符号名取函数指针。
- **`NativeFunction<T>` + `asFunction`** —— 把 C 的函数签名翻译成 Dart 可调用的函数。
- **`AbiSpecificInteger`** —— 表达"C 里宽度**随目标 ABI 变化**的整数类型"的机制。
- **`Struct` / `Packed`** —— 在 Dart 侧描述 C 结构体,字段类型必须与 C 头逐一对齐。
- **`Allocator`(`calloc` / `malloc`)与 `NativeFinalizer`** —— 原生堆的申请与释放。

## 原理详解

### 1. 绑定分两步:先写 C 签名,再写 Dart 签名

c-interop 指南给出的最小流程:

1. `typedef hello_world_func = ffi.Void Function();` —— **C 侧**签名。
2. `typedef HelloWorld = void Function();` —— **Dart 侧**签名。
3. `DynamicLibrary.open(路径)` 打开库。
4. `dylib.lookup<ffi.NativeFunction<hello_world_func>>('hello_world').asFunction()`。

`lookup` 的泛型参数是"原生函数类型",`asFunction()` 返回的是 Dart 函数 ——
两步分开是为了让类型系统能同时表达"两个世界"的签名。多参数场景下
Dart 侧签名是 C 侧签名的**逐参数改写**(`ffi.Int` → `int`、`ffi.Pointer<T>` →
`ffi.Pointer<T>`),本 demo 的 `dart/ffi_binding.dart` 里有两对完整示例。

### 2. 三种类型标记:可实例化 / 只能当标记 / 宽度随 ABI 变

| 分类 | 类型 | 说明 |
| --- | --- | --- |
| 可实例化 | `Array`、`Pointer`、`Struct`、`Union` | 在 Dart 里能构造、能取 `.ref` |
| 只能当标记 | `Bool`、`Double`、`Float`、`Int8/16/32/64`、`Uint8/16/32/64`、`NativeFunction`、`Opaque`、`Void` | 只能出现在签名与结构体字段标注里,不能在 Dart 里 new 出来 |
| 宽度随 ABI 变 | `AbiSpecificInteger` 及其 14 个子类:`Int`、`IntPtr`、`Long`、`LongLong`、`Short`、`SignedChar`、`Size`、`UintPtr`、`UnsignedChar`、`UnsignedInt`、`UnsignedLong`、`UnsignedLongLong`、`UnsignedShort`、`WChar` | 一个 Dart 名字对应多个位宽,由 `@AbiSpecificIntegerMapping` 按 ABI 解析 |

第三类是 FFI 里最容易出错的地方 —— 见下一节。

### 3. 同一个 `Long` 在不同 ABI 上宽度不同

官方在 `AbiSpecificInteger` 类页面上直接给出了 `UintPtr` 的完整映射,在
`Long` 类页面上给出了 `Long` 的映射。把两张表放一起对比(数据见
`python/abi_model.py`):

| ABI | `Long`(C `long`) | `UintPtr`(C `uintptr_t`) | 模型 |
| --- | --- | --- | --- |
| `linuxX64` / `linuxArm64` | 64 | 64 | LP64 |
| `macosX64` / `macosArm64` | 64 | 64 | LP64 |
| `iosArm64` / `iosX64` | 64 | 64 | LP64 |
| `androidArm64` / `androidX64` | 64 | 64 | LP64 |
| **`windowsX64` / `windowsArm64`** | **32** | **64** | **LLP64** |
| `windowsIA32` / `iosArm` / `androidArm` / `linuxArm` | 32 | 32 | ILP32 |

本 demo 的自检断言证明了一件很具体的事:**两张表宽度不一致的 ABI 恰好只有
`windowsArm64` 与 `windowsX64` 两个**,而且都是"指针 64 位、`long` 仍是 32 位"。

后果是可量化的:`python/main.py` 里对一段 `{Long, UintPtr, Long, Long}` 的字段序列,
在 `windowsX64` 上正确宽度是 20 字节,按"long = 8 字节"的错口径算出来是 32 字节 ——
**多算 12 字节**;而同样的错口径在 `linuxX64`/`macosX64`/`androidArm64` 上完全看不出问题。
这正是这类 bug 难以在 CI 上暴露的原因。

> **口径标注**:两份映射表来自不同版本的页面快照 —— `Long` 取自 2.19.5 的页面,
> 其中**没有** `androidRiscv64` / `fuchsiaRiscv64` 两项(后加的 ABI),而 `UintPtr`
> 的当前页面有。因此自检脚本只在**两张表的交集(20 个 ABI)**上做断言,对差集只记录
> 缺失、不推断宽度。这点同时写进了 `python/abi_model.py` 的模块注释。

### 4. 库文件名与加载

| 平台 | 产物 | 备注 |
| --- | --- | --- |
| macOS | `libhello.dylib` | **只能加载已签名的库**(可执行文件含 Dart VM 也受此约束) |
| Windows | `hello.dll` | 官方示例会带 `.def` 模块定义文件参与构建 |
| Linux / Android | `libhello.so` | 官方示例用 CMake 构建 |

因此 Dart 侧一般要像 `dart/ffi_binding.dart` 里那样按 `Platform.isMacOS` /
`Platform.isWindows` 拼路径,而不是硬编码一个文件名。

### 5. 内存与生命周期

- 原生堆用 `calloc`(清零)/ `malloc`(不清零)申请,必须显式 `free` ——
  Dart 的 GC 完全不管这块内存。
- 需要"对象被回收时顺带释放原生资源"时用 `ffi.NativeFinalizer`。
- 结构体字段要逐个对齐:定宽类型用 `Int32`/`Uint64`,变宽类型用 `Long`/`Size`/`UintPtr`;
  需要 1 字节紧排时给结构体打 `@ffi.Packed()`。

## 对比 / 选型

| 维度 | 平台通道 | dart:ffi | Pigeon |
| --- | --- | --- | --- |
| 跨的边界 | Dart ↔ 原生语言 | Dart ↔ C ABI | Dart ↔ 原生(生成桩) |
| 类型安全 | 否(靠约定) | 否(靠 C 头对齐) | 是 |
| 调用开销 | 编解码 + 跨线程投递 | 一次间接调用 | 同平台通道 |
| 能否复用现成 C 库 | 需自己写包装 | **可以** | 需自己写包装 |
| 平台限制 | 全平台 | Dart Native(web 用 JS interop) | 生成 Android/iOS/macOS/Windows |

结论:只是要给原生能力开个口子 → 用通道或 Pigeon;已经有一个成熟的 C/C++ 库(音视频、
加解密、数据库)必须复用时 → 用 FFI。

## 环境准备

- 操作系统:任意(自检脚本是纯 Python)
- Python 3.8+(无第三方依赖)
- 编译 C 库:`clang`(macOS/Linux),或 MSVC/`clang`(Windows)
- Dart / Flutter:阅读代码用,本机未安装 SDK,不做编译

## 运行方式

### Python(ABI 映射表自检)

```bash
cd python
python3 main.py     # 37 项断言
```

### C(构建原生库)

```bash
cd c
clang -shared -fPIC native_counter.c -o libncounter.dylib   # macOS
clang -shared -fPIC native_counter.c -o libncounter.so      # Linux
clang -shared native_counter.c -o ncounter.dll              # Windows
```

### Dart(需装 Dart SDK 后)

```bash
cd dart
dart run ffi_binding.dart
```

## 关键代码片段

```dart
// 第一步:C 的签名 -> 第二步:Dart 侧签名
typedef NcAddNative = ffi.Int Function(ffi.Int, ffi.Int);
typedef NcAdd = int Function(int, int);

// 结构体字段:变宽类型必须用 AbiSpecificInteger
final class NcRecord extends ffi.Struct {
  @ffi.Long()     external int tag;    // ❌ 写成 @ffi.Int64 在 Windows 上会错位
  @ffi.UintPtr()  external int addr;
  @ffi.Int32()    external int count;
}

final lib = ffi.DynamicLibrary.open(libraryPath());
final add = lib.lookup<ffi.NativeFunction<NcAddNative>>('nc_add').asFunction<NcAdd>();
final rec = ffi.calloc<NcRecord>();     // 原生堆,GC 不管
try {
  fill(rec, 7, 0x1000, 42);
  print('${rec.ref.tag} / ${rec.ref.count}');
} finally {
  ffi.calloc.free(rec);                 // 必须显式释放
}
```

C 侧刻意同时暴露 `sizeof(long)` 与 `sizeof(uintptr_t)` 的查询函数 —— 真机上
**直接问编译器**比查文档表更可靠。

## 性能与边界

- FFI 调用本身是一次间接跳转,比平台通道的"编码 + 投递 + 解码"便宜得多;
  真正的成本往往在**跨边界传大块数据时的拷贝**。
- Dart Native 上的 FFI 在**调用线程上同步执行**,长耗时的 C 调用会占住该 Isolate;
  需要并行就分发到别的 Isolate。
- 结构体布局由目标 ABI 决定,`@ffi.Packed()` 会改变对齐 —— 必须与 C 侧的打包指令一致。
- Web 平台不能用 `dart:ffi`,官方明确说 web 一般改用 JS interop。

## 注意事项与常见坑

1. **用定宽类型描述变宽 C 类型**:`long` 写成 `Int64`、`size_t` 写成 `Uint32`,
   在 Windows 64 位 / 32 位目标上直接错位。用 `Long` / `Size` / `UintPtr`。
2. **结构体字段顺序或类型写错**:FFI 不做任何校验,读出来是垃圾值而不是报错。
3. **忘记 `free`**:原生内存不会因为 Dart 对象被回收而释放。
4. **macOS 上加未签名的库**:官方明确要求签名,直接加载会失败。
5. **把 FFI 当"万能桥"**:需要的是 Kotlin/Swift 的平台能力(而非 C 库)时,
   走通道或 Pigeon 更省事;FFI 只值得用在已有 C/C++ 资产上。
6. **手写绑定到几百个函数**:用 `package:ffigen` 生成;原生库的构建打包用
   build hooks(旧称 native assets)。

## 参考资料(实际阅读过的权威来源)

- [dart.dev · C interop using dart:ffi](https://dart.dev/interop/c-interop)
  —— 两步绑定流程、库文件名三平台差异、macOS 签名要求、类型分类、ffigen 与 build hooks
- [api.dart.dev · AbiSpecificInteger](https://api.dart.dev/stable/dart-ffi/AbiSpecificInteger-class.html)
  —— `@AbiSpecificIntegerMapping` 机制与 `UintPtr` 的完整 22 项 ABI 映射
- [api.dart.dev · Long](https://api.dart.dev/stable/2.19.5/dart-ffi/Long-class.html)
  —— `Long`(C `long`)的 20 项 ABI 映射(2.19.5 快照口径)
- [api.dart.dev · dart:ffi 库索引](https://api.dart.dev/dart-ffi/)
  —— `Allocator` / `Packed` / `NativeFinalizer` / `NativeCallable` / `VarArgs` / `Handle` 等类型清单

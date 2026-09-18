// 用 dart:ffi 调用上面的 C 库。本机无 Dart/Flutter SDK,不做编译,按官方文档的
// 真实 API 书写,供人工审查。
//
// 权威依据:dart.dev/interop/c-interop(c-interop 指南)。
//
// [要点 1] 库文件名三平台不同:libhello.dylib(macOS)/ libhello.dll(Windows)/
//          libhello.so(Linux),所以打开前要先按平台拼路径。
// [要点 2] macOS 上可执行文件(含 Dart VM)**只能加载已签名的库**,未签名会在打开时失败。
// [要点 3] 声明分两步:第一步用 ffi.NativeFunction<...> 写"C 的签名",
//          第二步写"Dart 侧可调用的签名",再 lookup(...).asFunction() 把两者接起来。
// [要点 4] C 的 long / size_t / uintptr_t 宽度随 ABI 变化,必须用 AbiSpecificInteger
//          家族(Long / Size / UintPtr ...)标注;用定宽的 Int64 描述 long,在
//          Windows 64 位(LLP64)上会错位 4 字节。映射表见 ../python/abi_model.py。

import 'dart:ffi' as ffi;
import 'dart:io' show Directory, Platform;

// ---------- 第一步:C 的签名 ----------
typedef NcAddNative = ffi.Int Function(ffi.Int, ffi.Int);
typedef NcSizeofLongNative = ffi.Int Function();
typedef NcFillRecordNative = ffi.Void Function(
    ffi.Pointer<NcRecord>, ffi.Long, ffi.UintPtr, ffi.Int32);
typedef NcReadTagNative = ffi.Int64 Function(ffi.Pointer<NcRecord>);

// ---------- 第二步:Dart 侧签名 ----------
typedef NcAdd = int Function(int, int);
typedef NcSizeof = int Function();
typedef NcFillRecord = void Function(ffi.Pointer<NcRecord>, int, int, int);
typedef NcReadTag = int Function(ffi.Pointer<NcRecord>);

/// 与 C 侧 nc_record 一一对应。字段顺序与类型都必须与 C 头一致。
///
/// 关键:`tag` 用 `ffi.Long` 而不是 `ffi.Int64` —— 前者是 AbiSpecificInteger,
/// 会随目标 ABI 解析成 4 或 8 字节;后者在 Windows 上会把结构体整体推错位。
final class NcRecord extends ffi.Struct {
  @ffi.Long()
  external int tag;

  @ffi.UintPtr()
  external int addr;

  @ffi.Int32()
  external int count;
}

/// 按平台拼出动态库路径。
String libraryPath() {
  final String dir = Directory.current.path;
  if (Platform.isMacOS) return '$dir/libncounter.dylib';
  if (Platform.isWindows) return '$dir/ncounter.dll';
  return '$dir/libncounter.so'; // Linux / Android
}

/// 打开库并解析符号。
///
/// `lookup<ffi.NativeFunction<...>>('名字')` 返回原生函数指针,
/// `.asFunction()` 才把它变成可调用的 Dart 函数。
/// (本机无 SDK,`lookup` 失败时抛出的具体异常类型未经实测,故此处不写死,
///  以 api.dart.dev 的 DynamicLibrary 页为准。)
ffi.DynamicLibrary openCounter() {
  final ffi.DynamicLibrary lib = ffi.DynamicLibrary.open(libraryPath());
  return lib;
}

void main() {
  final ffi.DynamicLibrary lib = openCounter();

  final NcAdd add =
      lib.lookup<ffi.NativeFunction<NcAddNative>>('nc_add').asFunction<NcAdd>();
  final NcSizeof sizeofLong = lib
      .lookup<ffi.NativeFunction<NcSizeofLongNative>>('nc_sizeof_long')
      .asFunction<NcSizeof>();

  print('nc_add(2, 3) = ${add(2, 3)}');
  // 直接问原生库"这台机器上 long 几字节",比查表更可信。
  print('sizeof(long) on ${Platform.operatingSystem} = ${sizeofLong()}');
  // 与 AbiSpecificInteger 映射表的预期对照:
  //   macOS(arm64/x64) → 8;Linux x64 → 8;Windows x64 → 4
  // 见 ../python/abi_model.py 的 LONG_BITS(注意其中有条目来自不同版本的
  // 文档快照,README 已标注口径)。

  final NcFillRecord fill = lib
      .lookup<ffi.NativeFunction<NcFillRecordNative>>('nc_fill_record')
      .asFunction<NcFillRecord>();
  final NcReadTag readTag = lib
      .lookup<ffi.NativeFunction<NcReadTagNative>>('nc_read_tag')
      .asFunction<NcReadTag>();

  // 结构体在原生堆上分配:calloc 会清零,malloc 不会。
  final ffi.Pointer<NcRecord> rec = ffi.calloc<NcRecord>();
  try {
    fill(rec, 7, 0x1000, 42);
    print('rec.tag = ${rec.ref.tag}, rec.count = ${rec.ref.count}');
    print('nc_read_tag = ${readTag(rec)}');
  } finally {
    ffi.calloc.free(rec); // 忘了这一句就是原生内存泄漏,Dart 的 GC 管不到
  }
}

// ---------------------------------------------------------------------------
// 工程化补充(来自 c-interop 指南原文)
// ---------------------------------------------------------------------------
//
// * 大 API 面不要手写绑定:用 package:ffigen 从 C 头文件生成 Dart FFI 包装。
// * 原生库的构建与分发可以用 build hooks(旧称 native assets),由 Dart 侧声明
//   原生依赖并透明构建、打包,运行时直接可用;相关标记是 `@Native` 与 `@DefaultAsset`。
// * 给结构体打 `@ffi.Packed()` 表示成员按 1 字节对齐(即 C 的 `#pragma pack(1)`)。
// * 需要把 Dart 函数交给 C 回调时用 `ffi.NativeCallable`;需要"对象被回收时释放
//   原生资源"时用 `ffi.NativeFinalizer`。
// * 变参函数(C 的 `...`)用 `ffi.VarArgs` 表达;`ffi.Handle` 对应 dart_api.h 的
//   Dart_Handle,属于 VM 内部接口。

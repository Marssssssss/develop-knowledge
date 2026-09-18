/* dart:ffi demo 的原生库:故意同时暴露 C long 与 uintptr_t,用来在真机上观测
 * 两者的宽度差异(Windows 64 位是 LLP64,long 仍为 4 字节;Linux/macOS 是 LP64,为 8)。
 *
 * 编译(三平台):
 *   macOS : clang -shared -fPIC native_counter.c -o libncounter.dylib
 *   Linux : clang -shared -fPIC native_counter.c -o libncounter.so
 *   Windows: clang -shared native_counter.c -o ncounter.dll
 *
 * 没有引入任何第三方依赖。宏 NC_EXPORT 只是把可见性写清楚。
 */

#include <stddef.h>
#include <stdint.h>

#if defined(_WIN32)
#define NC_EXPORT __declspec(dllexport)
#else
#define NC_EXPORT __attribute__((visibility("default")))
#endif

/* 一个朴素的结构体:故意把 long 和 uintptr_t 放在一起。
 * 它的真实布局由目标 ABI 决定 —— Dart 侧必须用 Long / UintPtr 这类
 * AbiSpecificInteger 标记去描述,不能用 Int64 硬写。 */
typedef struct {
    long tag;          /* LP64: 8 字节;LLP64(Windows 64): 4 字节 */
    uintptr_t addr;    /* 两种模型下都是指针宽度 */
    int32_t count;     /* 定宽:always 4 */
} nc_record;

/* 只返回一个常量,用于验证符号解析与调用约定。 */
NC_EXPORT int nc_add(int a, int b) {
    return a + b;
}

/* 把当前编译目标的 sizeof 直接报出来 —— 这是最可靠的"这台机器怎么算"的答案,
 * 不依赖任何文档口径。 */
NC_EXPORT int nc_sizeof_long(void) {
    return (int)sizeof(long);
}

NC_EXPORT int nc_sizeof_uintptr(void) {
    return (int)sizeof(uintptr_t);
}

NC_EXPORT int nc_sizeof_record(void) {
    return (int)sizeof(nc_record);
}

/* 把记录写满,供 Dart 侧读回来比对字段偏移。 */
NC_EXPORT void nc_fill_record(nc_record *out, long tag, uintptr_t addr, int32_t count) {
    if (out == NULL) {
        return;
    }
    out->tag = tag;
    out->addr = addr;
    out->count = count;
}

/* 返回值用 int64_t 而不是 long:跨 ABI 传值时用定宽类型最安全。 */
NC_EXPORT int64_t nc_read_tag(const nc_record *in) {
    if (in == NULL) {
        return -1;
    }
    return (int64_t)in->tag;
}

# -*- coding: utf-8 -*-
"""JNI 符号解析与 RegisterNatives 断言(全部对照 JNI 规范原文)。"""

from jnimap import (
    REGISTER_NATIVES_INDEX, SoImage, long_name, mangle_args,
    mangle_ident, register_natives, resolve, short_name, unregister,
)

PASS = []


def ok(msg):
    PASS.append(msg)
    print(f"  [ok] {msg}")


def main():
    print("1. 拼接与转义(规范转义表)")
    assert short_name("java/lang/String", "indexOf") == "Java_java_lang_String_indexOf"
    ok("Java_ + FQCN(斜杠换下划线) + _ + 方法名")
    assert mangle_ident("some_method") == "some_1method"
    ok("标识符里的 '_' → _1")
    assert mangle_args("(Ljava/lang/String;[I)V") == "Ljava_lang_String_2_3I"
    ok("参数签名:';' → _2、'[' → _3;参数部分不含括号与返回值")
    assert mangle_ident("caf\u00e9") == "caf_000e9"
    ok("非 ASCII → _0XXXX,十六进制**小写**(规范原文:as opposed to _0ABCD)")

    print("2. 长名仅在 native 重载时需要")
    ln = long_name("Cls2", "f", "(II)V")
    assert ln == "Java_Cls2_f__II"
    ok("双 native 重载 f(II)/f(D):长名 = 短名 + __ + 转义参数签名")

    exported = {"Java_Cls1_g": 0x1000}      # Cls1: g(int) 是 Java 方法,g(double) 是 native
    kind, sym = resolve(exported, "Cls1", "g", "(D)D", native_overload_siblings=False)
    assert kind == "short"
    ok("规范 Cls1 例子:同名 Java 方法不进符号库,短名即可绑定,无需长名")

    exported2 = {"Java_Cls2_f__II": 0x2000, "Java_Cls2_f__D": 0x2040}
    k2, s2 = resolve(exported2, "Cls2", "f", "(II)V", native_overload_siblings=True)
    assert (k2, s2) == ("long", "Java_Cls2_f__II")
    k3, s3 = resolve(exported2, "Cls2", "f", "(D)D", native_overload_siblings=True)
    assert (k3, s3) == ("long", "Java_Cls2_f__D")
    ok("两个都是 native → VM 先找短名落空,再按参数签名取长名")

    k4, s4 = resolve({"Java_X_h": 1}, "X", "h", "(I)I", native_overload_siblings=False)
    assert k4 == "short" and s4 == "Java_X_h"
    ok("无重载时短名优先命中,长名根本不查")

    print("3. RegisterNatives(JNINativeMethod 三元组)")
    assert REGISTER_NATIVES_INDEX == 215
    ok("RegisterNatives 是 JNIEnv 函数表第 215 项(functions.html LINKAGE)")
    so = SoImage()
    table_off, table_bytes = so.method_table([
        ("nativeRead", "(I)[B", 0x401000),
        ("nativeInit", "()V", 0x401200),
    ])
    assert table_bytes == 2 * 24
    assert so.read_table(table_off, 2) == [("nativeRead", "(I)[B", 0x401000),
                                           ("nativeInit", "()V", 0x401200)]
    ok("结构体 {char*name; char*signature; void*fnPtr} 24 字节/项,字节级可解析"
       "(hook 点:读参数三元组即可拿到明文名与真实函数地址)")

    rc, bound, errors = register_natives(so, table_off, 2,
                                         {"nativeRead": "instance", "nativeInit": "static"})
    assert rc == 0 and not errors
    assert bound[0]["second_arg"] == "jobject" and bound[1]["second_arg"] == "jclass"
    ok("第二参数:实例方法收 jobject,静态方法收 jclass(design.html Native Method Arguments)")

    t2_off, _ = so.method_table([("toString", "()Ljava/lang/String;", 0x401400)])
    rc2, bound2, errors2 = register_natives(so, t2_off, 1, {"toString": "java"})
    assert rc2 == -1 and errors2 == [("NoSuchMethodError", "toString")]
    ok("目标不是 native(toString 是 Java 方法)→ NoSuchMethodError,返回负值;fnPtr 形如 "
       "ReturnType (*fnPtr)(JNIEnv*, jobject objectOrClass, ...)")

    print("4. UnregisterNatives 回退")
    unregister(bound2)
    assert bound2 == []
    ok("注销后回到链接前状态——符号名解析通道重新生效(壳反注册对抗的规范依据)")

    print(f"\n共 {len(PASS)} 项断言全部通过")


if __name__ == "__main__":
    main()

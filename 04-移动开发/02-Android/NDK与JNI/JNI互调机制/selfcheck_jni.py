"""JNI 互调模型自检：判据全部来自 Oracle JNI 规范与 OpenJDK jni.h。"""

from main import (
    argument_signature,
    mangle, short_name, long_name, resolve_symbol, jni_signature,
    VM, JNIEnv, JniError, UnsatisfiedLinkError,
    jni_on_load, register_natives, lookup_native,
    JNI_VERSION_1_6, JNI_VERSION_10, JNI_OK, JNI_ERR, JNI_EVERSION, JNI_ENOMEM,
    JNI_EDETACHED, JNI_EINVAL,
    JNILocalRefType, JNIGlobalRefType, JNIWeakGlobalRefType, JNIInvalidRefType,
    DEFAULT_LOCAL_CAPACITY,
)

PASS = 0


def ok(cond, label):
    global PASS
    assert cond, label
    PASS += 1


def raises(exc, fn, label):
    global PASS
    try:
        fn()
    except exc:
        PASS += 1
    except Exception as e:  # noqa: BLE001
        raise AssertionError("%s: 期望 %s，实得 %s(%s)" % (label, exc.__name__, type(e).__name__, e))
    else:
        raise AssertionError("%s: 期望抛 %s，但没有抛" % (label, exc.__name__))


# ---- 1. mangling：规范里的转义表 ----
ok(mangle("_") == "_1", "'_' 转义为 _1（jni.h 同名函数/字段分隔）")
ok(mangle(";") == "_2", "';' 转义为 _2（签名里的类型结束符）")
ok(mangle("[") == "_3", "'[' 转义为 _3（数组描述符）")
ok(mangle("a-b") == "a_0002db", "非字母数字走 _0 + 四位小写 hex")
ok(mangle("pkg/Cls") == "pkg_Cls", "'/' 转成 '_'（FQCN 分隔符）")
ok(mangle("中") == "_04e2d", "非 ASCII Unicode 走 _0XXXX")

# ---- 2. 短名 / 长名 ----
ok(short_name("pkg.Cls", "f") == "Java_pkg_Cls_f", "短名 = Java_ + mangled(FQCN) + _ + mangled(method)")
ok(long_name("pkg.Cls", "f", "(ILjava/lang/String;)D") == "Java_pkg_Cls_f__ILjava_lang_String_2",
   "长名 = 短名 + __ + mangled(参数签名)；规范原文的例子不含返回类型 D")

# ---- 3. 链接解析：先短名后长名 ----
lib = {"Java_pkg_Cls_g"}
ok(resolve_symbol(lib, "pkg.Cls", "g", "(I)I", overloaded=True) == "Java_pkg_Cls_g",
   "库里只有短名时，重载方法也能用短名解析")
lib2 = {"Java_pkg_Cls_g__I", "Java_pkg_Cls_g__D"}
ok(resolve_symbol(lib2, "pkg.Cls", "g", "(D)I", overloaded=True) == "Java_pkg_Cls_g__D",
   "双下划线长名按参数类型区分重载（返回类型不参与）")
raises(UnsatisfiedLinkError, lambda: resolve_symbol(lib2, "pkg.Cls", "g", "(F)I", overloaded=True),
       "签名不匹配的长名 -> UnsatisfiedLinkError")
ok(resolve_symbol(lib2, "pkg.Cls", "g", "(I)J", overloaded=True) == "Java_pkg_Cls_g__I",
   "返回类型不同不影响解析（只看参数）")
ok(resolve_symbol(lib, "pkg.Cls", "g", "(I)I", overloaded=False) == "Java_pkg_Cls_g",
   "未重载时按短名解析")
raises(UnsatisfiedLinkError, lambda: resolve_symbol({}, "pkg.Cls", "h", "()V", overloaded=False),
       "库里没有对应符号 -> UnsatisfiedLinkError")

# ---- 4. 签名拼装 ----
ok(argument_signature("(ILjava/lang/String;)D") == "ILjava/lang/String;",
   "长名只取括号内的参数部分")
ok(jni_signature(["I", "Ljava/lang/String;"], "D") == "(ILjava/lang/String;)D", "签名 = (params)ret")

# ---- 5. 局部引用生命周期 ----
vm = VM()
env = JNIEnv(vm, env_id=1)
o1 = vm.new_object()
r1 = env.new_local_ref(o1)
ok(env.get_object_ref_type(r1) == JNILocalRefType, "新建的对象引用是局部引用")
env.on_method_return()
ok(env.get_object_ref_type(r1) == JNIInvalidRefType,
   "native 方法返回后局部引用自动失效（规范：registry 被删除）")

# ---- 6. 全局引用跨方法、跨线程存活 ----
r2 = env.new_local_ref(vm.new_object())
g = env.new_global_ref(r2)
env.on_method_return()
ok(env.get_object_ref_type(g) == JNIGlobalRefType, "全局引用不随方法返回失效")
other = JNIEnv(vm, env_id=2)
ok(other.get_object_ref_type(g) == JNIGlobalRefType, "全局引用在别的线程里同样有效")

# ---- 7. 局部引用不可跨线程 ----
r3 = env.new_local_ref(vm.new_object())
ok(other.get_object_ref_type(r3) == JNIInvalidRefType,
   "局部引用只在创建它的线程有效（规范明确禁止跨线程传递）")

# ---- 8. 弱全局引用与 GC ----
ow = vm.new_object()
w = env.new_weak_global_ref(env.new_local_ref(ow))
ok(env.get_object_ref_type(w) == JNIWeakGlobalRefType, "弱全局引用类型正确")
ok(env.deref_weak(w) == ow, "对象存活时弱全局可解引用")
ok(vm.gc(ow) is True, "只被弱全局引用的对象会被 GC 回收")
ok(env.deref_weak(w) is None, "GC 之后弱全局解引用返回 None（JNI 语义：返回 NULL）")
ow2 = vm.new_object()
w2 = env.new_weak_global_ref(env.new_global_ref(env.new_local_ref(ow2)))
ok(vm.gc(ow2) is False, "有全局引用钉住的对象不会被回收")
ok(env.deref_weak(w2) == ow2, "被钉住的对象弱全局仍可解引用")

# ---- 9. IsSameObject ----
oa, ob = vm.new_object(), vm.new_object()
ra = env.new_global_ref(env.new_local_ref(oa))
rb = env.new_global_ref(env.new_local_ref(ob))
ok(env.is_same_object(ra, rb) is False, "不同对象 IsSameObject 为假")
ok(env.is_same_object(ra, ra) is True, "同一对象 IsSameObject 为真")
ok(env.is_same_object(None, None) is True, "两个 NULL 视为相同")
ok(env.is_same_object(ra, None) is False, "对象与 NULL 不相同")

# ---- 10. 局部引用容量 ----
env2 = JNIEnv(vm, env_id=3, local_capacity=2)
env2.new_local_ref(vm.new_object())
env2.new_local_ref(vm.new_object())
raises(JniError, lambda: env2.new_local_ref(vm.new_object()),
       "局部引用表溢出 -> JNI_ENOMEM")
ok(env2.ensure_local_capacity(2) == JNI_OK, "EnsureLocalCapacity 容量足够返回 JNI_OK")
ok(env2.ensure_local_capacity(3) == JNI_ENOMEM, "容量不足返回 JNI_ENOMEM")
ok(env2.ensure_local_capacity(-1) == JNI_EINVAL, "负数容量返回 JNI_EINVAL")

# ---- 11. Push/PopLocalFrame ----
env3 = JNIEnv(vm, env_id=4)
base = env3.new_local_ref(vm.new_object())
ok(env3.push_local_frame(8) == JNI_OK, "PushLocalFrame 成功返回 JNI_OK")
inner = env3.new_local_ref(vm.new_object())
ok(env3.get_object_ref_type(inner) == JNILocalRefType, "帧内新建的局部引用有效")
env3.pop_local_frame(base)
ok(env3.get_object_ref_type(inner) == JNIInvalidRefType, "PopLocalFrame 释放帧内全部局部引用")
ok(env3.get_object_ref_type(base) == JNILocalRefType, "PopLocalFrame 的 result 被保留并提升到外层帧")
raises(JniError, lambda: env3.pop_local_frame(None), "没有帧可弹 -> JNI_EINVAL")

# ---- 12. 线程 detach ----
env3.attached = False
raises(JniError, lambda: env3.new_local_ref(vm.new_object()), "线程已 detach -> JNI_EDETACHED")
try:
    env3.new_local_ref(vm.new_object())
except JniError as e:
    ok(e.code == JNI_EDETACHED, "错误码确为 JNI_EDETACHED(-2)")

# ---- 13. JNI_OnLoad 版本协商 ----
ok(jni_on_load(vm, JNI_VERSION_1_6) == JNI_OK, "返回受支持版本 -> JNI_OK")
ok(jni_on_load(vm, JNI_VERSION_10) == JNI_OK, "返回 JNI_VERSION_10 -> JNI_OK")
ok(jni_on_load(VM(supported_versions={JNI_VERSION_1_6}), JNI_VERSION_10) == JNI_EVERSION,
   "返回 VM 不支持的版本 -> JNI_EVERSION，库加载失败")

# ---- 14. RegisterNatives ----
tbl = register_natives({}, [
    {"name": "add", "signature": "(II)I", "fnPtr": "c_add"},
    {"name": "add", "signature": "(DD)D", "fnPtr": "c_add_d"},
])
ok(lookup_native(tbl, "add", "(II)I") == "c_add", "按 (name, signature) 查到函数指针")
ok(lookup_native(tbl, "add", "(DD)D") == "c_add_d", "同名不同签名映射到不同实现")
raises(UnsatisfiedLinkError, lambda: lookup_native(tbl, "add", "()V"), "未登记的签名找不到")
tbl2 = register_natives(tbl, [{"name": "add", "signature": "(II)I", "fnPtr": "c_add_v2"}])
ok(lookup_native(tbl2, "add", "(II)I") == "c_add_v2", "重复登记同一 (name, signature) 后者覆盖前者")
ok(lookup_native(tbl, "add", "(II)I") == "c_add", "register_natives 不修改原表")

print("PASS %d" % PASS)

"""JNI 互调机制的可执行模型。

依据：
  - Oracle《Java Native Interface Specification》Chapter 2「Design Overview」
    （Resolving Native Method Names / Native Method Arguments / Global and Local References）
  - OpenJDK `src/java.base/share/native/include/jni.h`
    （JNI 版本常量、jobjectRefType 枚举、JNINativeMethod、函数表中的引用操作）

只做可判定的三块：符号名 mangling 与链接解析、引用的生命周期、JNI_OnLoad 版本协商。
"""

# ---------- 常量（jni.h 原文取值） ----------
JNI_VERSION_1_1 = 0x00010001
JNI_VERSION_1_2 = 0x00010002
JNI_VERSION_1_4 = 0x00010004
JNI_VERSION_1_6 = 0x00010006
JNI_VERSION_1_8 = 0x00010008
JNI_VERSION_9 = 0x00090000
JNI_VERSION_10 = 0x000A0000

JNI_OK = 0
JNI_ERR = -1
JNI_EDETACHED = -2
JNI_EVERSION = -3
JNI_ENOMEM = -4
JNI_EEXIST = -5
JNI_EINVAL = -6

JNIInvalidRefType = 0
JNILocalRefType = 1
JNIGlobalRefType = 2
JNIWeakGlobalRefType = 3

# 默认局部引用表容量（JVM 规范未定死，HotSpot 默认 16；这里取一个可配置的有限值）
DEFAULT_LOCAL_CAPACITY = 16


class UnsatisfiedLinkError(Exception):
    pass


class JniError(Exception):
    def __init__(self, code, message):
        super().__init__("%s (code=%d)" % (message, code))
        self.code = code


# ---------- 1. 名字 mangling ----------

_ESCAPES = {"_": "_1", ";": "_2", "[": "_3"}


def mangle(text):
    """把 Java 全限定名 / 方法名 / 类型签名转成 C 函数名片段。

    '.'→'/'→'_'；'_'→'_1'；';'→'_2'；'['→'_3'；非 [A-Za-z0-9] 走 _0XXXX（小写 hex）。
    """
    out = []
    for ch in text:
        if ch == "/":
            out.append("_")
        elif ch in _ESCAPES:
            out.append(_ESCAPES[ch])
        elif ch.isascii() and (ch.isalnum()):
            out.append(ch)
        else:
            out.append("_0%04x" % ord(ch))  # 规范：小写十六进制
    return "".join(out)


def short_name(class_name, method_name):
    """Java_ + mangled(FQCN) + '_' + mangled(method)"""
    return "Java_" + mangle(class_name.replace(".", "/")) + "_" + mangle(method_name)


def argument_signature(signature):
    """取出括号内的参数部分；长名只 mangling 参数，不含括号与返回类型。"""
    i = signature.find("(")
    j = signature.rfind(")")
    if i < 0 or j < 0 or j < i:
        raise ValueError("illegal JNI signature: %s" % signature)
    return signature[i + 1:j]


def long_name(class_name, method_name, signature):
    """重载时才需要：短名 + '__' + mangled(参数签名)"""
    return short_name(class_name, method_name) + "__" + mangle(argument_signature(signature))


def resolve_symbol(native_symbols, class_name, method_name, signature, overloaded):
    """规范：先找短名，再找长名；且只有与『本库内另一个 native 方法』重载时才需长名。"""
    short = short_name(class_name, method_name)
    if not overloaded and short in native_symbols:
        return short
    ln = long_name(class_name, method_name, signature)
    if ln in native_symbols:
        return ln
    if short in native_symbols:
        return short
    raise UnsatisfiedLinkError("No implementation found for %s.%s%s" % (class_name, method_name, signature))


def jni_signature(params, ret):
    """由类型描述符拼出 JNI 签名，如 (ILjava/lang/String;)D"""
    return "(" + "".join(params) + ")" + ret


# ---------- 2. 引用 ----------

class Ref:
    __slots__ = ("oid", "kind", "env_id", "frame", "alive")

    def __init__(self, oid, kind, env_id, frame):
        self.oid = oid
        self.kind = kind
        self.env_id = env_id
        self.frame = frame
        self.alive = True


class VM:
    """极简 VM：持有对象存活标记，用于弱全局引用的『被回收后返回 None』语义。"""

    def __init__(self, supported_versions=(JNI_VERSION_1_6, JNI_VERSION_1_8, JNI_VERSION_10)):
        self.objects = {}
        self.next_oid = 1
        self.globals = {}
        self.weak_globals = {}
        self.supported_versions = set(supported_versions)

    def new_object(self, tag=""):
        oid = self.next_oid
        self.next_oid += 1
        self.objects[oid] = True
        return oid

    def gc(self, oid):
        """模拟 GC。全局引用是强引用，会钉住对象；弱全局引用不阻止回收。"""
        if self.globals.get(oid, 0) > 0:
            return False
        self.objects[oid] = False
        return True


class JNIEnv:
    """每个线程一份；局部引用表按『帧』管理（native 方法调用 = 一帧）。"""

    def __init__(self, vm, env_id, local_capacity=DEFAULT_LOCAL_CAPACITY):
        self.vm = vm
        self.env_id = env_id
        self.local_capacity = local_capacity
        self.locals = []          # 当前帧的局部引用
        self.frame_stack = []     # PushLocalFrame 压入的帧
        self.attached = True

    # --- 引用创建 ---
    def _require_attached(self):
        if not self.attached:
            raise JniError(JNI_EDETACHED, "thread detached from the VM")

    def new_local_ref(self, oid):
        self._require_attached()
        if len(self.locals) >= self.local_capacity:
            raise JniError(JNI_ENOMEM, "local reference table overflow")
        r = Ref(oid, JNILocalRefType, self.env_id, len(self.frame_stack))
        self.locals.append(r)
        return r

    def new_global_ref(self, ref):
        self._require_attached()
        ref = self._check(ref)
        g = Ref(ref.oid, JNIGlobalRefType, -1, -1)
        self.vm.globals[g.oid] = self.vm.globals.get(g.oid, 0) + 1
        return g

    def new_weak_global_ref(self, ref):
        self._require_attached()
        ref = self._check(ref)
        w = Ref(ref.oid, JNIWeakGlobalRefType, -1, -1)
        self.vm.weak_globals.setdefault(w.oid, []).append(w)
        return w

    def _check(self, ref):
        if ref is None or not ref.alive:
            raise JniError(JNI_EINVAL, "invalid reference")
        return ref

    # --- 引用查询 ---
    def get_object_ref_type(self, ref):
        if ref is None or not ref.alive:
            return JNIInvalidRefType
        if ref.kind == JNILocalRefType and ref.env_id != self.env_id:
            # 规范：局部引用只在创建它的线程有效
            return JNIInvalidRefType
        return ref.kind

    def is_same_object(self, a, b):
        if a is None and b is None:
            return True
        if a is None or b is None:
            return False
        return a.oid == b.oid and (self.vm.objects.get(a.oid, False) or a.kind == JNIGlobalRefType)

    def deref_weak(self, weak_ref):
        """弱全局：对象还活着 -> 可用；被回收 -> None（JNI 语义是返回 NULL）。"""
        if self.vm.objects.get(weak_ref.oid, False):
            return weak_ref.oid
        weak_ref.alive = False
        return None

    # --- 释放 ---
    def delete_local_ref(self, ref):
        self._check(ref)
        ref.alive = False

    def delete_global_ref(self, ref):
        self._check(ref)
        ref.alive = False
        if self.vm.globals.get(ref.oid, 0) > 0:
            self.vm.globals[ref.oid] -= 1
            if self.vm.globals[ref.oid] == 0 and self.vm.objects.get(ref.oid, False):
                ref_kind_alive = any(
                    w.alive for ws in self.vm.weak_globals.values() for w in ws
                )
                del self.vm.globals[ref.oid]

    def ensure_local_capacity(self, capacity):
        self._require_attached()
        if capacity < 0:
            return JNI_EINVAL
        if capacity > self.local_capacity:
            return JNI_ENOMEM
        return JNI_OK

    def push_local_frame(self, capacity):
        if capacity < 0 or self.ensure_local_capacity(capacity) != JNI_OK:
            return JNI_ENOMEM
        self.frame_stack.append(list(self.locals))
        self.locals = []
        return JNI_OK

    def pop_local_frame(self, result):
        """释放当前帧所有局部引用，只把 result 提升回上一帧。"""
        if not self.frame_stack:
            raise JniError(JNI_EINVAL, "no local frame to pop")
        for r in self.locals:
            r.alive = False
        self.locals = self.frame_stack.pop()
        if result is not None and result.kind == JNILocalRefType:
            result.frame = len(self.frame_stack)
            self.locals.append(result)
        return result

    # --- 方法边界：native 方法返回后，该帧局部引用全部失效 ---
    def on_method_return(self):
        for r in self.locals:
            r.alive = False
        self.locals = []


# ---------- 3. JNI_OnLoad 版本协商 ----------

def jni_on_load(vm, returned_version):
    """JNI_OnLoad 返回不受支持的版本号 -> 库加载失败（JNI_EVERSION）。"""
    if returned_version in vm.supported_versions:
        return JNI_OK
    return JNI_EVERSION


def register_natives(existing, methods):
    """JNINativeMethod{name, signature, fnPtr} 批量登记；重复 (name,signature) 后者覆盖前者。"""
    table = dict(existing)
    for m in methods:
        table[(m["name"], m["signature"])] = m["fnPtr"]
    return table


def lookup_native(table, name, signature):
    key = (name, signature)
    if key not in table:
        raise UnsatisfiedLinkError("No implementation found for %s%s" % (name, signature))
    return table[key]

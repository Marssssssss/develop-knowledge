# -*- coding: utf-8 -*-
"""Frida Java 层 hook 的分发语义模型(模拟 frida-java-bridge 核心 API)。

模拟对象(依据 frida.re 官方文档语义):
  Java.perform / performNow / available / use / cast / retain
  method.implementation 替换 + this.xxx() 调原始实现
  .overload(...) 重载选择 / $new 构造 / $dispose
  send()/recv() 宿主<->agent 消息通道
跑法: python frida_java_hook.py  -> 12 项断言
"""
import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ---------------- 消息通道(宿主 <-> agent) ----------------
class MessageBus:
    def __init__(self): self.host_inbox, self.agent_inbox = [], []
    def send(self, payload):          # agent -> host(异步)
        self.host_inbox.append(payload)
    def recv(self):                   # host 侧收
        return self.host_inbox.pop(0) if self.host_inbox else None
    def post_to_agent(self, payload): # host -> agent
        self.agent_inbox.append(payload)

BUS = MessageBus()

# ---------------- ART 方法分发模拟 ----------------
class Overload:                        # 一个具体重载(参数类型串 -> 实现)
    __slots__ = ("params", "orig", "impl", "hooked")
    def __init__(self, params, fn):
        self.params, self.orig, self.impl, self.hooked = tuple(params), fn, fn, False
    def dispatch(self, this, args, from_replacement=False):
        # 替换函数内部经 this.xxx() 调用 -> 走 orig(不递归); 外部调用 -> 走当前 impl
        target = self.orig if (self.hooked and from_replacement) else self.impl
        return target(this, *args)

class JavaMethod:                      # 方法名聚合所有重载
    def __init__(self, name):
        self.name, self.overloads = name, []
    def add(self, params, fn):
        self.overloads.append(Overload(params, fn))
    def resolve(self, arg_types):
        for ov in self.overloads:
            if list(ov.params) == list(arg_types): return ov
        raise KeyError("no such overload: %s(%s)" % (self.name, arg_types))
    # implementation 属性挂在选定的重载句柄上(见 MethodHandle)
    def dispatch(self, this, args, from_replacement=False):
        sig = [t.__name__ for t in map(type, args)]
        try:    ov = self.resolve(sig)
        except KeyError:               # 参数类型模拟不够精确时按元数兜底
            cands = [o for o in self.overloads if len(o.params) == len(args)]
            if len(cands) != 1: raise
            ov = cands[0]
        return ov.dispatch(this, args, from_replacement)

class MethodHandle:                    # Java.use(...).method / .overload(...) 返回的句柄
    def __init__(self, method, ov=None):
        self._method, self._ov = method, ov
    def overload(self, *param_type_names):
        for o in self._method.overloads:
            if list(o.params) == list(param_type_names):
                return MethodHandle(self._method, o)
        raise KeyError("overload not found: %s" % (param_type_names,))
    @property
    def implementation(self):
        return (self._ov or self._method).impl
    @implementation.setter
    def implementation(self, fn):
        target = self._ov or self._method
        if isinstance(target, JavaMethod):        # 单重载方法直接替换
            if len(target.overloads) != 1:
                raise RuntimeError("ambiguous overloads, use .overload(...)")
            target = target.overloads[0]
        target.impl, target.hooked = fn, True

class Instance:
    def __init__(self, klass, fields): self._klass, self._fields = klass, fields
    def __getattr__(self, name):
        m = self._klass.methods.get(name)
        if m is None: raise AttributeError(name)
        def call(*args, _from_replacement=False):
            return m.dispatch(self, list(args), _from_replacement)
        return call
    # Frida 替换函数里的 this.xxx() 必须走原始实现:
    def call_original(self, name, *args):
        return getattr(self, name)(*args, _from_replacement=True)

class Klass:
    def __init__(self, name, ctor=None):
        self.name, self.methods, self._ctor = name, {}, (ctor or (lambda s: None))
    def method(self, name, params, fn):
        self.methods.setdefault(name, JavaMethod(name)).add(params, fn)
    def handle(self, name):            # Java.use 包装上的方法访问
        if name not in self.methods: raise KeyError(name)
        return MethodHandle(self.methods[name])

class ClassLoader:
    def __init__(self): self.classes, self.ready = {}, False
    def register(self, klass): self.classes[klass.name] = klass
    def find(self, name):
        if not self.ready: raise RuntimeError("class loader not available yet")
        if name not in self.classes: raise ClassNotFoundException(name)
        return self.classes[name]

class ClassNotFoundException(Exception): pass

# ---------------- Java 命名空间模拟 ----------------
class JavaNS:
    def __init__(self, loader):
        self._loader, self._pending = loader, []
        self._retain_pool = []
    @property
    def available(self): return self._loader is not None
    def perform(self, fn):
        if self._loader.ready: fn(); return None
        self._pending.append(fn)           # 官方: class loader 未就绪则推迟
        return "deferred"
    def performNow(self, fn): fn()
    def set_class_loader_ready(self):      # 模拟 app classloader 挂载
        self._loader.ready = True
        while self._pending: self._pending.pop(0)()
    def use(self, className):
        klass = self._loader.find(className)
        return KlassWrapper(klass, self._loader)
    def retain(self, inst):                 # 复制包装供替换函数外留存
        self._retain_pool.append(inst)
        return inst
    def cast(self, handle, wrapper):       # 裸指针->包装(这里直接收实例)
        assert isinstance(handle, Instance)
        return Instance(wrapper._klass, handle._fields)

class KlassWrapper:
    def __init__(self, klass, loader): self._klass, self._loader = klass, loader
    def __getattr__(self, name):            # method 访问 -> MethodHandle; $new/$dispose 特判
        if name == "$new": return self._ctor_new
        if name == "$dispose": return lambda inst: None
        return self._klass.handle(name)
    def _ctor_new(self, *args):             # $new 构造器
        inst = Instance(self._klass, {})
        self._klass._ctor(inst, *args)
        return inst

def J(name):  # 语法糖: 类名直取包装
    return JAVA.use(name)

# ---------------- 目标 App 模拟 ----------------
LOADER = ClassLoader()
ACTIVITY = Klass("android.app.Activity")
ACTIVITY.method("onResume", [], lambda this: this._fields.setdefault("log", []).append("orig-onResume") or "RESUMED")
def _on_create(this, bundle): this._fields["bundle"] = bundle; return None
def _on_create0(this): this._fields["bundle"] = None; return None
ACTIVITY.method("onCreate", ["android.os.Bundle"], _on_create)
ACTIVITY.method("onCreate", [], _on_create0)
STRING = Klass("java.lang.String")
EXCEPTION = Klass("java.lang.Exception")
LOADER.register(ACTIVITY); LOADER.register(STRING); LOADER.register(EXCEPTION)
JAVA = JavaNS(LOADER)

# ---------------- 断言 ----------------
def check(label, cond):
    print(("PASS" if cond else "FAIL"), "-", label)
    assert cond, label

def main():
    # 1. available 门控 + perform 在类加载器就绪前推迟
    check("1 Java.perform 未就绪时推迟", JAVA.perform(lambda: None) == "deferred")
    seen = []
    JAVA.perform(lambda: seen.append("ran"))
    JAVA.set_class_loader_ready()
    check("2 类加载器就绪后冲刷执行", seen == ["ran"])

    # 2. implementation 替换: 分发进 hook, this.onResume() 走原始实现
    inst = JAVA.use("android.app.Activity")._ctor_new()
    hook_events = []
    def hook_on_resume(this):
        BUS.send("onResume() got called! Let's call the original implementation")
        hook_events.append("hook")
        return this.call_original("onResume")
    JAVA.use("android.app.Activity").onResume.implementation = hook_on_resume
    r = inst.onResume()
    check("3 替换后仍返回原始实现结果", r == "RESUMED")
    check("4 消息已发往宿主且先于原始执行", BUS.recv().startswith("onResume() got called") and hook_events == ["hook"])
    check("5 原始实现副作用发生(未递归)", inst._fields["log"] == ["orig-onResume"])

    # 3. 重载: 显式选择, 未选重载不受影响
    b = object()  # 模拟 Bundle 实例
    inst2 = JAVA.use("android.app.Activity")._ctor_new()
    JAVA.use("android.app.Activity").onCreate.overload().implementation = \
        lambda this: this._fields.__setitem__("bundle", "EMPTY") or "EMPTY"
    check("6 未 hook 的重载照常", inst2.onCreate(b) is None and inst2._fields["bundle"] is b)
    check("7 已 hook 重载走替换", inst2.onCreate() == "EMPTY")

    # 4. 多重载方法无 .overload 直接替换 -> 歧义报错
    try:
        JAVA.use("android.app.Activity").onCreate.implementation = lambda this: None
        amb = False
    except RuntimeError: amb = True
    check("8 多重载直接替换报歧义错", amb)

    # 5. $new 构造 / retain / cast
    inst3 = JAVA.use("android.app.Activity")._ctor_new()
    keep = None
    JAVA.use("android.app.Activity").onResume.implementation = lambda this: JavaNS and (globals().__setitem__("_keep", JAVA.retain(this)) or this.call_original("onResume"))
    inst3.onResume()
    check("9 Java.retain 留存实例可用", globals()["_keep"] is inst3 and isinstance(globals()["_keep"], Instance))
    w = JAVA.use("android.app.Activity")
    check("10 Java.cast 包装为类实例", isinstance(JAVA.cast(globals()["_keep"], w), Instance))

    # 6. 替换函数抛异常向外传播(官方 throw Exception.$new 语义)
    JAVA.use("android.app.Activity").onResume.implementation = lambda this: (_ for _ in ()).throw(ValueError("Oh noes!"))
    try: inst3.onResume(); raised = False
    except ValueError: raised = True
    check("11 implementation 抛异常向外传播", raised)

    # 7. 未注册类 -> ClassNotFoundException
    try: JAVA.use("com.example.NotFound"); nf = False
    except ClassNotFoundException: nf = True
    check("12 未知类抛 ClassNotFoundException", nf)
    print("ALL 12 CHECKS PASSED")

if __name__ == "__main__":
    main()

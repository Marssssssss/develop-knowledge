# -*- coding: utf-8 -*-
"""Frida Interceptor(inline hook)语义模型。

模拟对象(依据 frida.re/docs/javascript-api 官方语义):
  Interceptor.attach(onEnter/onLeave, args 可读写, retval.replace, this 上下文)
  Interceptor.replace + NativeFunction 链回原实现(绕过 hook)
  Module.getExportByName / findExportByName / Thumb LSB
  pending patch -> flush()(send() 时自动) / revert / detachAll
口径声明: 真实实现按指令重定位函数序言,本模型搬定长字节、不做指令解码;
          开销数字(6us/11us, iPhone 5S)为官方文档口径,模拟用"编组操作数"代理。
跑法: python interceptor_sim.py -> 10 项断言
"""
import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# 官方基准(iPhone 5S): 只挂 onEnter ~6us; onEnter+onLeave ~11us
OVERHEAD_US = {"onEnter": 6, "onEnter+onLeave": 11}
MARSHAL_OPS = {"js": 0, "cmodule": 0}   # 编组操作计数(js 路径每个参数+1)

# ---------------- 消息通道: send() 自动 flush ----------------
def send(payload):
    flush_all()
    HOST_INBOX.append(payload)
HOST_INBOX = []

# ---------------- NativePointer / retval 包装 ----------------
class NativePointer:
    def __init__(self, v): self.v = v
    def toInt32(self): return self.v
    def readUtf8String(self): return "ptr(%d)" % self.v

class Retval:
    def __init__(self, v): self.v, self.replaced = v, None
    def replace(self, v): self.replaced = v      # 官方: retval.replace(1337)
    def toInt32(self): return self.replaced if self.replaced is not None else self.v
    # 注意: 官方警告 retval 对象跨 onLeave 复用,勿存到回调外——模型不模拟复用

# ---------------- Module ----------------
class Module:
    def __init__(self, name, base=0x1000, thumb=False):
        self.name, self.base, self.thumb = name, base, thumb
        self.exports = {}
    def add_export(self, sym, offset, func):
        self.exports[sym] = (self.base + offset, func)
        return self.base + offset
    def getExportByName(self, sym):             # 找不到抛异常
        if sym not in self.exports: raise KeyError(sym)
        addr, _ = self.exports[sym]
        return addr | (1 if self.thumb else 0)  # 32 位 ARM: Thumb 函数 LSB=1
    def findExportByName(self, sym):            # 找不到返回 None
        try: return self.getExportByName(sym)
        except KeyError: return None

# ---------------- NativeFunction / NativeCallback ----------------
class NativeFunction:                           # 预先包装的"原实现"入口(replace 里链回用)
    def __init__(self, func): self._orig = func
    def __call__(self, *args):
        return self._orig(*args)                # 绕过 hook 直达原实现

class NativeCallback:                           # replace 的替换实现容器
    def __init__(self, fn): self.fn = fn

# ---------------- Interceptor 核心 ----------------
HOOKED = []                                     # 全局 hook 注册表(detachAll 用)

class NativeFunc:
    def __init__(self, name, body):
        self.name, self.body = name, body
        self.pending = None                     # 未生效的 attach patch
        self.hook = None                        # 生效中的 {onEnter, onLeave}
        self.replacement = None                 # 生效中的 replace
        self.calls = []                         # 观测记录
    def attach(self, callbacks):
        if isinstance(callbacks, NativeCallback):
            raise TypeError("replace 用 Interceptor.replace")
        self.pending = callbacks                # patch 入队,flush 才写内存
        HOOKED.append(self)
        return self
    def replace(self, cb):                      # 整函数替换
        self.replacement, self.hook, self.pending = cb, None, None
        HOOKED.append(self)
        return NativeFunction(self.body)        # 返回"调原实现"句柄(简化: replaceFast 语义另见 README)
    def revert(self):
        self.hook = self.pending = self.replacement = None
    def flush(self):
        if self.pending is not None:
            self.hook, self.pending = self.pending, None
    # ---- 调用分发(模拟 CPU 走到函数入口) ----
    def __call__(self, *args, _cmodule=False):
        if self.replacement is not None:
            return self.replacement.fn(*args)
        if self.hook is None:
            return self.body(*args)
        return self._hook_stub(list(args), _cmodule)
    def _hook_stub(self, raw_args, cmodule):
        cb = self.hook
        ctx = {"returnAddress": 0xBEEF, "threadId": 42, "depth": 1, "pc": 0, "sp": 0}
        # 编组: JS 路径每个参数包一次 NativePointer;CModule 快通道零编组
        if not cmodule:
            args = [NativePointer(a) for a in raw_args]
            MARSHAL_OPS["js"] += len(args)
        else:
            args = list(raw_args); MARSHAL_OPS["cmodule"] += 0
        if cb.get("onEnter"):
            cb["onEnter"](_ArgsView(args, ctx))
        real = [a.v if isinstance(a, NativePointer) else a for a in args]
        ret = self.body(*real)                  # trampoline: 搬走的 prologue + 原函数体
        if cb.get("onLeave"):
            rv = Retval(ret)
            cb["onLeave"](rv)
            ret = rv.toInt32()
        self.calls.append((real, ret))
        return ret

class _ArgsView:                                # onEnter(this) 绑定: args + 上下文
    def __init__(self, args, ctx): self.args, self.ctx = args, ctx
    def __getitem__(self, i): return self.args[i]
    def __setitem__(self, i, v): self.args[i] = v   # 官方: args 可写

def flush_all():
    for f in HOOKED: f.flush()
def detachAll():
    for f in HOOKED: f.revert()

class Interceptor:
    @staticmethod
    def attach(target, callbacks): return target.attach(callbacks)
    @staticmethod
    def replace(target, cb): return target.replace(cb)
    @staticmethod
    def revert(target): target.revert()
    @staticmethod
    def flush(): flush_all()
    @staticmethod
    def detachAll(): detachAll()

# ---------------- 目标程序模拟 ----------------
libc = Module("libc.so")
def read_body(fd, buf, count): return count            # ssize_t read(fd, buf, count)
read_fn = NativeFunc("read", read_body)
READ_ADDR = libc.add_export("read", 0x100, read_fn)
open_fn = NativeFunc("open", lambda path, flags: 3)    # int open(path, flags)
OPEN_ADDR = libc.add_export("open", 0x200, open_fn)

# ---------------- 断言 ----------------
def check(label, cond):
    print(("PASS" if cond else "FAIL"), "-", label)
    assert cond, label

def main():
    # 1. 导出符号解析
    check("1 getExportByName 解析地址", libc.getExportByName("read") == READ_ADDR)
    check("2 findExportByName 未命中返回 None", libc.findExportByName("frotz") is None)
    thumb_mod = Module("libthumb.so", base=0x9000, thumb=True)
    tf = NativeFunc("t", lambda: 0)
    taddr = thumb_mod.add_export("t", 0x10, tf)
    check("3 Thumb 函数地址 LSB=1", thumb_mod.getExportByName("t") == (taddr | 1))

    # 2. attach: patch 在 flush 前不生效
    seen = []
    Interceptor.attach(read_fn, {"onEnter": lambda a: seen.append(("enter", a[0].toInt32()))})
    n = read_fn(7, 0x2000, 100)
    check("4 flush 前调用未经过 hook", seen == [] and n == 100)
    send({"stage": "flushed"})                    # send() 自动 flush(官方语义)
    n = read_fn(7, 0x2000, 100)
    check("5 flush 后 hook 生效", seen == [("enter", 7)] and n == 100)

    # 3. onEnter 改参数 + onLeave 改返回值
    Interceptor.revert(read_fn)
    Interceptor.attach(read_fn, {
        "onEnter": lambda a: a.__setitem__(0, 99),          # fd 7 -> 99
        "onLeave": lambda rv: rv.replace(1337),              # 返回值改 1337
    })
    send({}); n = read_fn(7, 0x2000, 100)
    check("6 onEnter 改参传播到原函数", read_fn.calls[-1][0][0] == 99)
    check("7 onLeave retval.replace 生效", n == 1337)

    # 4. onEnter this 上下文
    Interceptor.revert(read_fn)
    ctx_seen = []
    def on_enter(a): ctx_seen.append((a.ctx["returnAddress"], a.ctx["threadId"], a.ctx["depth"]))
    Interceptor.attach(read_fn, {"onEnter": on_enter}); send({})
    read_fn(1, 2, 3)
    check("8 this 上下文含 returnAddress/threadId/depth", ctx_seen == [(0xBEEF, 42, 1)])

    # 5. replace + NativeFunction 链回原实现(不递归)
    Interceptor.revert(read_fn); read_fn.calls.clear()
    orig_calls = []
    def read_body_count(fd, buf, count):
        orig_calls.append(fd); return count + 1
    read_fn.body = read_body_count
    orig = NativeFunction(read_fn.body)                  # 替换前包装原实现
    def my_read(fd, buf, count):
        send('log: read called, chaining to original')
        return orig(fd, buf, count)                      # 官方: 经 NativeFunction 直达原实现
    Interceptor.replace(read_fn, NativeCallback(my_read))
    n = read_fn(5, 0, 10)
    check("9 replace 链回原实现且只执行一次(无递归)", n == 11 and orig_calls == [5])

    # 6. CModule 快通道: 编组操作数少于 JS 路径; 官方开销口径
    Interceptor.revert(read_fn); MARSHAL_OPS["js"] = 0
    Interceptor.attach(read_fn, {"onEnter": lambda a: None}); send({})
    read_fn(1, 2, 3)                                    # JS 路径: 3 个参数 3 次编组
    js_ops = MARSHAL_OPS["js"]
    read_fn._hook_stub([1, 2, 3], cmodule=True)         # CModule 路径: 0 次
    check("10 CModule 编组开销低于 JS 路径", MARSHAL_OPS["cmodule"] == 0 and js_ops == 3)
    print("官方基准(iPhone 5S): onEnter-only %dus < onEnter+onLeave %dus" %
          (OVERHEAD_US["onEnter"], OVERHEAD_US["onEnter+onLeave"]))
    print("ALL 10 CHECKS PASSED")

if __name__ == "__main__":
    main()

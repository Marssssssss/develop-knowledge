# -*- coding: utf-8 -*-
"""
Python · pickle 与对象序列化协议

围绕 __reduce__ 六元组契约逐项对拍:默认 state 的四种形态、memo 身份保持、
按引用序列化全局对象、copyreg / dispatch_table 两条注册通道、
PEP 574 带外缓冲,以及 pickletools 反汇编出的字节码事实。
演示类必须定义在模块顶层:按引用序列化要求类能以限定名被找到。

参考(实读):
  - https://docs.python.org/3/library/pickle.html      (reduce 契约/state 四态/memo/persistent_id)
  - https://docs.python.org/3/library/copyreg.html     (constructor / pickle 注册)
  - https://peps.python.org/pep-0307/                  (protocol 2: __getnewargs_ex__/__getstate__)
  - https://peps.python.org/pep-0574/                  (protocol 5: PickleBuffer 带外缓冲)
  - 本机 CPython 3.12 Lib/pickle.py 与 pickletools.py
"""

import copyreg
import io
import pickle
import pickletools

PASS = []

def ok(msg):
    PASS.append(msg)
    print(f"  [ok] {msg}")

def ops_of(data):
    return [op.name for op, _arg, _pos in pickletools.genops(data)]

# ---- 演示类(模块顶层,保证限定名可回查) ----

class P1Plain:
    def __init__(self):
        self.x = 42

class P1Six:
    calls = []

    def __reduce__(self):
        return (P1Six, (), {"x": 1}, None, None, self._setter)

    @staticmethod
    def _setter(obj, state):
        P1Six.calls.append(("setter", state))
        obj.x = state["x"] + 100

class P1SetState:
    def __init__(self):
        self.v = 0

    def __reduce__(self):
        return (P1SetState, (), {"v": 7})

    def __setstate__(self, state):
        self.v = state["v"] * 10

class P1StringReduce:
    def __reduce__(self):
        return "P1_SINGLETON"

P1_SINGLETON = P1StringReduce()

class P1NoDictNoSlots:
    __slots__ = ()

class P1DictOnly:
    pass

class P1SlotsOnly:
    __slots__ = ("a",)

class P1DictAndSlots:
    __slots__ = ("b", "__dict__")

class P1Node:
    pass

class P1Custom:
    def __reduce__(self):
        return (P1Custom, ())

class P1NewArgs:
    """自定义 __new__ → __getnewargs_ex__ 的 kwargs 真正进入流。"""

    def __new__(cls, fresh=False, tag="t"):
        obj = super().__new__(cls)
        obj.made_with = (fresh, tag)
        return obj

    def __getnewargs_ex__(self):
        return (), {"fresh": True, "tag": "x"}

    def __getstate__(self):
        return None  # 不落 state,__new__ 参数是唯一真相

class P1NewArgsDefaultNew:
    """默认 object.__new__ → kwargs 被丢弃,降级为 REDUCE 直接调用类。"""

    def __init__(self, fresh=None):
        self.fresh = fresh

    def __getnewargs_ex__(self):
        return (), {"fresh": True}

class P1Overwritten(P1NewArgs):
    """不屏蔽 state:BUILD 会把实例 dict 盖回 __new__ 的产出。"""

    def __getstate__(self):
        return self.__dict__

class P1C:
    def __init__(self, a):
        self.a = a

class P1Rec:
    def __init__(self, key):
        self.key = key

def demo_protocol_family():
    assert pickle.DEFAULT_PROTOCOL == 4 and pickle.HIGHEST_PROTOCOL == 5
    ok("3.12 默认协议 4、最高 5(3.14 起默认才是 5,读文档须对版本)")
    for proto in range(6):
        assert pickle.loads(pickle.dumps({"k": [1, 2]}, proto)) == {"k": [1, 2]}
    ok("协议 0~5 全部往返一致")

    p0 = pickle.dumps(P1Plain(), 0)
    assert b"\x80" != p0[:1] and b"__main__" in p0
    p2 = pickle.dumps(P1Plain(), 2)
    assert p2[:2] == b"\x80\x02"
    ok("协议 0 是明文(含模块名可读),协议 2 起以 PROTO 头 \\x80\\x02 开头")

    def local_cls():
        class Hidden:
            pass
        return Hidden
    try:
        pickle.dumps(local_cls()(), 2)
    except AttributeError:
        ok("局部类限定名带 <locals> 查不回 → 按引用序列化的第一道门槛")

def demo_reduce_contract():
    r = pickle.loads(pickle.dumps(P1Six()))
    assert r.x == 101 and P1Six.calls == [("setter", {"x": 1})]
    ok("第 6 项 (obj,state) 回调优先于 __setstate__(3.8 新增)")

    assert pickle.loads(pickle.dumps(P1SetState())).v == 70
    ok("有 __setstate__ 时 state 可以是任意对象,由它全权解释")

    assert pickle.loads(pickle.dumps(P1_SINGLETON)) is P1_SINGLETON
    ok("__reduce__ 返回字符串 = 在**对象自身模块**里按名取全局(点分路径反而查不到)")
    try:
        pickle.dumps(P1StringReduce())
    except pickle.PicklingError as e:
        assert "not the same object" in str(e)
    ok("字符串形式只认『对象即全局名本体』,普通实例借道单例名会被 PicklingError 拒绝")

def demo_state_forms():
    assert P1NoDictNoSlots().__getstate__() is None
    d = P1DictOnly(); d.z = 3
    assert d.__getstate__() == {"z": 3}
    s = P1SlotsOnly.__new__(P1SlotsOnly); s.a = 5
    assert s.__getstate__() == (None, {"a": 5})
    m = P1DictAndSlots(); m.b = 1; m.y = 2
    assert m.__getstate__() == ({"y": 2}, {"b": 1})
    ok("None / __dict__ / (None,slots) / (dict,slots) 四态(3.11 起 object 才有默认 __getstate__)")

    back = pickle.loads(pickle.dumps(m))
    assert back.b == 1 and back.y == 2 and hasattr(back, "__dict__")
    ok("『dict+slots 共存』要把 __dict__ 显式写进 __slots__;往返后两路状态都恢复")
    back2 = pickle.loads(pickle.dumps(s))
    assert not hasattr(back2, "__dict__") and back2.a == 5
    ok("slots-only 实例往返后仍无 __dict__,值经 BUILD 逐槽恢复")

def demo_memo_and_byref():
    n = P1Node()
    round = pickle.loads(pickle.dumps([n, n, [n]]))
    assert round[0] is round[1] is round[2][0]
    lst = [1]; lst.append(lst)
    rec = pickle.loads(pickle.dumps(lst))
    assert rec[1] is rec
    ok("共享对象按 memo 恢复同一身份;自引用结构也不死循环")

    ops2 = ops_of(pickle.dumps([P1Node(), P1Node()], 2))
    assert ops2.count("BINPUT") == 4 and ops2.count("BINGET") == 1
    ops4 = ops_of(pickle.dumps([P1Node(), P1Node()], 4))
    assert ops4.count("MEMOIZE") == 6 and ops4.count("BINGET") == 1
    ok("记账指令按协议分代:BINPUT(2/3) → MEMOIZE(4);读回统一 BINGET(共享即少一次构造)")

    try:
        pickle.dumps(lambda x: x)
    except AttributeError:
        ok("lambda 找不到限定名 → AttributeError『local object』(按引用机制的代价)")

def demo_newobj_vs_reduce():
    ops = ops_of(pickle.dumps(P1Plain(), 2))
    assert "NEWOBJ" in ops and "BUILD" in ops
    ok("默认 __reduce_ex__(2) 走 copyreg.__newobj__ → NEWOBJ 建对象 + BUILD 放 state")

    ops = ops_of(pickle.dumps(P1Custom(), 2))
    assert "REDUCE" in ops and "NEWOBJ" not in ops
    ok("自定义 __reduce__ 返回可调用元组 → REDUCE 直接调用构造器")

    made = pickle.loads(pickle.dumps(P1NewArgs())).made_with
    assert made == (True, "x")
    assert "NEWOBJ_EX" in ops_of(pickle.dumps(P1NewArgs()))
    ok("自定义 __new__ + kwargs → NEWOBJ_EX 重建(有 kwargs 的专用指令,协议 4+)")

    ov = pickle.loads(pickle.dumps(P1Overwritten()))
    assert ov.made_with == (False, "t")
    ok("BUILD 的 state 优先于 NEWOBJ(_EX) 的 __new__ 产出——不屏蔽 dict 时参数重建会被盖掉")

    d = P1NewArgsDefaultNew()
    back = pickle.loads(pickle.dumps(d))
    assert back.fresh is None and "NEWOBJ" not in ops_of(pickle.dumps(d, 2))
    ok("默认 object.__new__ 时 kwargs 被整条丢弃(降级 REDUCE 直调类,__init__ 收默认值)")

def demo_registration_channels():
    copyreg.pickle(P1C, lambda c: (P1C, (c.a,)))
    assert pickle.loads(pickle.dumps(P1C(3))).a == 3
    ok("copyreg.pickle(type, fn) 注册全局还原函数")

    class MyPickler(pickle.Pickler):
        dispatch_table = {**copyreg.dispatch_table,
                          P1C: lambda c: (P1C, (c.a * 1000,))}

    buf = io.BytesIO()
    MyPickler(buf).dump(P1C(3))
    assert pickle.loads(buf.getvalue()).a == 3000
    ok("Pickler.dispatch_table 只影响该 pickler,不动全局注册表")

    copyreg.constructor(P1C)
    try:
        copyreg.constructor(42)
    except TypeError:
        ok("copyreg.constructor 校验可调用性,非可调用对象抛 TypeError")

def demo_out_of_band():
    payload = bytearray(b"abcdefgh" * 64)
    pb = pickle.PickleBuffer(payload)

    round = pickle.loads(pickle.dumps(pb, 5))
    assert isinstance(round, bytearray) and round == payload
    ok("协议 5 无回调时 PickleBuffer 原地降级为主流内嵌字节")

    try:
        pickle.dumps(pb, 4)
    except pickle.PicklingError as e:
        assert "protocol >= 5" in str(e)
        ok("协议 <5 序列化 PickleBuffer 直接 PicklingError")

    buffers = []
    blob = pickle.dumps(["meta", pb], 5, buffer_callback=buffers.append)
    assert len(buffers) == 1 and isinstance(buffers[0], pickle.PickleBuffer)
    assert b"abcdefgh" not in blob
    restored = pickle.loads(blob, buffers=buffers)
    assert restored[0] == "meta" and bytes(restored[1]) == b"abcdefgh" * 64
    ok("buffer_callback 把大缓冲分离出主流,loads 时按序回填 → 零拷贝通道")

def demo_persistent_id():

    class DBPickler(pickle.Pickler):
        def persistent_id(self, obj):
            return ("Rec", obj.key) if isinstance(obj, P1Rec) else None

    class DBUnpickler(pickle.Unpickler):
        db = {7: P1Rec(7)}

        def persistent_load(self, pid):
            tag, key = pid
            assert tag == "Rec"
            return self.db[key]

    buf = io.BytesIO()
    DBPickler(buf).dump(P1Rec(7))
    assert DBUnpickler(io.BytesIO(buf.getvalue())).load().key == 7
    ok("persistent_id/persistent_load 把『值序列化』替换成『数据库引用』")

def main():
    demo_protocol_family()
    demo_reduce_contract()
    demo_state_forms()
    demo_memo_and_byref()
    demo_newobj_vs_reduce()
    demo_registration_channels()
    demo_out_of_band()
    demo_persistent_id()
    print(f"\n共 {len(PASS)} 项断言全部通过")

if __name__ == "__main__":
    main()

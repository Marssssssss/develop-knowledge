# pickle 与对象序列化协议

> pickle 不是"把对象变字节"的黑盒,而是一台**栈式虚拟机**:流里是一串指令(REDUCE/NEWOBJ/BUILD/MEMOIZE...),
> 反序列化就是逐指令执行。理解指令,才能理解 `__reduce__` 契约、memo 身份保持、按引用序列化与 PEP 574 带外缓冲。

## 1. 协议族:0~5 各是什么

| 协议 | 引入 | 形态 | 关键点 |
| --- | --- | --- | --- |
| 0 | 2.x | 人类可读文本 | 明文含模块名;`PUT`/`GET` 记账 |
| 1 | 2.x | 旧二进制 | `BINPUT`/`BINGET` 短索引 |
| 2 | PEP 307 | 新二进制 | `NEWOBJ`/`BUILD`、`__getnewargs_ex__` |
| 3 | 3.0 | 字节原生 | Python 3 专用 |
| 4 | PEP 3154(3.4) | 帧 + 短名 | `FRAME`、`MEMOIZE`、`SHORT_BINUNICODE`、`NEWOBJ_EX` |
| 5 | PEP 574(3.8) | 带外缓冲 | `PickleBuffer` + `buffer_callback` |

**版本敏感**:本机 3.12 `DEFAULT_PROTOCOL=4`;docs.python.org/3(当前 3.14)文档已写"默认 5"。
**3.11 起** `object` 才有默认 `__getstate__` 实现。

## 2. `__reduce__` 六元组契约(核心 API)

```python
def __reduce__(self):
    return (callable, args, state, listitems, dictitems, state_setter)
```

- **callable + args**:重建起点(通常 `cls` 本身或 `copyreg.__newobj__`);
- **state**:交给 `__setstate__`;没有它时必须是 dict,直接 `update` 进 `__dict__`;
- **state_setter**(第 6 项,3.8+):`(obj, state)` 回调,**优先于 `__setstate__`**;
- **返回字符串** = 按**对象自身模块**里的一个全局名取值——是单例惯用法,但有两个反直觉点:
  - 字符串不是点分路径,`"os.sep"` 会被当成 `__main__` 模块下名叫 `os.sep` 的属性 → 查不到;
  - pickle 校验"被序列化对象 is 该全局名指向的本体",普通实例借单例名会被
    `PicklingError: it's not the same object` 拒绝,防止身份被静默改写。

## 3. 默认 state 的四种形态(3.11+ 默认 `__getstate__`)

| 类形态 | `__getstate__()` |
| --- | --- |
| 无 dict 无 slots | `None` |
| 有 dict 无 slots | `self.__dict__` |
| 有 slots 无 dict | `(None, {slot名: 值})` |
| 两者都有 | `(dict, {slot名: 值})` |

要"dict + slots 共存",必须把 `'__dict__'` 显式写进 `__slots__`。
slots-only 实例往返后**仍然没有** `__dict__`,值由 `BUILD` 逐槽恢复。

## 4. memo:身份保持靠记账

- 同一对象多次出现 → 只序列化一次,后面引用 memo 下标 → **恢复后 `is` 同一对象**;
- 自引用结构(`l.append(l)`)不死循环,同理;
- 记账指令按协议分代:协议 2/3 `BINPUT`、协议 4 `MEMOIZE`;读回统一 `BINGET`。

## 5. 按引用序列化:类与函数只存"限定名"

实例还原 = 反查 `module.qualname` 再执行指令。三个直接后果:

1. **局部类/lambda 序列化失败**(限定名带 `<locals>`)——注意 lambda 抛的是 `AttributeError`;
2. 代码改了(改名/删字段)旧流可能**反序列化出错误结果**或 `AttributeError`;
3. **安全**:反序列化就是执行指令流,恶意流 = 任意代码执行。永远不要 `loads` 不可信数据。

## 6. NEWOBJ vs REDUCE:两种重建路径

- 默认 `__reduce_ex__(2)` 走 `copyreg.__newobj__` → 字节码 **NEWOBJ**(调 `cls.__new__`)+ **BUILD**(放 state);
- 自定义 `__reduce__` 返回 `(cls, args)` → **REDUCE**(直接调用 `cls(*args)`,即 `__new__` + `__init__` 全跑);
- `__getnewargs_ex__` 的 **kwargs 只有在类自定义了 `__new__` 时才真正进入流**:
  - 自定义 `__new__` + 协议 4+ → 专用指令 **NEWOBJ_EX**;
  - 默认 `object.__new__` → reduce_2 直接**丢弃 kwargs**,降级为 REDUCE 调类,`__init__` 只收到默认值;
- **BUILD 的 state 优先于 NEWOBJ(_EX) 的 `__new__` 产出**:实例 dict 里同名字段会盖掉参数重建结果;
  想让 `__new__` 参数成为唯一真相,需 `__getstate__` 返回 `None`。

## 7. 两条注册通道:copyreg 与 dispatch_table

```python
copyreg.pickle(MyClass, reduce_fn)                 # 全局注册
class MyPickler(pickle.Pickler):
    dispatch_table = {**copyreg.dispatch_table,    # 仅本 pickler 生效
                      MyClass: other_reduce_fn}
```

`copyreg.constructor(obj)` 只做可调用性校验(不可调用抛 `TypeError`)。

## 8. PEP 574 带外缓冲(协议 5)

```python
buffers = []
blob = pickle.dumps(["meta", PickleBuffer(big)], 5,
                    buffer_callback=buffers.append)
restored = pickle.loads(blob, buffers=buffers)
```

- 主流里只剩引用,`b"abcdefgh"` 等大块字节**不在** pickle 流内 → 零拷贝/共享内存通道;
- 无 `buffer_callback` 时 `PickleBuffer` 原地降级为内嵌字节;
- 协议 <5 序列化 `PickleBuffer` 直接 `PicklingError`。

## 9. persistent_id:把"值"换成"引用"

`Pickler.persistent_id(obj)` 返回非 None → 流里只写 `(tag, key)` 这类 ID,
`Unpickler.persistent_load(pid)` 负责从数据库/缓存取回本体。ORM 与缓存层的标准姿势。

## 踩坑与反直觉清单

1. 字符串 reduce 不是路径,是"对象自己模块里的全局名",且要求对象即本体;
2. `MEMOIZE` 是协议 4 才有的,协议 2 用 `BINPUT`——用 `pickletools.genops` 对拍别记错指令名;
3. kwargs + 默认 `__new__` = 静默丢弃,不是报错——**静默降级**比异常更危险;
4. docs.python.org/3 是最新版文档,版本敏感结论(默认协议号)要与本机对拍;
5. `dict+slots` 共存要显式写 `__slots__ = ("b", "__dict__")`。

## 自检

`python main.py` —— 26 项断言覆盖以上全部机制(协议族 / 六元组 / state 四态 / memo /
按引用 / NEWOBJ 族与 BUILD 优先级 / 注册通道 / 带外缓冲 / persistent_id)。

## 参考资料(实读)

- [pickle — Python object serialization](https://docs.python.org/3/library/pickle.html)
- [copyreg — Register pickle support functions](https://docs.python.org/3/library/copyreg.html)
- [PEP 307 — Extensions to the pickle protocol](https://peps.python.org/pep-0307/)
- [PEP 3154 — Pickle protocol 4](https://peps.python.org/pep-3154/)(MEMOIZE/FRAME 出处)
- [PEP 574 — Pickle protocol 5 with out-of-band data](https://peps.python.org/pep-0574/)
- 本机 CPython 3.12 `Lib/pickle.py`、`Lib/pickletools.py`(genops 指令名以实跑为准)

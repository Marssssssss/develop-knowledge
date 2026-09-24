# 缓冲协议(PEP 3118)与 memoryview

> `bytes`/`bytearray`/`array`/`numpy` 数组……这些对象的共同点是实现了**缓冲协议**:
> 暴露一块带格式说明的原始内存。`memoryview` 是这套协议的用户侧窗口——
> **不拷贝**地读写、按格式重解释、管理导出生命周期。numpy/struct/零拷贝网络 IO 全踩在这块地基上。

## 1. 视图即窗口

| 属性 | 含义 | `memoryview(bytearray(b"abcdef"))` |
| --- | --- | --- |
| `nbytes` / `itemsize` / `len()` | 总字节 / 单元素字节 / 元素个数 | 6 / 1 / 6 |
| `shape` / `strides` / `ndim` | 维度信息 | (6,) / (1,) / 1 |
| `format` / `readonly` | struct 格式符 / 只读标志 | 'B' / False |
| `suboffsets` | PIL 式间接层的步长 | `()`(简单缓冲无间接层) |
| `obj` | 指回导出对象 | 该 bytearray |

`array('h',[1,2,3,4])` 的视图:itemsize=2、nbytes=8、len=4,索引拿到的是**元素值** 2 而非字节串。
(`buffer_info()` 出口方自描述要 3.13+ 才有。)

## 2. 零拷贝读写

- `mv[0] = 90` 直通底层缓冲;切片 `mv[1:4]` 是**共享同一内存**的子视图(`sub.obj is b`),
  改子视图就是改原对象;
- 比较按内容:`memoryview(b"abc") == b"abc"`、跨 bytes/bytearray 的两个视图也相等;
- `tobytes()` / `tolist()` / `bytes(mv)` 才产生拷贝。

## 3. resize 闸门与生命周期(出口方契约)

```python
b = bytearray(b"abcdef"); mv = memoryview(b)
b.append(103)        # BufferError: Existing exports of data: object cannot be re-sized
```

- 只要**任一**视图存活(含子视图,导出按计数记账),出口方就不得移动/重分配内存;
- `mv.release()` 释放一份计数,**幂等**(重复 release 不报错);全部释放后闸门打开;
- release 后再使用 → `ValueError: operation forbidden on released memoryview`;
- `with memoryview(...) as mv:` 离开块自动 release——长生命周期的推荐姿势;
- `toreadonly()`:共享内存但拒绝写入(TypeError),给下游传"只许看"的窗口。

## 4. cast:同一字节流的格式重解释

```python
memoryview(b"\x01\x02\x03\x04\x05\x06\x07\x08").cast("h")
# → format 'h', len 4, [513, 1027, 1541, 2055](little-endian:0x0201=513)
```

- 长度按 itemsize 缩放;要求 **1-D + C 连续 + 字节对齐**,非法组合直接 ValueError;
- `struct.pack_into("<ii", mv, 0, 10, 20)`:struct 模块可直接写进视图——字节级写入通道。

## 5. 连续性

- 普通视图 C 连续;`[::2]`、`[::-1]` 等带步长切片的 strides 非单位 → `c_contiguous` 为 False;
- 不连续视图照样能读(tobytes 按逻辑顺序拼),但很多 C 扩展要求 contiguous,先转置/拷贝再传。

## 6. 只读视图可哈希

- 只读 + 格式 B/b/c 的 1-D 视图:`hash(m) == hash(m.tobytes())`,切片/步长切片同样成立
  ——这让 memoryview 能当 dict 键/进 set(零拷贝场景的散列凭据);
- 可写视图 hash → **ValueError**(注意不是 TypeError):可变内容没有稳定哈希。

## 7. 设计视角:PEP 3118 出口方/进口方契约

- 出口方(实现 `bf_getbuffer`/`bf_releasebuffer`):按 PyBUF_* 标志(shape/strides/writable…)
  决定给什么视图;有导出期间保证内存不失效——bytearray 的 BufferError 就是这条义务的执行;
- 进口方(C-API `PyObject_GetBuffer`):声明需要的标志,用完必须 `PyBuffer_Release`,
  否则导出计数泄漏、出口方永久锁死;
- Python 层用户拿到的是打包好的 memoryview,生命周期语义是同一套。

## 自检

`python main.py` —— 21 项断言:属性自描述 / 零拷贝与子视图共享 / 内容比较 /
resize 闸门与计数 / release 幂等与 with / cast 重解释与 pack_into /
连续性 / toreadonly / 只读哈希与可写 ValueError。

## 参考资料(实读)

- [memoryview — Python 文档(stdtypes)](https://docs.python.org/3/library/stdtypes.html#memoryview-type)
- [Buffer Protocol — Python/C API](https://docs.python.org/3/c-api/buffer.html)
- [PEP 3118 — Revising the Buffer Protocol](https://peps.python.org/pep-3118/)
- 本机 CPython 3.12 实测(3.14 版文档的版本敏感差异已在文中标注)

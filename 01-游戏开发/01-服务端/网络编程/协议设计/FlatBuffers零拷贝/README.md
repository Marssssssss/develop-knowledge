# FlatBuffers 线格式与零拷贝访问

## 简介

- FlatBuffers 是 Google 推出的序列化库:序列化结果本身就是一棵**可以用相对偏移寻址、就地(in-place)遍历**的二进制对象树,读取前**不需要 parse、不需要临时对象、不需要拷贝**;定位是移动游戏(内存与带宽受限)等最高性能场景(官方白皮书)。
- 与 Protobuf 的本质差异:Protobuf 收到 wire 数据后必须先解码成语言对象才能访问;FlatBuffers 直接在 buffer 上按 vtable 间接寻址读字段。
- 关键概念:
  - **vtable**:每个 table 附带的小元数据表(int16 数组),记录「字段 id → 字段在 table 内的字节偏移」;条目为 0 = 字段缺失,读默认值 —— 这是前后向兼容的基石。
  - **soffset / uoffset**:table 首字段是有符号 32 位偏移,**反向**指向自己的 vtable;string/vector/子表字段是无符号 32 位偏移,**正向**指向目标。
  - **back-to-front 构建**:底层对象(string、子表)先写在缓冲区高地址端,root 最后写在低地址端,因此 uoffset 永远为正,缓冲区构成 DAG 不可能有环。
  - **零拷贝访问**:读 string 只是把 `(地址, 长度)` 切成 view,不复制字节。

历史:Wouter van Oortmersson 为游戏场景设计,2014 年开源;white paper 明确说动机是「现代程序性能取决于内存,而不是指令」。

## 原理详解

### 一个 FooBar 的完整字节布局(flatcc 文档教学示例,本 demo 逐字节复现)

schema(字段 id 顺序):`meal:byte 枚举(id0,默认 Banana=-1)`、`density:long(deprecated, id1)`、`say:string(id2)`、`height:short(id3)`,数据 `{meal:42, say:"hello", height:-8000}`:

```
+0x0000  08 00 00 00        root uoffset(正) → root table @ 0x0008
+0x0004  'N' 'O' 'O' 'B'    可选文件标识符
+0x0008  e8 ff ff ff        table soffset = -0x18(补码) → vtable = 0x0008-(-24) = 0x0020
+0x000c  08 00 00 00        say 的 uoffset → string = 0x000c+8 = 0x0014
+0x0010  2a                 meal = 42(Orange)
+0x0011  00                 对齐填充
+0x0012  c0 e0              height = -8000(int16 小端 0xE0C0)
+0x0014  05 00 00 00        string 长度 = 5(不含终止符)
+0x0018  "hello" + 00       内容 + 零终止
+0x001e  00 00              填充至 4 对齐
+0x0020  0c 00              vtable[0] = vtable 自身长度 12
+0x0022  0c 00              vtable[1] = table 长度 12
+0x0024  08 00 00 00 04 00 0a 00   字段 0/1/2/3 的 table 内偏移: meal=+8, density=0(缺失!), say=+4, height=+0x0a
```

### 读一个字段的完整路径(这就是「零拷贝」的全部开销)

1. `buf[0..4]` → root uoffset,加到 0 上得 root table 地址 t;
2. `i32(t)` 读 soffset,`vtable = t - soffset`;
3. 查 `vtable + 4 + fid*2` 的 uint16 → 字段在 table 内偏移 off(**必须先比 vtable[0],越界视为字段不存在 → 返回 schema 默认值**,这正是新代码读旧数据的向前兼容路径);
4. off == 0 → 字段缺失 → 默认值;否则按类型在 `t + off` 直接读标量,或再解一次 uoffset 跳到 string/vector。

### vtable 与 schema 演进

- 加字段:新 vtable 在尾部多一条,旧代码查不到(超出 vtable[0])→ 默认值,向后兼容;删字段/废弃:条目写 0;多实例同布局共享同一 vtable(构建器去重,真实库中 N 个同结构对象只付一份 vtable)。
- 缺省值字段**不占存储**:写入值 == 默认值时 vtable 条目直接置 0。

### 对齐与约束(全部来自 flatcc binary-format 文档)

- 小端序;标量按自身大小对齐(1~8);table 对齐 4;vector 长度字段给出的是**元素个数**;string 是 `长度 + 内容 + \0`;
- 缓冲区上限 2 GB(`2^31-1`,保证 soffset 可用有符号表示);最小长度 8 字节(2×uoffset);
- table 内字段顺序**格式不规定**,只要求对齐 —— 所以 flatc(vtable 放 table 前,soffset 为正)与 flatcc(vtable 放末尾,soffset 为负)产物不同但都合法。

## 对比 / 选型

| 维度 | Protobuf | FlatBuffers |
| --- | --- | --- |
| 访问成本 | 先完整 parse 成对象,再访问 | 就地寻址,读取即若干次内存读 + 间接跳转 |
| 访问局部性 | 只需一个字段也要解整个消息 | 按字段懒访问,只碰用到的字节 |
| 字段可选性 | proto3 标量隐式默认 | vtable 条目 0 显式「缺失/默认」语义 |
| 演进方式 | 手工管理 field number | 弃用置 0,新字段追加 vtable 尾部 |
| 修改 buffer | 不行(需重新序列化) | 标量字段可就地 mutate(等长标量) |
| 典型场景 | 通用 RPC 载荷 | 游戏资源/配置、高频小消息、移动端 |

## 环境准备

- OS:任意;Python 3.8+(仅标准库 struct);Go 1.18+(仅标准库)。

## 运行方式

```bash
python3 python/main.py   # 断言全绿即通过(含与 flatcc 参考字节逐字节比对)
go run go/main.go        # 同一组断言的 Go 版
```

## 关键代码片段(Python)

```python
def field_offset(buf, t, fid):
    """vtable 查找:越界(旧数据)与条目 0(缺失)都返回 0 → 调用方取默认值"""
    vt = t - read_i32(buf, t)          # soffset 是『减去』,见原理第 2 步
    vtsize = read_u16(buf, vt)
    slot = 4 + fid * 2                 # 跳过 vtable_size/table_size 两个头条目
    if slot >= vtsize:                 # 字段 id 超出新旧任一方的 vtable → 视为缺失
        return 0
    return read_u16(buf, vt + slot)

def read_string(buf, t, fid, default=None):
    off = field_offset(buf, t, fid)
    if off == 0:
        return default
    s = t + off + read_u32(buf, t + off)   # uoffset 加在它自己的存储地址上
    n = read_u32(buf, s)
    return memoryview(buf)[s + 4 : s + 4 + n]  # view,零拷贝
```

构建器按 flatcc 布局组装 block(header→table→string→vtable),对同一布局的 vtable 做字节级缓存去重,两个字段齐全的子表共享一个 vtable 实例(断言 4 验证)。

## 性能与边界

- 访问单个标量字段 = 4 次小内存读 + 1 次间接寻址;官方 white paper 未给具体倍数数字,定性结论是「省掉 parse 的临时对象、分配与拷贝」,对移动端带宽/内存受限场景收益最大(白皮书原话:focus on mobile hardware... highest performance needs: games)。
- 2 GB 上限;信任外部 buffer 前必须跑 Verifier(检查偏移、对齐、范围) —— 本 demo 的 reader 做了 vtable 长度与最小长度检查,生产环境要用完整 verifier。

## 注意事项与常见坑

- **soffset 符号坑**:table→vtable 是 `t - soffset`,uoffset 是 `p + u`;flatc 与 flatcc 的 vtable 摆放位置相反,同一份逻辑必须同时接受正/负 soffset,本 demo 断言 1 复现的正是负值情形(`e8 ff ff ff`)。
- **越界 = 缺失**:查字段前先比 `vtable[0]`,否则新代码读旧 buffer 会读到 vtable 外的垃圾字节。
- **vector 长度是元素个数不是字节数**;string 长度不含终止符,但字节里确实有一个 `\0`。
- **field id 与 vtable 槽位**:deprecated 字段槽位保留(条目 0),后续字段 id 不前移,否则新旧读写错位。
- 本 demo 构建器为单 schema 教学实现(固定「uoffset 字段在前、标量按 id 序」布局以复现参考字节);真实 flatc 的 table 内字段顺序不保证与此一致,跨实现互操作只依赖 vtable,不依赖字段物理顺序。

## 参考资料(实际阅读过的权威来源)

- [FlatBuffers white paper](https://flatbuffers.dev/white_paper) — 官方动机与 table/vtable/struct 设计取舍(uoffset DAG、字段弃用、native vector/union 与 Protobuf 对比)。
- [flatcc: FlatBuffers Binary Format](https://github.com/dvidelabs/flatcc/blob/master/doc/binary-format.md) — 最详尽的线格式规范文档(vtable/soffset/uoffset/string/vector/对齐/2GB 上限),本 demo 的参考字节布局出处。
- [flatbuffers annotation 文档](https://flatbuffers.dev/annotation/) — 官方逐字节标注工具,其中 vtable 示例(VOffset16/UOffset32/SOffset32 内部类型)与本文布局一致。
- [google/flatbuffers include/flatbuffers/table.h](https://github.com/google/flatbuffers/blob/master/include/flatbuffers/table.h) — 官方 C++ 读取路径源码(GetVTable/GetOptionalFieldOffset 的越界即缺失逻辑)。

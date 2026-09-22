# 609 · Protobuf 线格式与 gRPC API 建模

> 目标：把「gRPC 为什么这么建模」拆成两层——**字节层**（Protobuf 线格式，为什么 tag 要按字段号编码、为什么 int64 负数占 10 字节）和**语义层**（proto3 字段号治理、gRPC 状态码分工）。
> 多语言：Python（可运行 + 断言自检）／Go（人工审查 + 结构校验，本机无 Go 工具链）。

## 目录

```
python/  wire.py（varint/ZigZag/tag/六种线类型/记录级解析）
         proto.py（字段号治理/reserved/MergeFrom/map）
         grpcstatus.py（17 个状态码与重试判定）
         main.py  selfcheck_protobuf.py  harness.py
go/      protobuf.go（线格式）  proto.go（字段号治理 + 状态判定）
```

运行：

```bash
python python/main.py
python python/selfcheck_protobuf.py     # 113 断言全绿
go run go/protobuf.go go/proto.go
```

## 一、为什么线格式里只有字段号，没有字段名

Protobuf 的 wire format **不传字段名**，每条记录只传一个 varint `tag`：

```
tag = (field_number << 3) | wire_type
```

低 3 位是线类型，剩下的是字段号。这个设计带来三个直接后果：

1. **字段号就是契约**。改字段名是安全的（线上字节不变），改字段号等于换协议。
2. **字段号决定 tag 的字节数**。proto3 指南明确：1~15 的 tag 占 **1 字节**，16~2047 占 **2 字节**。所以高频字段应压在 1~15 —— 这不是风格建议，是 tag 编码的直接算术结果。
3. **未知字段可被跳过**。解析器读到不认识的字段号，靠 `wire_type` 就知道该跳几个字节（`0/2` 读 varint、`1` 读 8 字节、`5` 读 4 字节），这是向后兼容的物理基础。

六种线类型：

| 值 | 名称 | 用于 | 载荷 |
|---|---|---|---|
| 0 | VARINT | int32/int64/uint32/uint64/sint/sint64/bool/enum | Base 128 varint |
| 1 | I64 | fixed64/sfixed64/double | 小端 8 字节 |
| 2 | LEN | string/bytes/嵌套消息/packed repeated | varint 长度 + 载荷 |
| 3 | SGROUP | group 开始（已废弃） | — |
| 4 | EGROUP | group 结束（已废弃） | — |
| 5 | I32 | fixed32/sfixed32/float | 小端 4 字节 |

## 二、varint 与 ZigZag：负数为何会「变胖」

Base 128 varint：每个字节**低 7 位**存数据、小端在前，**最高位（MSB）是续接位**。

```
150 = 0b10010110
   → 低7位 = 0010110，还有高位 → 字节 = 0x96（MSB=1）
   → 剩 1              → 字节 = 0x01
   → 96 01
```

`sint32/sint64` 用 ZigZag 把有符号数折成无符号：

```
ZigZag(n) = (n << 1) ^ (n >> (bits-1))      # 32 位用 31，64 位用 63
0→0   -1→1   1→2   -2→3   ...  -500→999
```

**关键代价**：`int32/int64`（非 sint）对负数按 **补码** 编码，`int64 x = -2` 的补码是 `0xFFFF...FE`，占满 **10 字节** varint；而 `sint64` 走 ZigZag 只要 1~2 字节。这直接对应官方建议：**字段可能取负值时用 `sintN`**。

`wire.py` 里 `to_signed64()` 演示了 64 位补码回读：varint 解出的是无符号，需要显式做 `-(v & 1)` 那一步还原。

## 三、packed repeated 的「必须拼接」约束

`repeated int32` 在 proto3 默认 **packed**：整个数组编码成**一条 LEN 记录**，载荷是各元素 varint 的裸拼接，**没有逐元素的 tag**：

```
repeated int32 g = 5;  [1, 2, 3]  →  2a 03 01 02 03
                                     ^^ tag  ^len ^^^ 三个 varint
```

官方编码指南有一条容易漏的要求：**解析器必须接受同一个字段出现多次 packed 记录，并把载荷拼接后再解析**。`ConcatPacked()` 就是这条约束——它模拟「一个数组被拆成两条 LEN 记录」时的正确行为。这条是为了让「流式生成端一边算一边吐」和「一次算完再吐」产出可以互相兼容。

## 四、group：SGROUP/EGROUP 必须字段号配对

group 的起止靠线类型 3/4 表达，**字段号必须相同**，嵌套时形成栈。`GroupIssues()` 用栈做配对检查，能报三类错：

- EGROUP 无对应 SGROUP（栈空）
- 字段号不匹配（`SGROUP 7 ... EGROUP 8`）
- SGROUP 未闭合（结束时栈非空）

group 在 proto3 已废弃（用嵌套 message 代替），但解析器仍须识别，因为老数据还在。

## 五、字段号治理（proto3 指南）

| 规则 | 数值 | 违反后果 |
|---|---|---|
| 合法区间 | **1 ~ 536870911**（2^29 − 1） | 越界是非法的 |
| 实现保留 | **19000 ~ 19999** | 编译器报错（给 Protobuf 实现自己用） |
| tag 字节数 | 1~15 → 1 字节；16~2047 → 2 字节 | 只影响体积 |

`reserved` 用于「删掉的字段号永不再用」，防止后来者复用造成新旧数据语义冲突：

```proto
reserved 2, 15, 9 to 11;
reserved "foo", "bar";
```

两个易错点，demo 里都做了断言：

1. **区间是闭区间**。`reserved 9 to 11` 含 9、10、11（`ExpandReservedNumbers` 验证 `12` 不在其中）。
2. **字段号与字段名不能混写在同一条语句**（`reserved 2, "foo";` 非法），必须拆成两条。

## 六、拼接即合并（MergeFrom 语义）

Protobuf 的「反序列化」默认不是覆盖，而是**合并**：两段字节拼在一起再解析，等价于把后者的字段合并进前者。规则是三条：

- **标量**：last one wins（后者覆盖）
- **repeated**：**拼接**（不是覆盖）
- **子消息**：**递归合并**（不是整体替换）

`merge_messages()` 就是这三条。`map` 也不是独立机制，而是 `repeated Entry` 的语法糖，`map_entries()/map_from_entries()` 演示了双向转换——这也是为什么 **map 的迭代顺序不保证**：它在字节里就是一组无序的记录。

## 七、gRPC 状态码：谁负责生成哪个码

gRPC 官方定义 **17 个码**（0~16，从 `OK` 到 `UNAUTHENTICATED`），并明确指出其中 **7 个库从不生成**，只该由应用层返回：

```
INVALID_ARGUMENT / NOT_FOUND / ALREADY_EXISTS / FAILED_PRECONDITION
ABORTED / OUT_OF_RANGE / DATA_LOSS
```

其余（如 `UNAVAILABLE`、`INTERNAL`、`DEADLINE_EXCEEDED`）由库根据传输层状况产生。**应用层不该「抢答」这些码**——比如网络断了你不该自己返 `UNAVAILABLE`。

几个高频易混判定，demo 里做成 `classify_*()`：

| 场景 | 正确码 | 常见误用 |
|---|---|---|
| 整类资源对该用户都不可见 | `NOT_FOUND` | 返 `PERMISSION_DENIED`（会泄露资源存在性） |
| 同类资源中部分被拒 | `PERMISSION_DENIED` | 一律 `NOT_FOUND` |
| 配额／限流耗尽 | `RESOURCE_EXHAUSTED` | `UNAVAILABLE`（后者暗示可重试） |
| 无法识别调用方（凭证缺失/过期） | `UNAUTHENTICATED` | `PERMISSION_DENIED` |
| 参数超出可表示范围 | `INVALID_ARGUMENT` | `OUT_OF_RANGE` |
| 索引超过**当前**集合长度 | `OUT_OF_RANGE` | `INVALID_ARGUMENT` |

最后两条的分界是：`OUT_OF_RANGE` 说的是「这个值本身合法，但落在当前集合之外」（重试可能就对了），`INVALID_ARGUMENT` 说的是「这个值本身就不该出现」（重试也没用）。

## 八、重试：三个码的三种处置

官方给出的判定准则，`RetryHint()` 直接编码：

- **(a) UNAVAILABLE** → 可以**重试本调用**。它表示「服务暂时不可用」，是瞬时的。
- **(b) ABORTED** → 应在**更高层**（整个事务/业务操作）重试，因为可能已经发生了部分副作用。
- **(c) FAILED_PRECONDITION** → **不要重试**，必须先改变系统状态（比如先创建缺失的前置资源）。

这三者的区别不在「能不能重试」，而在**重试的粒度**。看错粒度会造成重复扣款这类事故——`ABORTED` 被当成 `UNAVAILABLE` 重试是最典型的坑。

## 九、与 REST/JSON 的对照

| 维度 | Protobuf / gRPC | JSON / REST |
|---|---|---|
| 字段标识 | **字段号**（整数，紧凑） | 字段名（字符串，自描述） |
| 未知字段 | 靠 wire_type 跳过，天然兼容 | 通常忽略，取决于解析器 |
| 负数编码 | 非 sint 类型补码占 10 字节 | 十进制字符串 |
| 合并语义 | 官方 MergeFrom（标量覆盖/repeated 拼接） | 无统一语义 |
| 错误模型 | 17 个状态码 + 机器可读 details | HTTP 状态 + 自定义 body |

## 参考（本轮实际读取）

- Protobuf Encoding Guide — https://protobuf.dev/programming-guides/encoding/ （varint/ZigZag/tag/六种线类型/packed 必须拼接/group 配对）
- Proto3 Language Guide — https://protobuf.dev/programming-guides/proto3/ （字段号 1~536870911、19000~19999 保留、tag 字节数分界、reserved 闭区间与混写禁令）
- gRPC Status Codes — https://grpc.io/docs/guides/status-codes/ （17 个码、7 个库从不生成、(a)(b)(c) 重试准则）

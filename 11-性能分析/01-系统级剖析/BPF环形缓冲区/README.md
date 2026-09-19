# 436 BPF ring buffer：为什么它能同时省内存和保序

> perf buffer 是 **per-CPU** 的：16 核就 16 块缓冲区，内存按峰值预留、事件在核之间**无法保序**。BPF ring buffer 改成 **MPSC 单缓冲区**，一次解决两件事——内存共享、跨 CPU 保序。代价是保留要过自旋锁，以及「慢生产者会挡住后面已提交的记录」。

## 1. 简介

本 demo 依据 Linux 内核文档《BPF ring buffer》实现：

- `BPF_MAP_TYPE_RINGBUF` 的 2 的幂大小约束与 producer/consumer 两个**只增**逻辑计数；
- 8 字节记录头（长度 + busy 位 + discard 位 + 页偏移）；
- `reserve` / `commit` / `discard` / `output` 四件套与验证器的引用追踪约束；
- **保留有序、提交无序**造成的"慢生产者挡路"；
- NMI 下抢不到自旋锁；
- **自节流通知**（这是它相对 perfbuf 最重要的性能特性）。

## 2. 原理详解

### 2.1 两个动机（perf buffer 都做不到）

| 问题 | perf buffer | BPF ringbuf |
| --- | --- | --- |
| **内存利用率** | 每 CPU 一块，按峰值预留，空闲核白占 | 全 CPU 共享一块 |
| **跨 CPU 保序** | 做不到（`fork`/`exec`/`exit` 分散在多核，顺序乱） | 保留严格有序 ⇒ 天然保序 |

两者都源于"per-CPU"这一个设计选择，所以换 MPSC 一次解决。文档也提到：保序理论上可以在 perf buffer 里靠内核计数补救，但既然第一件事必须要 MPSC，同一个方案顺带就解决了第二件。

### 2.2 它是个 map，且 `max_entries` 必须是 2 的幂

`BPF_MAP_TYPE_RINGBUF` 的 key/value size 强制为 0，`max_entries` 用来指定缓冲区大小，**必须是 2 的幂**。做成 map 的好处是复用现有基础设施（内核 introspection、libbpf、bpftool），还能塞进 `ARRAY_OF_MAPS` / `HASH_OF_MAPS` 玩出各种拓扑。

### 2.3 两套 API 的取舍

| API | 拷贝 | 长度要求 | 适用 |
| --- | --- | --- | --- |
| `bpf_ringbuf_output()` | **多一次拷贝** | 长度可以不被验证器预知 | 变长记录、从 perf buffer 迁移 |
| `reserve()/commit()/discard()` | **零拷贝** | 必须是验证器可判定的**常量** | 定长样本、大记录 |

零拷贝的价值很实在：BPF 栈空间很小，大记录以前得先放进 per-CPU array 当临时堆，`reserve()` 直接给出缓冲区内的指针，省掉这一跳。

`discard()` 与 `commit()` 差别极小——只是打个标记让**消费者跳过**。文档举的两个用法：实现 all-or-nothing 的多记录提交，或在单次 BPF 程序里模拟 `malloc()`/`free()`。

验证器通过**引用追踪**跟踪每条已保留的记录（类似 socket 引用），所以**不可能出现"保留了却忘了提交"**。

### 2.4 8 字节头部与"只要指针就能提交"

每条记录一个 8 字节头，含：

- **长度**；
- **busy 位**（还在写，别读）；
- **discard 位**（提交时标记作废）；
- **记录相对数据区起始的偏移（以页为单位）**。

最后一项的妙处：`commit()`/`discard()` 只需要**记录指针**就能反推出整个 ring buffer 在哪儿，不用再传 map 指针——既简化了验证器，也顺手改善了 API 易用性。

> 位布局说明：文档只列了这四个字段，未给精确位号。本 demo 采用「4B len（最高位 busy、次高位 discard）+ 4B pg_off」并标注为 demo 布局。

### 2.5 保留有序、提交无序 ⇒ 慢生产者会挡路

- `reserve` 在**自旋锁**下推进 producer ⇒ 保留之间**严格有序**；
- `commit` **完全无锁**、相互独立；
- 但记录对消费者可见要满足两个条件：按保留顺序、**且它前面所有记录都已提交**。

所以：**一个慢生产者会临时挡住后面已经提交完的记录**。本 demo 的断言直接验了这一点——后保留的先提交，`avail_data` 仍是 0；等前一条提交，两条一起出现。

### 2.6 NMI 与自节流通知

- **NMI 上下文**：因为 reserve 要用自旋锁，NMI 里可能抢不到锁 ⇒ **reserve 失败，即使缓冲区没满**。这是 ringbuf 少数比 perfbuf 吃亏的地方（perfbuf 的 per-CPU 设计不需要抢锁）。
- **自节流通知**：`commit` 只在「消费者已经追到这条记录」时才发通知；如果消费者还落在后面，反正它还要往前走，天然会看到新数据，不需要额外唤醒。官方 benchmark 显示这让 ringbuf 无需 perfbuf 时代"每 N 个样本通知一次"的技巧就能跑出高吞吐。需要精细控制时可以用 `BPF_RB_NO_WAKEUP` / `BPF_RB_FORCE_WAKEUP`。

### 2.7 数据区映射两遍

缓冲区在虚拟内存里**连续映射两份**，所以绕回末尾的记录在虚拟地址上仍然是一段连续内存，生产者和消费者都不需要为"跨边界"写特殊处理。

## 3. 对比

| | perf buffer | BPF ringbuf |
| --- | --- | --- |
| 缓冲区 | 每 CPU 一块 | 一块共享（也可 map-in-map 拆分） |
| 保序 | 否 | 是（按保留顺序） |
| 变长记录 | 支持 | 支持 |
| 空间不足 | 失败，不阻塞 | 失败，不阻塞 |
| mmap / epoll / 忙轮询 | 支持 | 支持 |
| NMI 里 reserve | 可用 | **可能抢锁失败** |
| 通知 | 需手动降频 | 自节流 |

## 4. 环境与运行方式

```bash
cd 11-性能分析/01-系统级剖析/BPF环形缓冲区
python ringbuf_check.py     # 35 条断言，全部实跑通过
go run ringbuf.go           # 需 Go 工具链（本机无，走人工审查 + 机械核查）
```

## 5. 关键代码

```python
def reserve(self, length, nmi=False):
    if nmi and self.lock_held:
        return None                     # NMI 抢不到自旋锁
    total = align8(HDR_SIZE + length)
    if self.producer - self.consumer + total > self.size:
        return None                     # 空间不足：不阻塞，直接失败
    pos = self.producer
    self.producer += total              # 保留即推进，数据尚未可用
    self._put_hdr(pos, length, BUSY_BIT)
    return pos

def _ready(self):                       # 遇到未提交记录立刻停 ⇒ 挡住后面所有
    pos = self.consumer
    while pos < self.producer:
        rec = self.records[pos]
        if rec.state == "reserved":
            return
        yield rec
        pos += rec.total
```

## 6. 性能边界

- 只有一块缓冲区 ⇒ **所有生产者抢同一把自旋锁**，写密集场景可能成为争用点；此时应该用 `HASH_OF_MAPS` 按 key 分片。
- 慢生产者挡路是**设计使然**（为保序付出的代价），不是 bug；需要"先到先得"语义的场景不该用 ringbuf。
- `bpf_ringbuf_query()` 的四个返回值都是**瞬时快照**，拿到时可能已经变了，只适合调试或启发式策略。
- 本模型把"8 字节头"与"页偏移"在 Python 里真写进了 `bytearray`，但没有模拟虚拟内存双映射与缓存一致性，只做语义验证。

## 7. 注意事项与常见坑

1. **`max_entries` 必须是 2 的幂**，写错加载会直接失败。
2. **reserve 的长度必须是验证器可判定的常量**——想提交变长数据用 `output()`。
3. **discard 不是回滚**：记录仍占着空间、仍会被消费者读到（只是跳过）。
4. **在 NMI 里 reserve 可能失败**，别假设"没满就一定成功"。
5. **不要靠 `AVAIL_DATA` 做精确判断**，它是快照。
6. 保留后**必须**提交或丢弃（验证器会强制，但手写 C 时要自己保证路径完整）。

## 8. 参考资料（已读）

- [Linux Kernel — BPF ring buffer](https://docs.kernel.org/bpf/ringbuf.html)——两个动机、map 形态与 2 的幂约束、output vs reserve/commit/discard、8 字节头（len + busy + discard + 页偏移）、reserve 自旋锁串行与 commit 无锁、"按保留顺序但必须前面都提交"、NMI 抢锁失败、虚拟内存双映射、自节流通知与 `BPF_RB_NO_WAKEUP`/`FORCE_WAKEUP`、`bpf_ringbuf_query` 四种查询与快照语义
- 同目录 [eBPF动态追踪/](../eBPF动态追踪/)（demo 097，指令集/验证器/JIT）、[bpftrace前端DSL/](../bpftrace前端DSL/)（demo 102，前端语言与直方图）

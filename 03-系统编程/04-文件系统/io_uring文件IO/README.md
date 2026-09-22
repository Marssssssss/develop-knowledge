# io_uring 对文件 IO 的影响

io_uring 不是"更快的 read/write"，它换掉的是**提交与收割的机制**：把系统调用从"每个 IO 一次"变成"共享内存环 + 可选零系统调用"。

## 1. 三块 mmap

`io_uring_setup(2)` 返回一个 fd，随后要 mmap 三块区域（偏移常量在 `include/uapi/linux/io_uring.h`）：

| 区域 | 偏移 | 长度 |
| --- | --- | --- |
| SQ ring | `IORING_OFF_SQ_RING = 0x0` | `sq_off.array + sq_entries * sizeof(u32)` |
| SQEs | `IORING_OFF_SQES = 0x10000000` | `sq_entries * sizeof(struct io_uring_sqe)` |
| CQ ring | `IORING_OFF_CQ_RING = 0x8000000` | `cq_off.cqes + cq_entries * sizeof(struct io_uring_cqe)` |

（还有 `IORING_OFF_PBUF_RING = 0x80000000` 给 packet buffer ring。）

**SQ 是间接的，CQ 是直接的**——这是最容易记混的一点：

- SQ ring 里 `array[tail & mask]` 存的是 **SQE 下标**，再拿这个下标去 SQEs 数组里取真正的 `io_uring_sqe`
- CQ ring 里 `cqes[head & mask]` **直接就是一个** `io_uring_cqe`

所以"提交"要两步（取槽位 + 填 array），"收割"只要一步。用 `IORING_SETUP_NO_SQARRAY` 可以去掉这层间接（代价是不能再乱序提交）。

上限（`io_uring/io_uring.h`）：

```c
#define IORING_MAX_ENTRIES      32768
#define IORING_MAX_CQ_ENTRIES   (2 * IORING_MAX_ENTRIES)
```

`entries` 超过上限时：带 `IORING_SETUP_CLAMP` 就钳到 32768，不带直接 `EINVAL`；`CQSIZE` 时 `cq_entries` 也必须**大于** `entries`，超 `IORING_MAX_CQ_ENTRIES` 则钳到 65536。

## 2. SQE：`addr` 的语义取决于 opcode

文件 IO 相关的 opcode（枚举顺序即编号）：

```
0 NOP   1 READV   2 WRITEV   3 FSYNC   4 READ_FIXED   5 WRITE_FIXED
8 SYNC_FILE_RANGE   17 FALLOCATE   18 OPENAT   19 CLOSE   21 STATX
22 READ   23 WRITE   24 FADVISE   28 OPENAT2   29 SPLICE   56 FTRUNCATE
62 READV_FIXED   63 WRITEV_FIXED
```

**关键陷阱**：`IORING_OP_READ_FIXED`(4) 的 `addr` 字段是**已注册缓冲区的索引**，不是用户态指针。同一字段在 `IORING_OP_READ`(22) 里才是真正的地址。写反了不会报错，只会把索引当地址去解引用（反之亦然）。

固定文件是另一回事：要额外置 `IOSQE_FIXED_FILE`，此时 `fd` 也是**注册文件表的索引**。两个"固定"是正交的。

`sqe->flags` 位（注意 `IOSQE_IO_DRAIN` 插在 `FIXED_FILE` 和 `IO_LINK` 之间，所以 `IO_LINK` 是 bit 2 不是 bit 1）：

```
bit0 IOSQE_FIXED_FILE      bit1 IOSQE_IO_DRAIN    bit2 IOSQE_IO_LINK
bit3 IOSQE_IO_HARDLINK     bit4 IOSQE_ASYNC       bit5 IOSQE_BUFFER_SELECT
bit6 IOSQE_CQE_SKIP_SUCCESS
```

## 3. CQE：`res` 是 `-errno` 的单通道

```c
struct io_uring_cqe {
        __u64   user_data;  /* sqe->user_data 原样带回 */
        __s32   res;        /* 结果；出错时是 -errno */
        __u32   flags;
        __u64   big_cqe[];  /* IORING_SETUP_CQE32 时的 16 字节扩展 */
};
```

没有单独的 error 字段——`res >= 0` 是字节数（**0 也算成功**，表示 EOF），`res < 0` 是 `-errno`。`user_data` 把请求和完成对上，通常塞一个指针或自增序号。

CQ ring 满时的行为：早期内核直接丢完成事件；`IORING_SETUP_CQ_OVERFLOW`（现在已是默认行为）下内核会把事件暂存到内部队列，等 CQ 腾出空间再回填，期间的溢出计数在 `cq_off.overflow`。本 demo 用"允许/不允许 overflow"两个对照环演示了这条差异。

## 4. 注册资源：一次性 pin 住

`io_uring_register(2)` 的 opcode：`BUFFERS=0 / UNREGISTER_BUFFERS=1 / FILES=2 / BUFFERS2=15 / BUFFERS_UPDATE=16 / RING_FDS=20`。

- 注册缓冲区时内核 **pin 住页**，之后每次 IO 省掉 `get_user_pages` + `put_page`
- 已满的注册表**不能重复注册**，要先 `UNREGISTER_BUFFERS`（否则 `EBUSY`）
- `IORING_REGISTER_BUFFERS_UPDATE` 只能替换**已有槽位**，索引越界是 `EINVAL`，**不能用来扩容**
- `BUFFERS2` 支持带 tag 注册，`IORING_RSRC_REGISTER_SPARSE` 允许稀疏（槽位为 NULL）

## 5. 什么时候还要系统调用

| 场景 | 是否 enter |
| --- | --- |
| 普通模式提交 | 需要 |
| 普通模式等待完成 | 需要（`IORING_ENTER_GETEVENTS` 或 `min_complete > 0`） |
| `IORING_SETUP_SQPOLL` 提交 | **不需要**（内核线程自己看 SQ） |
| `IORING_SETUP_SQPOLL` 等待完成 | 仍需进入（`min_complete > 0`） |

`IORING_SETUP_IOPOLL` 是另一回事：它让内核用轮询而非中断来收割完成，**对常规块设备文件不一定成立**（需要驱动支持 `HIPRI`），网络 IO 更是完全不适用。

## 文件说明

- `python/ioring.py` —— 常量、`SQE`/`CQE`、`Ring`（含 SQ 间接 / CQ 溢出），`RegisteredBuffers`、`setup()` / `clamp_entries()` / `mmap_length()`
- `python/selfcheck_ioring.py` —— 75 条断言（实跑全绿）
- `python/main.py` —— 演示入口
- `go/ioring.go` + `go/ioring_ring.go` —— Go 侧镜像（常量与 setup 一份，环与注册表一份）

## 参考资料（实际读过）

- `include/uapi/linux/io_uring.h`（SQE/CQE 结构、`IORING_OFF_*`、setup flags、opcode 枚举、IOSQE 位、register opcode）
  <https://github.com/torvalds/linux/blob/master/include/uapi/linux/io_uring.h>
- `io_uring/io_uring.h`（`IORING_MAX_ENTRIES 32768` / `IORING_MAX_CQ_ENTRIES`）
  <https://github.com/torvalds/linux/blob/master/io_uring/io_uring.h>
- `io_uring/io_uring.c`（entries 的钳制与 `cq_entries` 上限判断）
  <https://github.com/torvalds/linux/blob/master/io_uring/io_uring.c>
- `io_uring_setup(2)` / `io_uring_register(2)` / `io_uring_enter(2)`
  <https://man7.org/linux/man-pages/man2/io_uring_setup.2.html>
  <https://man7.org/linux/man-pages/man2/io_uring_register.2.html>
  <https://man7.org/linux/man-pages/man2/io_uring_enter.2.html>

> 口径说明：`IORING_SETUP_CQ_OVERFLOW` 这个 flag 名在当前 `io_uring.h` 里已不再出现（溢出暂存已是默认行为），本 demo 只用"允许/不允许溢出"两个对照环表达语义差异，未断言该 flag 的位值。SQ ring 的 64 字节头是按 `struct io_sqring_offsets` 的字段数估算，不是内核实际结构尺寸。

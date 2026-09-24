# MSG_ZEROCOPY 与 vmsplice

`sendfile` / `splice` 消除的是「内核内部」的搬运,`MSG_ZEROCOPY` 补上的是
**用户进程 ↔ 内核**之间那一次拷贝。它把每字节的拷贝成本换成了
**页固定(page pinning)的记账成本 + 完成通知开销**,因此并不总是划算。

## 一、不是加个 flag 就行:三步

`Documentation/networking/msg_zerocopy.rst` 原文(实际抓取并阅读):

1. **先声明意图** —— 内核对未定义 flag 是宽容的(默认忽略),为了不让
   「碰巧传了这个 flag 的老程序」被静默改变语义,必须显式打开:

   ```c
   if (setsockopt(fd, SOL_SOCKET, SO_ZEROCOPY, &one, sizeof(one)))
           error(1, errno, "setsockopt zerocopy");
   ```

2. **发送** —— `ret = send(fd, buf, sizeof(buf), MSG_ZEROCOPY);`
   失败返回 `-1` 且 `errno == ENOBUFS`(超过 socket 的 optmem 限制,
   或用户超过锁定页的 ulimit)。

3. **收完成通知** —— 内核通过 **socket error queue** 通知何时可以改写缓冲区。

## 二、counter:按调用计数,不按字节

原文:

```
Each send call with MSG_ZEROCOPY that successfully sends data increments
the counter. The counter is not incremented on failure or if called with
length zero. The counter counts system call invocations, not bytes.
It wraps after UINT_MAX calls.
```

四条判据,每一条都容易记反:

| 情形 | counter |
| --- | --- |
| 成功发送(无论 1 字节还是 100 KB) | **+1** |
| 长度 0 的调用 | **不增** |
| 失败(ENOBUFS) | **不增** |
| 累计 UINT_MAX 次 | 回绕到 0 |

## 三、通知:走 error queue 的 `sock_extended_err`

```c
struct sock_extended_err {
    __u32 ee_errno;  __u8 ee_origin;  __u8 ee_type;
    __u8 ee_code;    __u8 ee_pad;
    __u32 ee_info;   /* 区间下界 */
    __u32 ee_data;   /* 区间上界 */
};
```

取自 `include/uapi/linux/errqueue.h` 的常量:`SO_EE_ORIGIN_ZEROCOPY = 5`、
`SO_EE_CODE_ZEROCOPY_COPIED = 1`。

- 通知区间是 **`[ee_info, ee_data]`,闭区间**。
- **`ee_errno` 恒为 0** —— 这是刻意的:非零错误码会阻塞该 socket 上的
  `send` / `recv`。所以零拷贝通知「不阻塞其他操作」。
- `poll()` 里 `revents & POLLERR`,而 `events` **不必**设 `POLLERR`
  (错误是无条件上报的)。

### 合并规则

新通知入队前会检查**是否恰好延长了队尾区间的上界**:是则丢弃新包、抬高
`ee_data`。

```
For protocols that acknowledge data in-order, like TCP, each notification
can be squashed into the previous one, so that no more than one
notification is outstanding at any one point.

Ordered delivery is the common case, but not guaranteed. Notifications
may arrive out of order on retransmission and socket teardown.
```

| 通知序列 | 结果 |
| --- | --- |
| 1, 2, 3 | 全程合并 → `[1,3]`,outstanding 恒为 1 |
| 1, 2, 4 | 4 接不上 → `[1,2]` + `[4,4]` |
| UINT_MAX, 0 | u32 意义上是连续的 → 仍然合并 |

## 四、完成通知 ≠ 传输完成

原文说得很直白:

```
A zerocopy completion notification is not a transmit completion
notification, therefore.
```

原因是 **deferred copy**:设备不支持 scatter-gather、或者需要深栈里重算校验和时,
内核会退化成一份私有拷贝。此时通知在**内核释放共享页**时就发出,
可能早于数据真正传完。内核用 `ee_code` 上的
`SO_EE_CODE_ZEROCOPY_COPIED` 标志告知「这次其实是拷过去的」——
应用可以用它决定后续是否继续传 `MSG_ZEROCOPY`。

**loopback 是一个特例**:发往本机的包可能被对端无限期搁置,
通知延迟不可接受,因此**所有 loopback 到本 socket 的 MSG_ZEROCOPY 包都会
deferred copy**(包括走 packet socket / tun 设备的)。文档还提醒:用 veth
跨 namespace 做 benchmark 是**看不出**收益的。

## 五、什么时候该用

原文只给量级,没给精确阈值:

```
MSG_ZEROCOPY is generally only effective at writes over around 10 KB.
```

小于这个量级,页固定 + 通知的开销**高于**直接拷贝。文档中「safe to mix calls
with the flag with those without」也说明:混合大/小 buffer 是推荐做法。

## 六、vmsplice:把用户页喂进管道

`vmsplice(2)`:

- **`fd` 必须是 pipe**,否则 `EBADF`。
- `nr_segs > IOV_MAX`(=1024)→ `EINVAL`。
- **`SPLICE_F_GIFT`**:把用户页「赠」给内核,应用**此后再也不能改这块内存**
  (否则 page cache 与磁盘数据可能不一致)。要求
  **内存基址与长度都页对齐**,否则 `EINVAL`。不给这个 flag 时,后续
  `splice(SPLICE_F_MOVE)` 就只能拷贝。
- `SPLICE_F_MOVE` 对 `vmsplice()` **未使用**;`SPLICE_F_MORE` **当前无效果**。
- 方向不对称:

```
vmsplice() really supports true splicing only from user memory to a pipe.
In the opposite direction, it actually just copies the data to user space.
```

## 七、代码

| 文件 | 说明 |
| --- | --- |
| `python/zcopy_model.py` | counter / 通知合并 / vmsplice 入参校验 |
| `python/selfcheck_zcopy.py` | 57 条断言(实跑全绿) |
| `python/main.py` | 九张对照表 |
| `go/zcopy.go` + `go/main.go` | Go 转写与同构断言 |

## 参考资料(2026-09-24 10:00 槽实际抓取并阅读)

- Linux `Documentation/networking/msg_zerocopy.rst`(全文 9078 B)
- Linux `include/uapi/linux/errqueue.h`(`struct sock_extended_err`、
  `SO_EE_ORIGIN_*`、`SO_EE_CODE_ZEROCOPY_COPIED`)
- `vmsplice(2)` — https://man7.org/linux/man-pages/man2/vmsplice.2.html
- `splice(2)` — https://man7.org/linux/man-pages/man2/splice.2.html
- `recv(2)`(`MSG_ERRQUEUE`)— https://man7.org/linux/man-pages/man2/recv.2.html
- `send(2)` — https://man7.org/linux/man-pages/man2/send.2.html

## 口径说明

- 「约 10 KB」是文档的量级表述,本 demo 只做 `> 10 KiB` 的布尔判定,
  **不反推任何精确阈值**。
- 文档未规定合并通知时 `ee_code` 如何取值;本 demo 取「参与合并的任一次是拷贝,
  则该区间的 `ee_code` 带 `SO_EE_CODE_ZEROCOPY_COPIED`」,并在 README 与
  `python/zcopy_model.py` 注释中标注为 demo 取法而非原文规定。
- 页大小按 4096 建模;实际体系可能不同。

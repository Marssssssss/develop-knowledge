# 紧急数据:`MSG_OOB` / `SIGURG` / `SIOCATMARK`

`tcp(7)` 的说法:

```
TCP supports urgent data. Urgent data is used to signal the receiver that
some important message is part of the data stream and that it should be
processed as soon as possible.
```

但「尽快处理」在 Linux 上只落实成**一个字节 + 一个信号 + 一个标记位**,
而且紧急指针本身就有两套互不兼容的解释。

## 一、紧急指针的两套解释

`tcp(7)` 的 `tcp_stdurg`:

```
If this option is enabled, then use the RFC 1122 interpretation of the TCP
urgent-pointer field. According to this interpretation, the urgent pointer
points to the last byte of urgent data. If this option is disabled, then use
the BSD-compatible interpretation: the urgent pointer points to the first
byte after the urgent data.
```

`tcp_check_urg()` 里就是一行:

```c
u32 ptr = ntohs(th->urg_ptr);
if (ptr && !READ_ONCE(sock_net(sk)->ipv4.sysctl_tcp_stdurg))
        ptr--;                       /* BSD 解释:退一格 */
ptr += ntohl(th->seq);
```

**默认(BSD 解释)下 `urg_ptr=1` 指向的是 `seq+0`,即发送的那一字节本身。**
本 demo 的对照(`seq=100`, payload `ABC`):

| 解释 | `urg_seq` | 取到的字节 | atmark | SIOCINQ |
| --- | --- | --- | --- | --- |
| Linux 默认(BSD) | 100 | `'A'` | 1 | 0 |
| `tcp_stdurg=1`(RFC 1122) | 101 | `'B'` | 0 | 1 |

## 二、`tcp_check_urg()` 的三条守卫

```c
/* 1. Ignore urgent data that we've already seen and read. */
if (after(tp->copied_seq, ptr)) return;
/* 2. Do not replay urg ptr. */
if (before(ptr, tp->rcv_nxt)) return;
/* 3. Do we already have a newer (or duplicate) urgent pointer? */
if (tp->urg_data && !after(ptr, tp->urg_seq)) return;

sk_send_sigurg(sk);
```

三条都不满足才发 `SIGURG`。**「紧急指针落到乱序队列里已经收到的报文上」
会被第 2 条直接丢掉** —— 源码注释自己也承认这是「specs 没覆盖的情形」。

## 三、那个著名的 "Double Dutch" 注释

源码注释原文(节选):「author of comment above did something sort of
`send("A", MSG_OOB); send("B", MSG_OOB);` and expect that both A and B
disappear from stream. This is _wrong_. ... Any application relying on
this is buggy.」判据是 `urg_seq == copied_seq && urg_data &&
!SOCK_URGINLINE && copied_seq != rcv_nxt`,命中则 `copied_seq++`
(完整代码块见 `NOTES.md` §1)。

即:**连续两次 `send(MSG_OOB)`,内核不会让两个字节都从流里消失**。
非 `SO_OOBINLINE` 时它会偷偷把 `copied_seq` 前推一格,以免破坏
`SIOCATMARK` 的语义;开了 `SO_OOBINLINE` 就完全不动(见 `python/main.py` ③)。

## 四、`urg_data` 三态与那个字节

`include/net/tcp.h`:

```c
#define TCP_URG_VALID   0x0100
#define TCP_URG_NOTYET  0x0200
#define TCP_URG_READ    0x0400
```

高 8 位是状态,**低 8 位就是那个紧急字节本身**——`tcp_urg()` 里:

```c
if (ptr < skb->len) {
        u8 tmp;
        if (skb_copy_bits(skb, ptr, &tmp, 1)) BUG();
        WRITE_ONCE(tp->urg_data, TCP_URG_VALID | tmp);
```

这也是「Linux 的紧急数据只有 1 个字节」的直接证据:状态字段里只塞得下 1 字节。

## 五、`SIOCATMARK` 与 `sockatmark()`

`net/ipv4/tcp.c` 的 `tcp_ioctl()`:

```c
case SIOCATMARK:
        answ = READ_ONCE(tp->urg_data) &&
               READ_ONCE(tp->urg_seq) == READ_ONCE(tp->copied_seq);
        break;
```

`sockatmark(3)` 补充说明:返回 1 表示在标记处、0 表示不在,
**不摘除标记**,并且**可以在 SIGURG 处理器里安全调用**,它就是用
`SIOCATMARK` ioctl 实现的。它的 NOTES 也给出结论:
「Out-of-band data is supported only on some stream socket protocols.」

## 六、两个反直觉的接口语义

### 1. `SIOCINQ` / `FIONREAD` 会被截断到标记处

`tcp_inq()`:

```c
} else if (sock_flag(sk, SOCK_URGINLINE) || !tp->urg_data ||
           before(tp->urg_seq, tp->copied_seq) ||
           !before(tp->urg_seq, tp->rcv_nxt)) {
        answ = tp->rcv_nxt - tp->copied_seq;
        ...
} else {
        answ = tp->urg_seq - tp->copied_seq;      /* 只报到紧急字节之前 */
}
```

**接收队列里有 3 字节,`SIOCINQ` 照样可能报 0**(紧急字节就在最前面)。
开了 `SO_OOBINLINE` 才报 3。

### 2. `SO_OOBINLINE` 并不会解除普通读的截断

`tcp_recvmsg()` 里 `used = urg_offset` 的截断在判断 `SOCK_URGINLINE`
**之前**(完整代码块见 `NOTES.md` §2):

```c
u32 urg_offset = tp->urg_seq - *seq;
if (urg_offset < used) { if (!urg_offset) { if (!SOCK_URGINLINE) {...} } else used = urg_offset; }
```

`SO_OOBINLINE` 唯一的区别在 `urg_offset == 0` 那一格:内联时把该字节当普通
数据交给用户;不内联时跳过它、记一个 `urg_hole`。其余位置一律截断。

## 七、`tcp_recv_urg()` 的三态

```c
/* No URG data to read. */
if (sock_flag(sk, SOCK_URGINLINE) || !tp->urg_data || tp->urg_data == TCP_URG_READ)
        return -EINVAL;      /* Yes this is right ! */
```

- `urg_data == 0` 或已是 `TCP_URG_READ` → **`-EINVAL`**(注意是**精确相等**比较,
  不是按位与)。
- 只有 `TCP_URG_NOTYET`(指针到了、字节还没到)→ **返回 0,不是 EINVAL**。
- `TCP_URG_VALID` → 返回 1 字节并在 `msg_flags` 上置 `MSG_OOB`;
  `MSG_PEEK` 不改状态;`len == 0` 时返回 0 并置 `MSG_TRUNC`。

## 八、就绪通知

`tcp(7)`:紧急数据到达时内核给 socket 的 owner 发 **`SIGURG`**
(owner 由 `SIOCSPGRP` / `FIOSETOWN` / `fcntl(F_SETOWN)` 设置);
`SO_OOBINLINE` 打开时紧急字节并入正常流,否则只能用 `MSG_OOB` 读。
`select(2)` 报**异常条件**,`poll(2)` 报 `POLLPRI`。

`tcp_poll()` 里 epoll 的判据是**只看 `TCP_URG_VALID`**:

```c
if (urg_data & TCP_URG_VALID)
        mask |= EPOLLPRI;
```

另外「在标记处且非 URGINLINE」时 `rcvlowat` 的目标会 **+1**
(把那一个字节算进去才能算可读)。

## 九、代码

| 文件 | 说明 |
| --- | --- |
| `python/urg_model.py` | 序列号比较、`tcp_check_urg`、`SIOCATMARK`、`tcp_inq`、`tcp_recv_urg` |
| `python/selfcheck_urg.py` | 61 条断言(实跑全绿) |
| `python/main.py` | 七张对照表 |
| `go/urg.go` + `go/main.go` | Go 转写与同构断言 |

## 参考资料(2026-09-24 10:00 槽实际抓取并阅读)

- `tcp(7)` — https://man7.org/linux/man-pages/man7/tcp.7.html
- `recv(2)` — https://man7.org/linux/man-pages/man2/recv.2.html
- `sockatmark(3)` — https://man7.org/linux/man-pages/man3/sockatmark.3.html
- Linux `net/ipv4/tcp_input.c`:`tcp_check_urg()` / `tcp_urg()`
- Linux `net/ipv4/tcp.c`:`tcp_recv_urg()` / `tcp_ioctl()` 的 `SIOCATMARK` /
  `tcp_poll()` / `tcp_recvmsg()` 的 `urg_offset` 与 `urg_hole`
- Linux `include/net/tcp.h`:`TCP_URG_VALID` / `TCP_URG_NOTYET` /
  `TCP_URG_READ` / `tcp_inq()`

## 口径说明

- `before()` / `after()` 按 32 位无符号回绕实现(`(s32)(a-b)`),本 demo 未做
  PAWS / 时间戳等旁路校验。
- 相邻 demo [进程间通信/凭证传递/](../../进程间通信/凭证传递/) 已验证
  **AF_UNIX 明确不支持 `MSG_OOB`**,本 demo 只讨论 TCP。
- 「只有 1 个字节」是 Linux 实现的结论(`urg_data` 低 8 位只放得下 1 字节),
  不是 RFC 的普遍要求;RFC 1122 语义下紧急指针标记的是一个**位置**。

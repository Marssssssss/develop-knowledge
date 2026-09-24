# 网络编程（系统层视角）

面向**通用**网络服务（Web 服务器、代理、数据库驱动），与游戏场景的区别见 [01-游戏开发/01-服务端/网络编程/](../01-游戏开发/01-服务端/网络编程/)。

## 子领域

- [Socket基础/](./Socket基础/) — BSD Socket API、阻塞/非阻塞、SOCKET 选项（Nagle / Delayed ACK）
- [IO多路复用/](./IO多路复用/) — `select` / `poll` / `epoll` / `kqueue` / `IOCP`
- [IO模型/](./IO模型/) — `readv` / `writev`（scatter-gather）、`TCP_CORK` 组包
- [协议解析/](./协议解析/) — TCP 粘包、HTTP、TLV、二进制协议
- [传输层/](./传输层/) — 拥塞控制（Reno / CUBIC）、重传与 RTO、流量控制
- [连接管理/](./连接管理/) — backlog / SYN 队列 / accept 队列、TIME_WAIT
- [零拷贝/](./零拷贝/) — `sendfile` / `splice` / `mmap`、用户态拷贝的消除
- [进程间通信/](./进程间通信/) — Unix domain socket、`SCM_RIGHTS` 传 fd、凭证传递

## 已完成 demo

| 知识点 | 路径 | 语言 | 摘要 |
| --- | --- | --- | --- |
| Nagle 算法 vs `TCP_NODELAY` | [Socket基础/Nagle算法/](./Socket基础/Nagle算法/) | C / Python / Go | 100× 1-byte send 在 Nagle on → ~8 段 vs `TCP_NODELAY=1` → 100 段;`getsockopt(TCP_INFO).tcpi_segs_out` 实测 |
| 最小 TCP Echo Server | [Socket基础/TCP-Echo-Server/](./Socket基础/TCP-Echo-Server/) | C / Python / Go | BSD socket 标准服务端流程;socket→bind→listen→accept→recv/send 回环;SIGPIPE/EINTR/partial write 三件套规避 |
| TCP Keepalive 探测机制 | [Socket基础/TCP-Keepalive/](./Socket基础/TCP-Keepalive/) | C / Python / Go | SO_KEEPALIVE + TCP_KEEPIDLE/INTVL/CNT 四件套;默认 2h+75s×9 ≈ 2h11m → 本 demo 改 3+2×3 ≈ 9s 加速验证 dead-peer 检测;`kill -STOP` 触发 ETIMEDOUT |
| `SO_REUSEADDR` / `SO_REUSEPORT` / TIME_WAIT | [Socket基础/SO-REUSEADDR/](./Socket基础/SO-REUSEADDR/) | C / Python / Go | 自动跑 A/B/C 三组实验:无 REUSEADDR 重启 EADDRINUSE / REUSEADDR 跳过 TIME_WAIT / REUSEPORT 多 socket 同端口内核 hash 分发 |
| TCP 拥塞控制:Reno vs CUBIC | [传输层/TCP拥塞控制/](./传输层/TCP拥塞控制/) | C / Python / Go | RFC 5681 vs RFC 9438:慢启动 / AIMD 加性增 / 快重传(3 dup ACK)vs RTO;CUBIC 的 `W_cubic = C(t−K)³ + W_max`、β=0.7、α≈0.5294、快收敛;实测吞吐 +17.7%,回到 W_max 的恢复时间 320 RTT → 8 RTT(≈40×) |
| 零拷贝:sendfile / splice | [零拷贝/sendfile-splice/](./零拷贝/sendfile-splice/) | C / Python / Go | read+write(4 搬运/2 CPU 拷贝/4 切换)→ mmap+write → sendfile(2 切换/1 拷贝,有 SG-DMA 则 0)→ splice(0 拷贝但要管道);in_fd 不能是 socket、单次上限 0x7ffff000 |
| backlog 与 SYN 队列 | [连接管理/backlog与SYN队列/](./连接管理/backlog与SYN队列/) | C / Python / Go | `listen` 的 backlog 约束**全连接队列**,上限 `min(backlog, somaxconn)` 静默截断;SYN 队列由 `tcp_max_syn_backlog` 管;溢出时 `tcp_abort_on_overflow` 决定「自愈」还是 RST;syncookies 的代价 |
| scatter-gather IO | [IO模型/scatter-gather/](./IO模型/scatter-gather/) | C / Python / Go | `writev` 一次调用聚集写、`readv` 按 iovec 顺序分散读;IOV_MAX=1024;`TCP_CORK` 200 ms 上限与 `tcp_autocorking`;小响应四种写法包数 2 → 1,大响应包数趋同(此时省的是用户态 memcpy) |
| SCM_RIGHTS 传文件描述符 | [进程间通信/SCM-RIGHTS-fd传递/](./进程间通信/SCM-RIGHTS-fd传递/) | C / Python / Go | 传的是 **open file description 引用**(等价 dup),偏移量共享;`SCM_MAX_FD=253`;流式 socket 必须夹带 ≥1 字节;`CMSG_SPACE` vs `CMSG_LEN` 差 4 字节就丢 fd;截断/限流时多余 fd 在接收方被自动关闭 |
| HTTP/1.1 请求解析器 | [协议解析/HTTP11-Parser/](./协议解析/HTTP11-Parser/) | Python + Go | RFC 7230 §3 状态机;Request-Line + Header Fields + 空行终结 + Content-Length/chunked body;header 大小写不敏感 + 同名合并 + 裸 LF 容错;mini server 实战 |
| TLV 编解码器(简化版 ASN.1 BER) | [协议解析/TLV-Codec/](./协议解析/TLV-Codec/) | Python + Go | Type 2B BE + Length 短/长格式 + Value bytes;流式解码前向兼容未知 tag 跳过;Person 业务示例展示多层结构 |
| 非阻塞 IO 与 `EAGAIN` 语义 | [IO模型/非阻塞Socket/](./IO模型/非阻塞Socket/) | Python + Go | `read(2)` 按 fd 类型分两条 ERRORS 条目(Linux 上 `EAGAIN==EWOULDBLOCK`);短读「有多少给多少」、`MSG_DONTWAIT` 按调用覆盖、部分写、非阻塞 connect 三态 `EINPROGRESS`/`EALREADY`/`EISCONN`、失败只能从 `SO_ERROR` 取;忙轮询 7 次空转 vs 就绪通知 1 次挂起,数据面完全等价 |
| TCP 流量控制与零窗口 | [传输层/TCP流量控制与零窗口/](./传输层/TCP流量控制与零窗口/) | Python + Go | `U = SND.UNA+SND.WND-SND.NXT`;发送端 SWS 四判据(`Fs=1/2`、判据(2)(3) 要求无在飞数据、override 超时兜底);接收端抑制更新至 `RCV.BUFF-RCV.USER-RCV.WND >= min(Fr*RCV.BUFF, MSS)`;零窗口探测首探 RTO、间隔指数增长;`MAY-8`+`MUST-37` 不得拆连接;RFC 7323 `shift<=14`、SYN 不缩放、量化损失与窗口回缩 |
| HTTP/2 帧与 HPACK | [协议解析/HTTP2帧与HPACK/](./协议解析/HTTP2帧与HPACK/) | Python + Go | 9 字节帧头且 Length 不含帧头、默认上限 2^14、未知类型先读长度再丢弃、未定义 flags 必须忽略;HPACK 整数边界是**严格小于** `2^N-1`(故 127 编码为 `FF 00` 而非 `FF`);indexed 索引 0 非法 vs 字面量索引 0 合法;静态表 61 项;条目大小 +32、尾部逐出、过大条目清空整表 |
| `io_uring` 网络 IO | [IO多路复用/io_uring网络IO/](./IO多路复用/io_uring网络IO/) | Python + Go | 每 SQE 恰一 CQE;`res` 成功为返回值、失败为 `-errno` 且 **errno 通道根本不用**;完成顺序不可假设,`user_data` 是必需品;同 socket 同方向不得有多个在飞 send/recv;`IOSQE_IO_LINK` 只保执行顺序;`IORING_SETUP_SQPOLL` 可做到零系统调用(睡了要查 `IORING_SQ_NEED_WAKEUP`);`IOPOLL` 只给 O_DIRECT 不给网络 |
| Unix socket 凭证传递 | [进程间通信/凭证传递/](./进程间通信/凭证传递/) | Python + Go | `SO_PASSCRED` 是接收侧开关但凭证**逐消息**携带,默认填发送方 PID + **real** UID/GID;`SO_PEERCRED` 是 `connect`/`listen`/`socketpair` **时刻的快照**(之后 setuid 不影响),只适用于已连接 stream 与 socketpair;设 `SO_PASSCRED` 会触发 autobind 抽象地址(`\0` + 5 hex,上限 2^20);`SO_RCVBUF` 对 AF_UNIX 无效、无 `MSG_OOB`、`MSG_MORE` |
| TCP_QUICKACK 与 TCP_USER_TIMEOUT | [传输层/TCP-QUICKACK与USER-TIMEOUT/](./传输层/TCP-QUICKACK与USER-TIMEOUT/) | Python + Go | `TCP_QUICKACK` 是**一次性**开关,配额 `rcv_wnd/(2*rcv_mss)` 上限 16 且被 pingpong 一票否决;`TCP_ATO_MIN=TCP_DELACK_MIN=40 ms`、`TCP_DELACK_MAX=200 ms`;ato 递推 `a←floor(a/2)+m` 的不动点是 2m−1 而非 2m;`TCP_USER_TIMEOUT=0` 表示用系统默认,到点返回 1 jiffy 而非 0;`tcp_retries2=15` 的默认超时 924.6 s |
| MSG_ZEROCOPY 与 vmsplice | [零拷贝/MSG-ZEROCOPY与vmsplice/](./零拷贝/MSG-ZEROCOPY与vmsplice/) | Python + Go | 必须先 `setsockopt(SO_ZEROCOPY)` 声明意图;counter 按**调用**计数且长度 0/失败不增;通知走 error queue,`ee_origin=5`、`ee_errno` 恒 0、区间 `[ee_info, ee_data]` 闭区间;TCP 有序时合并到最多 1 条;loopback **必定**退化成拷贝(`ee_code=1`) |
| MSG_OOB 紧急数据与 SIGURG | [IO模型/MSG-OOB紧急数据/](./IO模型/MSG-OOB紧急数据/) | Python + Go | 默认 BSD 解释下 `urg_ptr` 减 1 指向紧急数据**最后一个**字节;`urg_data` 高 8 位是状态**低 8 位就是那个字节**;`SIOCATMARK = urg_data && urg_seq == copied_seq`;有紧急数据时 `SIOCINQ` 被截断到标记处;`SO_OOBINLINE` 只影响 `urg_offset==0` 那一格 |
| BBR 拥塞控制 | [传输层/BBR拥塞控制/](./传输层/BBR拥塞控制/) | Python + Go | 增益是定点整数(`BBR_SCALE=8`);`high_gain=739`(≈2/ln2)、`drain_gain=88`,两者乘积右移 8 位是 254 不是 256;8 相增益循环 `[5/4, 3/4, 1×6]` 且起始相位**永不**是 1;`bbr_bdp` 最后一次除法**向上取整**;pacing 比估计带宽低 1% |
| QUIC 变长整数与帧 | [协议解析/QUIC变长整数与帧/](./协议解析/QUIC变长整数与帧/) | Python + Go | `length = 1 << prefix`,四档 6/14/30/62 位;同一值的非最短编码合法,**但帧类型必须最短**;空负载 = `PROTOCOL_VIOLATION`;ACK Range `largest = previous_smallest − gap − 2`,gap 编码值 = 洞里包数 − 1 |

## 待研究

- [ ] TLS 握手流程(04-API设计 已先做 WebSocket 握手;TLS 与 OpenSSL/BoringSSL 在 03-系统编程 视角下补)
- [x] 非阻塞 Socket 编程(`O_NONBLOCK` + `EAGAIN` + IO 模式) → [IO模型/非阻塞Socket/](./IO模型/非阻塞Socket/)
- [x] 紧急数据 (`MSG_OOB`) 与 SIGURG(TCP 侧;AF_UNIX 明确不支持,见 [进程间通信/凭证传递/](./进程间通信/凭证传递/)) → [IO模型/MSG-OOB紧急数据/](./IO模型/MSG-OOB紧急数据/)
- [x] scatter/gather IO (`readv` / `writev` / `sendmsg`) → [IO模型/scatter-gather/](./IO模型/scatter-gather/)
- [x] TCP_CORK / TCP_QUICKACK / TCP_USER_TIMEOUT → `TCP_CORK` 已随 [IO模型/scatter-gather/](./IO模型/scatter-gather/);`TCP_QUICKACK` / `TCP_USER_TIMEOUT` 待补
- [x] Unix domain socket 与文件描述符传递(SCM_RIGHTS) → [进程间通信/SCM-RIGHTS-fd传递/](./进程间通信/SCM-RIGHTS-fd传递/)
- [x] HTTP/2 二进制帧解析(HPACK 头部压缩) → [协议解析/HTTP2帧与HPACK/](./协议解析/HTTP2帧与HPACK/)
- [x] HTTP/3 与 QUIC(**帧层与变长整数**已完成 → [协议解析/QUIC变长整数与帧/](./协议解析/QUIC变长整数与帧/);流复用、0-RTT、QPACK、版本协商待补)
- [ ] MQTT / CoAP 协议解析
- [x] TCP 流量控制与零窗口探测(`window update` / 接收窗口自动调优) → [传输层/TCP流量控制与零窗口/](./传输层/TCP流量控制与零窗口/)
- [x] 拥塞控制新算法对照(**BBR** 已完成 → [传输层/BBR拥塞控制/](./传输层/BBR拥塞控制/);Vegas / DCTCP / ECN / CUBIC-vs-BBR 混跑对照待补)
- [x] `io_uring` 下的网络 IO(与 `epoll` 的取舍) → [IO多路复用/io_uring网络IO/](./IO多路复用/io_uring网络IO/)
- [x] `MSG_ZEROCOPY` 与 `vmsplice`(零拷贝的用户态页传递) → [零拷贝/MSG-ZEROCOPY与vmsplice/](./零拷贝/MSG-ZEROCOPY与vmsplice/)
- [x] Unix socket 凭证传递(`SCM_CREDENTIALS` / `SO_PASSCRED`)与 `SO_PEERCRED` → [进程间通信/凭证传递/](./进程间通信/凭证传递/)
- [x] `TCP_QUICKACK` / `TCP_USER_TIMEOUT` → [传输层/TCP-QUICKACK与USER-TIMEOUT/](./传输层/TCP-QUICKACK与USER-TIMEOUT/)
- [ ] `SO_ATTACH_BPF` / `SO_REUSEPORT` 的 eBPF 负载分发
## 参考资料(2026-09-20 08:00 槽 + 2026-09-24 10:00 槽实际抓取并阅读)

- RFC 9293《Transmission Control Protocol (TCP)》§3.8.6 / §3.8.6.1 / §3.8.6.2 — https://www.rfc-editor.org/rfc/rfc9293.txt
- RFC 7323《TCP Extensions for High Performance》§2.2 / §2.3 / §2.4 — https://www.rfc-editor.org/rfc/rfc7323.txt
- RFC 9113《HTTP/2》§4.1 / §4.2 — https://www.rfc-editor.org/rfc/rfc9113.txt
- RFC 7541《HPACK》§4 / §5.1 / §6.1 / Appendix A — https://www.rfc-editor.org/rfc/rfc7541.txt
- `read(2)` — https://man7.org/linux/man-pages/man2/read.2.html
- `recv(2)` — https://man7.org/linux/man-pages/man2/recv.2.html
- `socket(7)` — https://man7.org/linux/man-pages/man7/socket.7.html
- `tcp(7)` — https://man7.org/linux/man-pages/man7/tcp.7.html
- `unix(7)` — https://man7.org/linux/man-pages/man7/unix.7.html
- `credentials(7)` — https://man7.org/linux/man-pages/man7/credentials.7.html
- `io_uring(7)` — https://man7.org/linux/man-pages/man7/io_uring.7.html
- `io_uring_setup(2)` — https://man7.org/linux/man-pages/man2/io_uring_setup.2.html
- `io_uring_enter(2)` — https://man7.org/linux/man-pages/man2/io_uring_enter.2.html
- `vmsplice(2)` — https://man7.org/linux/man-pages/man2/vmsplice.2.html
- `splice(2)` — https://man7.org/linux/man-pages/man2/splice.2.html
- `sockatmark(3)` — https://man7.org/linux/man-pages/man3/sockatmark.3.html
- RFC 9000《QUIC: A UDP-Based Multiplexed and Secure Transport》§12.4 / §16 / §19.1-19.3 / 附录 A.1 — https://www.rfc-editor.org/rfc/rfc9000.txt
- RFC 9002《QUIC Loss Detection and Congestion Control》 — https://www.rfc-editor.org/rfc/rfc9002.txt
- Linux 源码 `net/ipv4/tcp_input.c`(`tcp_incr_quickack` / `tcp_event_data_recv` / `tcp_check_urg` / `tcp_urg`)
- Linux 源码 `net/ipv4/tcp_output.c`(`tcp_send_delayed_ack` / `tcp_delack_timer_handler`)
- Linux 源码 `net/ipv4/tcp_timer.c`(`tcp_model_timeout` / `retransmits_timed_out` / `tcp_probe_timer` / `tcp_clamp_*_to_user_timeout`)
- Linux 源码 `net/ipv4/tcp.c`(`tcp_recv_urg` / `tcp_ioctl` 的 `SIOCATMARK` / `tcp_poll`)
- Linux 源码 `net/ipv4/tcp_bbr.c`(全文 42814 B)
- Linux 源码 `include/net/tcp.h` / `include/net/inet_connection_sock.h` / `include/uapi/linux/errqueue.h` / `include/linux/jiffies.h`
- Linux 源码 `Documentation/networking/msg_zerocopy.rst`

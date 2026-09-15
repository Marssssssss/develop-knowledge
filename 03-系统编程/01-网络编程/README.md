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

## 待研究

- [ ] TLS 握手流程(04-API设计 已先做 WebSocket 握手;TLS 与 OpenSSL/BoringSSL 在 03-系统编程 视角下补)
- [ ] 非阻塞 Socket 编程(`O_NONBLOCK` + `EAGAIN` + IO 模式)
- [ ] 紧急数据 (`MSG_OOB`) 与 SIGURG
- [x] scatter/gather IO (`readv` / `writev` / `sendmsg`) → [IO模型/scatter-gather/](./IO模型/scatter-gather/)
- [x] TCP_CORK / TCP_QUICKACK / TCP_USER_TIMEOUT → `TCP_CORK` 已随 [IO模型/scatter-gather/](./IO模型/scatter-gather/);`TCP_QUICKACK` / `TCP_USER_TIMEOUT` 待补
- [x] Unix domain socket 与文件描述符传递(SCM_RIGHTS) → [进程间通信/SCM-RIGHTS-fd传递/](./进程间通信/SCM-RIGHTS-fd传递/)
- [ ] HTTP/2 二进制帧解析(HPACK 头部压缩)
- [ ] MQTT / CoAP 协议解析
- [ ] TCP 流量控制与零窗口探测(`window update` / 接收窗口自动调优)
- [ ] 拥塞控制新算法对照(BBR / Vegas / DCTCP 与 ECN)
- [ ] `io_uring` 下的网络 IO(与 `epoll` 的取舍)
- [ ] `MSG_ZEROCOPY` 与 `vmsplice`(零拷贝的用户态页传递)
- [ ] Unix socket 凭证传递(`SCM_CREDENTIALS` / `SO_PASSCRED`)与 `SO_PEERCRED`
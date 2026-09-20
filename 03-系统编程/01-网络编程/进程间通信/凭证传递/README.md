# Unix domain socket 凭证传递

与同目录的 `SCM-RIGHTS-fd传递/` 是一对：一个传**文件描述符**，一个传**身份**。凭证传递的价值在于——TCP 的 `getpeername()` 只能告诉你 IP 和端口，而 AF_UNIX 能直接拿到对端的 **PID / UID / GID**，且由内核填充。

## 一、原理详解

### 1.1 两条独立的路径

| | `SO_PASSCRED` + `SCM_CREDENTIALS` | `SO_PEERCRED` |
| --- | --- | --- |
| 获取方式 | `recvmsg()` 的 ancillary data | `getsockopt()` |
| 粒度 | **每条消息** | 每个连接（一次） |
| 取值时刻 | 消息发送时 | **`connect()` / `listen()` / `socketpair()` 时刻** |
| 能否拿到多个发送方 | 能（同一 socket 上各不相同） | 不能（只有连接对端） |
| 典型用途 | 服务器上区分同一 socket 的不同客户端 | 一次性身份校验 |

### 1.2 SO_PASSCRED：是开关，但凭证是逐消息的

`unix(7)` 原文：

> *"Enabling this socket option causes receipt of the credentials of the sending process in an `SCM_CREDENTIALS` ancillary message in **each subsequently received message**. The returned credentials are those specified by the sender using `SCM_CREDENTIALS`, or a default that includes the sender's PID, **real user ID**, and **real group ID**, if the sender did not specify `SCM_CREDENTIALS` ancillary data."*

三个要点：

1. **它是接收方的 socket 选项**，但结果是**每条消息**各自附带一份 —— 所以同一个监听 socket 上，7001/7002/7003 三个客户端的凭证互不干扰（demo 第 9 节断言了三条消息各自的 pid 与 uid）。
2. **默认凭证取 real UID / real GID，不是 effective**。一个 setuid 程序用默认凭证上报时，对方看到的是它的**真实**身份，不是提权后的 effective UID。demo 里专门构造了 `uid=1000, euid=0` 的进程来验证这一条。
3. **发送方显式带 `SCM_CREDENTIALS` 时按发送方给的投递**，且不再补默认值（不会同时出现两份）。

### 1.3 SO_PEERCRED：connect 时刻的快照

`unix(7)`：

> *"This read-only socket option returns the credentials of the peer process connected to this socket. The returned credentials are those that were in effect **at the time of the call to `connect(2)`, `listen(2)`, or `socketpair(2)`**."*

这是本 demo 最锋利的一条。含义是：

```
client: setuid(0)                       # 之后才提权
        connect(fd, server_addr)
server: getsockopt(fd, SO_PEERCRED)     # 看到的是提权**之前**的 uid
```

**反过来想就危险了**：如果进程在 `connect()` **之前**就已经是 root，那 `SO_PEERCRED` 看到的就是 root。快照语义的价值在于「**连接建立时**对端是什么身份」是可信的锚点，之后对端怎么改都不影响。demo 的负向断言是「改 `p2.uid` 后 `SO_PEERCRED` **不会**跟着变」。

**适用范围**（原文）：*"The use of this option is possible only for connected AF_UNIX stream sockets and for AF_UNIX stream and datagram socket pairs created using `socketpair(2)`"*。所以：

- ✅ 已连接的 stream socket
- ✅ socketpair 创建的 stream / datagram
- ❌ 未连接的 datagram socket（demo 断言会报错）

注意 datagram 的差别：**未 connect 的 datagram 取不到**，但 **socketpair 的 datagram 可以** —— 因为 socketpair 天生就是「已连接」的。

**`SO_PEERCRED` 只读**，没有对应的 `setsockopt`。

### 1.4 autobind：设 SO_PASSCRED 会顺带产生一个抽象地址

`unix(7)` 的 Autobind 一节：

> *"If a `bind(2)` call specifies `addrlen` as `sizeof(sa_family_t)`, or **`SO_PASSCRED` socket option was specified for a socket that was not explicitly bound to an address**, then the socket is autobound to an abstract address. The address consists of a null byte followed by **5 bytes in the character set [0-9a-f]**. Thus, there is a limit of **2^20** autobind addresses."*

抽象命名空间（abstract namespace）是 Linux 扩展：地址不存在于文件系统，因此**没有文件权限问题、也不需要清理残留 socket 文件**。代价是不可移植。

地址格式是 `\0` + 5 个十六进制字符（`[0-9a-f]`），所以上限是 16^5 = 2^20 ≈ 104 万个。demo 断言了长度 6、首字节 0、后 5 字节全在 `[0-9a-f]` 内。

### 1.5 AF_UNIX 的能力边界

`unix(7)` 明确列出的「不支持」清单：

| 能力 | 状态 |
| --- | --- |
| `MSG_OOB`（带外数据） | **不支持** |
| `MSG_MORE` | **不支持** |
| `SO_SNDBUF` | 有效 |
| `SO_RCVBUF` | **无效**（这点很反直觉） |
| `SCM_RIGHTS` | 支持（见姊妹 demo） |
| `SCM_CREDENTIALS` | 支持 |

`SO_RCVBUF` 无效意味着**不能用它来给 Unix socket 扩容接收缓冲**；想控制吞吐得从别的层面（比如发送方的 `SO_SNDBUF` 或应用层的流控）入手。

## 二、与相关机制的对比

| 机制 | 拿到什么 | 谁填的 | 可否伪造 |
| --- | --- | --- | --- |
| TCP `getpeername()` | IP + 端口 | 内核 | 不可（但可 NAT / 可换端口） |
| HTTP `Authorization` 头 | 应用层自报 | 应用 | 完全可伪造 |
| `SCM_CREDENTIALS`（默认） | PID + real UID + real GID | **内核** | 不可（内核按发送进程填） |
| `SCM_CREDENTIALS`（显式） | 发送方自报 | 应用 | 见下方说明 |
| `SO_PEERCRED` | PID + UID + GID（连接时刻） | **内核** | 不可 |

关于「显式指定 `SCM_CREDENTIALS`」：`unix(7)` 的措辞是 *"The returned credentials are those specified by the sender using SCM_CREDENTIALS"* —— 也就是说**接收方看到的是发送方填的内容**。因此**不要把 `SCM_CREDENTIALS` 当成安全边界使用**：本 demo 只建模 `unix(7)` 描述的投递语义，不做内核侧防伪造的断言。要做权限判断，请依赖内核填充的**默认**凭证或 `SO_PEERCRED`，并配合对端的权限校验。

## 三、环境要求

- Linux（`SO_PASSCRED` / `SO_PEERCRED` / 抽象命名空间均为 Linux 特性）
- Python 3.8+（仅标准库）
- Go 1.18+（本机无 Go 工具链，人工审查 + 结构校验；运行用 `go run .`，两个 `.go` 同属 `package main`）

## 四、运行方式

```bash
cd 凭证传递
python selfcheck_cred.py    # 33 项断言
go run .                    # Go 版 24 项断言
```

## 五、关键代码

```python
def sendmsg(sock, data, cmsgs=None, flags=0):
    if flags & MSG_OOB:
        raise SockError("AF_UNIX 不支持带外数据（MSG_OOB）")
    peer = sock.peer
    cmsgs = list(cmsgs or [])
    has_cred = any(t == SCM_CREDENTIALS for _, t, _ in cmsgs)
    if not has_cred and sock.proc is not None:
        # unix(7)：未指定时填「发送方 PID、real UID、real GID」
        cmsgs.append((SOL_SOCKET, SCM_CREDENTIALS, sock.proc.default_cred()))
    peer.inbox.append((data, cmsgs))
    return len(data)


def recvmsg(sock, flags=0):
    data, cmsgs = sock.inbox.pop(0)
    if not sock.passcred:                      # SO_PASSCRED 是接收侧的开关
        cmsgs = [c for c in cmsgs if c[1] != SCM_CREDENTIALS]
    return data, cmsgs
```

Go 版里 `Ucred` 是**值类型**，天然体现快照语义：

```go
func (s *Sock) Connect(other *Sock) {
	s.Peer = other
	other.Peer = s
	c := other.Proc.DefaultCred()   // connect 时刻取一次
	s.PeerCred = &c                 // 之后对端 setuid 不影响这份拷贝
}
```

## 六、性能边界

- **每条消息都带 ancillary 有成本**：`SCM_CREDENTIALS` 让 `recvmsg` 必须走 cmsg 解析路径。高频小消息场景要考虑是否值得。
- **`SO_PASSCRED` 开启后无法只针对部分消息关闭** —— 是 socket 级别的常开开关。
- **autobind 上限 2^20**：地址空间耗尽后会 `bind` 失败，短生命周期大量建 socket 的场景要注意。
- **抽象地址不落盘**，进程退出即释放，不需要像文件系统 socket 那样处理 stale 文件。
- 本 demo 只建模语义，**不涉及真实内核行为与实测性能**。

## 七、注意事项与常见坑

1. **`SO_PEERCRED` 是快照**：想拿「当前」身份，只能让对端在新消息里带 `SCM_CREDENTIALS`。
2. **默认凭证用 real UID，不是 effective UID** —— 期待看到提权后身份会失望。
3. **未连接的 datagram socket 取不到 `SO_PEERCRED`**，只有 socketpair 出来的 datagram 可以。
4. **`SO_RCVBUF` 对 AF_UNIX 无效**，别指望靠它提升吞吐。
5. **AF_UNIX 没有带外数据**，`MSG_OOB` 不是「紧急通道」的替代方案。
6. **设 `SO_PASSCRED` 会自动 autobind** —— 如果你打算随后显式 `bind()` 一个文件系统路径，顺序反了就会得到一个抽象地址而不是你想要的 socket 文件。
7. **`SCM_CREDENTIALS` 不能当安全边界**（见第二节对比表），权限判断请依赖内核填充的值。
8. **测试夹具要当心**：本 demo 自检第 5 节修改了 `p2.uid`，第 6 节若复用同一对象会导致期望值过时 —— 已改为用全新进程对象。这类「跨节共享可变 fixture」是可变状态测试里最常见的假失败来源。

## 八、参考资料

- `unix(7)` — https://man7.org/linux/man-pages/man7/unix.7.html （`SO_PASSCRED` / `SO_PEERCRED` / Autobind / Sockets API 能力清单）
- `credentials(7)` — https://man7.org/linux/man-pages/man7/credentials.7.html （real / effective / saved set-user-ID 的区分）
- `socket(7)` — https://man7.org/linux/man-pages/man7/socket.7.html （`SO_RCVBUF` / `SO_SNDBUF` 的一般语义，用于对照 AF_UNIX 的差异）
- `cmsg(3)` — https://man7.org/linux/man-pages/man3/cmsg.3.html （ancillary data 的构造宏）

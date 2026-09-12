# SYN Cookie 防 SYN Flood

## 简介

**SYN Cookie** 是 Daniel J. Bernstein 与 Eric Schenk 在 1996 年 9 月发明的抵御 **SYN Flood 攻击** 的技术(1996 年 9 月 Panix 邮件服务遭 SYN flood 攻击停服而闻名,触发发明)。它不用为每个 SYN 分配连接状态(TCB),而把 SYN 队列项**编码进 SYN+ACK 的 TCP 序列号**本身;只有当客户端回 ACK 时,服务器才校验这个"cookie"并重建 TCB。

- **关键问题**:SYN flood 是最古老的 L4 DDoS 攻击。攻击者发海量伪造源 IP 的 SYN 包,服务器为每个 SYN 分配一个等待 ACK 的 TCB(占 ~200-300 B 内存),SYN 队列(tcp_max_syn_backlog,默认 1024-8192)被迅速塞满 → 合法用户的 SYN 包被丢弃 → **拒绝服务**。CERT CA-1996-21 首次记录。
- **关键概念**:
  - **TCP 三次握手的"中间状态"问题**:SYN 收到 → 回 SYN+ACK → 等 ACK → 这一段时间服务器端必须保留一份状态(半开连接)。
  - **无状态握手(SYN Cookie)**:把"半开连接的 5 元组 + 时间戳"用密钥做 HMAC,把结果作为 SYN+ACK 的 ISN;**不分配任何状态**。
  - **客户端回 ACK 的 ack number 还原 ISN**:服务端从 ACK 减 1 拿到 ISN → 验证 HMAC → 通过则分配 TCB 接管,失败则丢弃。
  - **代价**:TCP 选项(window scale/SACK/timestamps) 在攻击期间丢失;MSS 编码限 8 个值(3 bit)。
- **历史**:D.J. Bernstein 在 1996-09 提议 → Jeff Weisberg 第一个 SunOS 实现(1996-10)→ Eric Schenk Linux 实现(1997-02)→ Linux 内核 2.6.26 起支持 TCP 选项编码进 timestamp。RFC 4987 (2007) "TCP SYN Flooding Attacks and Common Mitigations" 正式定义。FreeBSD 自 4.5 起支持。

## 原理详解

### 攻击机制(为什么需要 SYN Cookie)

```
                   SYN 队列满
                        │
攻击者发 100 万 伪造 IP SYN         ┌──────┐
                              ┌──→  │ TCB #1  │ 半开连接
                              │     ├──────┤
Client A:── SYN ──→  Server    ├──→  │ TCB #2  │ 等 ACK (永远不来,5+ 分钟)
                              │     ├──────┤
                              │     │   …    │
                              │     ├──────┤
                              │     │ TCB #N  │ 队列爆满
                              │     └──────┘
Client B:── SYN ──→  Server    │     
                              └──→  ❌ 队列满,丢 Client B 的 SYN
                                    ⚠️ Client B 即使 TCP 全合规也被拒服务
```

### 防御:SYN Cookie 序列号编码

**响应 SYN 时的"无状态"算法**(服务器**不**分配 TCB):

```text
SYN Cookie 32 bit 序列号布局:
┌─────────────────┬─────────┬──────────────────────────────┐
│ Top 5 bits      │ 3 bits  │ Bottom 24 bits               │
│ t mod 32        │ MSS 索引│ s = SHA1(...)[低 24 bit]     │
│ (慢速时间戳)    │ (8 个值)│ (HMAC 摘要,防伪造)          │
└─────────────────┴─────────┴──────────────────────────────┘
```

具体字段:

```text
let t    = time() right-shifted 6   // 慢时间戳,粒度 64 秒,5 bit → 周期 2048 秒 = 34 分钟
let m    = MSS index (从 8 个常用 MSS 值中选最匹配的)  // 仅 3 bit
let s    = (SHA1(server_secret, src_ip, src_port,
                 dst_ip, dst_port, t, m))[低 24 bit]
cookie  = (t mod 32) << 27 | (m_code << 24) | s
```

完整 32 bit ISN = `cookie + 1`(SYN 标志位的 SYN 包,seq 高位要 BIT 31 S=1,SYN+ACK 的 ACK 编号 = client_ISN + 1)。

### 验证 ACK 的算法

```text
当收到 ACK 时:
  1. seq = ack_number - 1  ← 拿回 cookie
  2. t_cooked = (seq >> 27) & 0x1F
  3. m_code   = (seq >> 24) & 0x7
  4. s        = seq & 0xFFFFFF  (低 24 bit)
  5. 比较当前 t mod 32 与 t_cooked(差距 ≤ 4 表示时戳有效,4 × 64s = 256s 容忍窗口)
  6. 重算 s_expected = (SHA1(server_secret, ...))[低 24 bit];s 与 s_expected 匹配 → 合法
  7. 用 m_code 反编码回 MSS,分配 TCB,接管连接
  8. 失败则丢包(攻击者伪造的)
```

### Linux 内核的实际实现

Linux 2.6.32 起的 `tcp_syncookies` 包含两个函数:

- `syncookie_value()` (net/ipv4/syncookies.c):
  ```c
  u32 syncookie_value = (jiffies / (60 * HZ)) << 26   /* t (6 bit 版本上用 5) */
                      | (mss_idx & 0x7) << 24
                      | (smp_syn_hash_1(...));           /* 24 bit SHA1 */
  ```
- `check_tcp_syn_cookie()`:校验 32 位 cookie 是否合法。

**触发条件**(`/proc/sys/net/ipv4/tcp_syncookies`):

| 值 | 行为 |
| --- | --- |
| `0` | 禁用 |
| `1` | 在 SYN 队列满时自动启用(Bernstein 推荐) |
| `2` | 始终启用 |

**SYN Cookie 的局限(教学要点)**:

| 局限 | 影响 |
| --- | --- |
| 32 bit 序列号只能塞 5 bit timestamp + 3 bit MSS + 24 bit hash | MSS 只能 8 个预定义值;超过的 MSS 选项丢失 |
| 没有 TCP 选项空间(window scale、SACK、timestamps) | 在 SYN flood 期间,这些选项不可用;SYN 风暴结束后(backlog 恢复正常)重新支持 |
| 高负载时 SHA-1 计算消耗 CPU | 攻击者可改用 CPU 耗尽型 SYN flood |
| 服务器 secret 是单 key | 需周期重启内核或用 `tcp_syncookie_secret` 接口换 key(2.6.36+) |

### 现代替代方案

| 技术 | 优劣 |
| --- | --- |
| **SYN Cookie** | 标准、内核级、透明,但有 TCP 选项丢失 |
| **SYN Proxy / TCP Proxy** | 反向代理完成握手再转给 backend,backend 不暴露 |
| **Rate Limiting** | per-IP 速率限制,简单但易被绕(spoof IP) |
| **BGP Blackhole / Anycast Scrubbing** | 大规模 ISP 级清洗,适合运营商 |
| **tarpit / RST** | 主动 RST 半开连接,降低攻击效率 |

Linux 推荐**多层防御**:SYN cookies + tcplimit per-IP + 增大 `tcp_max_syn_backlog` + 上游 CDN。

## 环境准备

- 操作系统:Linux(macOS 上也可以跑,BSD 有自家实现),BSD/macOS 也可。
- 语言版本:C/Python/Go 任意。
- **无需 root**:本 demo 纯算法演示,**不发真实 SYN 包**,只回放"分配 cookie / 验证 cookie"的字节序列。
- 真实部署:sysctl `net.ipv4.tcp_syncookies = 1` + `tcp_max_syn_backlog = <N>`。

## 运行方式

```bash
# C
gcc -O2 -Wall -Wextra -pedantic c/syn_cookie.c -lcrypto -o syn_cookie
./syn_cookie

# Python
python3 python/syn_cookie.py

# Go
cd go && go run syn_cookie.go
```

输出演示:
1. 服务器在不同时间收到 3 个 SYN → 输出 3 个 32 bit SYN Cookie ISN
2. 客户端回应 ACK(回传 ISN+1)
3. 服务器解码 cookie → 校验通过 → 输出还原的 `src/dst IP:port + timestamp + MSS` + "✓ 通过"
4. 第二个客户端篡改 cookie 1 bit → 服务器校验失败 → 输出"✗ 丢弃"

## 关键代码片段

完整实现见 `python/syn_cookie.py`。核心算法对应原理详解:

```python
import hashlib, hmac, socket, time, struct

MSS_TABLE = [536, 1300, 1460, 1500, 2000, 4096, 8192, 9000]  # 8 个预设 MSS

def slow_time(now: int) -> int:
    """t = time() >> 6,5 bit 实际为 t mod 32 (64s 粒度,周期 2048 秒)"""
    return (now // 64) % 32

def mss_index(mss: int) -> int:
    return min(MSS_TABLE, key=lambda x: abs(x - mss))  # 找最接近预设

def syn_cookie(src_ip, src_port, dst_ip, dst_port, mss, server_key, now):
    t = slow_time(now)
    m = mss_index(mss)
    msg = f"{src_ip}|{src_port}|{dst_ip}|{dst_port}|{t}|{m}".encode()
    digest = hmac.new(server_key, msg, hashlib.sha1).digest()  # 20 B
    s = struct.unpack(">I", digest[:4])[0] & 0xFFFFFF        # 低 24 bit
    cookie = (t << 27) | (m << 24) | s
    return cookie

def verify_cookie(cookie, src_ip, src_port, dst_ip, dst_port, server_key, now):
    t_cooked = (cookie >> 27) & 0x1F
    m_code   = (cookie >> 24) & 0x7
    s_recv   = cookie & 0xFFFFFF

    # 时戳有效性: t 在 ±4 个窗口内 (256 秒容忍)
    t_now = slow_time(now)
    if abs((t_now - t_cooked) % 32) > 4:
        return None

    msg = f"{src_ip}|{src_port}|{dst_ip}|{dst_port}|{t_cooked}|{m_code}".encode()
    digest = hmac.new(server_key, msg, hashlib.sha1).digest()
    s_expected = struct.unpack(">I", digest[:4])[0] & 0xFFFFFF
    if s_recv != s_expected:
        return None

    return {'src': (src_ip, src_port), 'dst': (dst_ip, dst_port),
            'timestamp': t_cooked, 'mss': MSS_TABLE[m_code]}
```

## 性能与边界

- **Cookie 计算成本**:HMAC-SHA1 一次哈希 ≈ 200 ns(64 B 输入,~400 cycles),单核 ≈ 5M cookie/s。26Mpps 的 SYN flood 在 4 核 CPU 上就能拖到 100%。
- **内存节省**:无 SYN Cookie 时,半开连接占 ~200 B/TCB × 8192 backlog = 1.6 MB;启用后约 0 KB(全部状态藏在 32 bit ISN 里)。
- **可编码选项**:
  - `t`:5 bit(实际 Linux 用 6 bit,2048s/64s 粒度,周期变 64+ 小时)
  - `MSS`:3 bit(8 个预设)
  - `hash`:24 bit(2^24 = 16M,碰撞极小,生日攻击 2^12 个连接就有 50%)
- **超 8 个 MSS** 的客户端:在攻击期间降级到最近预设 MSS。绝大多数网站用 1500/1460,涵盖。
- **不支持 TCP 选项**:SACK、window scale(> 65535 窗口)、timestamps。当前主流 Linux 可用 **ecn**+**SACK** 但丢 **window scale>15**。

## 注意事项与常见坑

- **`tcp_max_syn_backlog` 设过小**:就算开了 SYN Cookie 也会丢包;现代 Linux 推荐 4096-8192。
- **SYN Cookie 与 NAT 不冲突**:NAT 后的多个客户端共享一个公网 IP,SYN Cookie 用 `src_ip:port` 区分即可。
- **不要所有 TCP 服务都开 SYN Cookie**:数据库、管理后台长连接服务应禁用或限制(挡住 win scale 后,跨洲大带宽长连接性能差)。
- **CPU 瓶颈**:SYN Cookie 把"内存耗尽型"变成"CPU 耗尽型"攻击;200万 SYN/s 在 1 核 CPU 上就能拖垮。
- **启用 `tcp_syncookies` 后全端口生效 vs `per-port`**:Linux 早期实现是全局开关,会让攻击者通过 80 端口绕开 22 端口防火墙(Bernstein & Schenk 原论文特别强调需要 per-port 控制);现代 Linux 已分 per-socket。
- **伪造 ACK 攻击**:攻击者不需要完成握手,只需"骗"服务器校验通过 → 所以才用 HMAC + server secret(随机 64-bit,启动时 `cat /dev/urandom` 生成)。
- **`ulimit -n` / `net.core.somaxconn`**:SYN Cookie 启用,半开连接数不再受 `tcp_max_syn_backlog` 直接控制,但 `net.core.somaxconn` 是 `listen()` 上限,仍要调高。
- **现代 mitigation**:Linux 4.4+ 引入了「SYN Proxy」(BPF + XDP)。Cloudflare 用 eBPF/XDP 在网卡驱动 RX hook 早期丢 SYN,几乎不影响 CPU。

## 对比 / 选型

| 防护 | 实现位置 | 优 | 劣 |
| --- | --- | --- | --- |
| **SYN Cookie** | 内核 TCP 协议栈 | 标准化、无需额外进程 | TCP 选项丢失、CPU bound |
| **iptables `hashlimit`** | 内核 netfilter | per-IP 速率限 | 易被 IP spoofing 绕开 |
| **XDP DROP SYN** | 网卡驱动 | 早期 hook、< 100 ns/包 | 需要 eBPF 程序 + XDP-capable NIC |
| **CDN / Anycast Scrubbing** | 上游 ISP | 几乎无限容量 | 引入供应商依赖 |
| **Reverse Proxy TCP Proxy** | 用户空间 | 把 TCP 状态推到 proxy,backend 完全无状态 | 增加 RTT |

## 参考资料(实际阅读过的权威来源)

- [RFC 4987 — TCP SYN Flooding Attacks and Common Mitigations](https://www.rfc-editor.org/rfc/rfc4987) — Bernstein & Schenk 论文数字化,总结了 4 种主要防御(SynCache / SynCookies / SynProxy / content delivery),本 demo 即基于 §3.1 SynCookies 设计
- [SYN cookies — Softpanorama 镜像](https://softpanorama.org/Net/Network_security/DDoS/syn_cookies.shtml) — 完整记述 1996 Panix 攻击、DJB 原论文字段拆分(top 5 / mid 3 / low 24)、FreeBSD 4.5 实现历史
- [SYN cookies — OWiki](https://www.owiki.org/wiki/Syn_cookies) — Linux 内核全局开关误用风险分析(per-port firewall)、RFC 4987 设计思路复盘
- [SYN Flood Attack — Azion](https://www.azion.com/en/learning/ddos/what-is-syn-flood-attack) — 攻击指标(下表:常态 vs 攻击期 SYN/s、内存使用、ACK 比例)+ 8 种缓解技术对照表
- [TCP SYN Flood — adhdecode](https://adhdecode.com/networking/network-attacks-and-threats/tcp-syn-flood) — Defense-in-depth 三层策略(SYN Cookie + Rate Limit + 入口过滤 Ingress Filtering)+ Botnet 攻击规模分析
- [Linux man page — tcp(7) `tcp_syncookies`](https://man7.org/linux/man-pages/man7/tcp.7.html) — 系统调用 `tcp_syncookies` 的 0/1/2 三态行为,`tcp_max_syn_backlog` 关联关系
- [Linux kernel source — `net/ipv4/syncookies.c`](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/net/ipv4/syncookies.c) — `syncookie_value()` + `check_tcp_syn_cookie()` 原代码(教学参考)
- [Linux kernel source — `include/net/tcp.h` `tcp_synack_options()`](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/include/net/tcp.h) — Linux 实际把 TCP options 编码进 timestamp 后的反向解码逻辑(2.6.26+)

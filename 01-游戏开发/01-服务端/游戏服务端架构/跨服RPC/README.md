# 跨服通信:自研 RPC(gRPC 对照)

## 简介

- 分服/分线架构里,跨服组队、全局排行榜、踢人、邮件都要**服务器进程之间**互相调用;游戏服务端两条主流路线:**gRPC**(标准、跨语言、要 HTTP/2 栈)与**自研 RPC**(一根长连接 + 请求号关联,延迟与可控性最优)。本 demo 实现后者,并按 gRPC 官方 wire 协议逐项对照 —— 自研协议里的每个字段,几乎都能在 gRPC 里找到对应物。
- 关键概念:
  - **请求号关联(req_id)**:一根有序连接上并发多个在途调用,响应乱序回来靠 req_id 配对 —— 对应 gRPC 的 **HTTP/2 stream-id**(一次调用占一个 stream)。
  - **超时与晚到响应**:超时的调用在应答晚到时必须静默丢弃(对应 gRPC 的 `grpc-timeout` 头,最多 8 位数字 + 单位字符,如 `1S`)。
  - **单向推送(push)**:req_id=0 的消息,服务端主动推 —— 对应 gRPC 的 server streaming(同一 stream 上多消息)。
  - **错误传播**:响应带状态码而非异常对象(对应 `grpc-status` trailer,必须发送即使 OK=0)。

## 原理详解

### 自研帧格式(本 demo)

```
[uint32 BE 帧长][uint8 消息类型][uint32 BE req_id][uint8 路径长度][路径][载荷]
 1=REQ 2=RESP 3=PUSH(req_id 恒 0)     RESP 额外带 1 字节状态码(0=OK)
```

- 4 字节**大端**长度前缀刻意与 gRPC 的 Length-Prefixed-Message 一致:`[1 字节压缩标志][4 字节大端消息长]`;
- 路径形如 `/ZoneService/KickPlayer` —— 对应 gRPC 的 `:path`(`/包名.服务名/方法名`,`:method` 恒 `POST`,`content-type: application/grpc[+proto]`)。

### 一次调用的完整生命周期

1. 客户端分配 req_id,登记 pending 表(含 deadline),发出 REQ 帧;
2. 服务端按路径查 handler 表,执行(可带模拟延迟),回 RESP 帧(状态码 + 载荷);
3. 多个在途调用的响应**按完成顺序**而非请求顺序回;
4. 客户端按 req_id 配对,写回结果或错误;
5. 每 tick 检查 pending:超过 deadline → 本地判 TIMEOUT;之后同 req_id 的晚到响应**直接丢弃**(连接有序,判过超时的 req_id 不再有效);
6. PUSH 帧无配对,直接交给推送回调。

### 与 gRPC over HTTP/2 的字段对照(全部来自官方 PROTOCOL-HTTP2.md)

| 本 demo | gRPC | 说明 |
| --- | --- | --- |
| req_id | HTTP/2 stream-id | 仅在单连接内有效,不能当全局 GUID |
| 帧长前缀(4B 大端) | Length-Prefixed-Message(1B 压缩标志 + 4B 大端长) | gRPC 压缩上下文**不跨消息** |
| 路径字符串 | `:path` 头 | gRPC 要求 `te: trailers`,非 `application/grpc` 前缀返回 HTTP 415 |
| RESP 状态码 | trailer `grpc-status` / `grpc-message` | OK 也必须发;HTTP 状态恒 200 |
| TIMEOUT | `grpc-timeout: 1S` | 8 位数字 + H/M/S/m/u/n 单位 |
| PUSH | server streaming | 同一 stream 上 `*Length-Prefixed-Message` |
| 断链检测 | HTTP/2 PING 帧 | 双方都可发,内容须精确回显;服务器 PING 超时 → 未完成调用全 CANCELLED,客户端 PING 超时 → 全 UNAVAILABLE |
| 优雅下线 | GOAWAY 帧 | 声明不再收新 stream,已收的可继续;晚于其中 id 的调用客户端应转 UNAVAILABLE 重试 |
| (未实现) | RST_STREAM 映射 | REFUSED_STREAM → UNAVAILABLE(可重试);CANCEL(8) → 调用取消 |

### gRPC 的两个工程要点(规范原文)

- **不假设幂等**:「除非显式声明,gRPC 调用不假定为幂等;无法证明已开始的调用不会重试;无去重机制」—— 自研 RPC 重试同样要带消息号去重;
- **流控**:gRPC 未定义私有流控,完全依赖 HTTP/2 窗口;自研协议要在应用层自己做(如 pending 上限)。

## 对比 / 选型

| 维度 | gRPC | 自研 RPC(本 demo) |
| --- | --- | --- |
| 协议栈 | HTTP/2 + Protobuf(默认) | 一根 TCP 长连接 + 自定义帧 |
| 连接复用 | stream 多路复用 | req_id 关联多路复用 |
| 生态 | 跨语言 stub、拦截器、超时/重试标准 | 全自己写,但零额外依赖、帧头最小 |
| 游戏场景痛点 | HTTP/2 栈重、队头调试难 | 与网关/心跳共用一条连接,运维直观 |

## 环境准备

- OS 任意;Python 3.8+ / Go 1.18+,纯标准库;demo 用内存队列模拟进程间有序信道。

## 运行方式

```bash
python3 python/main.py   # 帧编解码/乱序关联/超时晚到/错误传播/单向推送 断言
go run go/main.go
```

## 关键代码片段(Python)

```python
def on_frame(self, f, now):
    if f.msg_type == MSG_PUSH:            # 单向推送无配对
        self.pushes.append(f)
        return
    entry = self.pending.get(f.req_id)
    if entry is None or entry.done:       # 超时后晚到的响应:静默丢弃
        return
    entry.done = True
    if f.status == 0:
        entry.result = f.payload
    else:
        entry.error = f.status            # 错误以状态码传播,不跨进程抛异常
```

## 性能与边界

- 帧头开销 11 字节(类型+req_id+路径长度+路径)vs gRPC HTTP/2 头压缩后的几十字节 —— 高频小调用差距明显;
- pending 无上限会被慢服务端打爆内存(自研协议要设上限);gRPC 靠 HTTP/2 流控窗口兜底;
- req_id 32 位回绕周期内不许有同号在途调用(本 demo 单调递增不回绕)。

## 注意事项与常见坑

- **晚到响应必须丢弃**:判完超时又收到响应,若仍写入结果会让调用方状态错乱 —— demo 断言 4 专门覆盖;
- **长度前缀要校验**:帧长字段与实际收到的字节数不符必须断链重连,不能「容错」继续解析(会把后续帧全部错位);
- **路径用字符串、版本演进靠新增路径**:不要复用旧路径改语义 —— gRPC 的 `:path` 同理,方法签名变更要开新方法;
- **错误用状态码不用异常**:跨进程异常栈没有意义,gRPC 的 `grpc-message` 也只是 UTF-8 描述(百分号编码)。

## 参考资料(实际阅读过的权威来源)

- [gRPC over HTTP/2(PROTOCOL-HTTP2.md)— grpc/grpc 官方规范](https://github.com/grpc/grpc/blob/master/doc/PROTOCOL-HTTP2.md) — stream 映射、Length-Prefixed-Message、grpc-status/trailers、grpc-timeout、PING/GOAWAY、RST_STREAM 状态映射、幂等性声明。
- [MMO 架构:微服务与同步协议设计](https://blog.csdn.net/chenby186119/article/details/157689177) — 游戏服务间 gRPC 连接池/熔断/重试的工程形态(带 100ms 级 context 超时的调用样例)。
- [《游戏服务端编程实践》1.2.2 MMO 架构模式](https://plumephp.com/game-server-in-action-122/) — 登录/网关/世界服/战斗服间 HTTP+gRPC 与实时 TCP/UDP 的分工。

# HTTP/2 多路复用与流控(RFC 9113)

> OkHttp/Retrofit 默认走 HTTP/2:一条 TCP 上交错跑多个请求、队头阻塞从 HTTP 层
> 消失(挪到 TCP 层)、收发靠**双窗口流控**配速。移动网络高 RTT 下,
> 这两个机制直接决定并发请求的吞吐曲线。

## 1. 流与多路复用(§5)

- **流标识符**:客户端 MUST 用**奇数**(1,3,5…)、服务端偶数;
  `0x0` 专属连接控制(SETTINGS/WINDOW_UPDATE/WINDOW_UPDATE 连接级);
  新流 ID 必须数值更大——一个连接生命周期内不重用;
- **交错不乱序**:任意流的帧可随时插入发送;单个流内部帧序保持;
  一个流的阻塞/慢请求不阻止其它流推进——这是"HTTP 层队头阻塞"的解法
  (TCP 层丢包仍会 holistic 卡住所有流,HTTP/3 才治本);
- 受控帧(基本只有 DATA)受流控约束;HEADERS/WINDOW_UPDATE 等不受。

## 2. 双窗口流控(§6.9.1/6.9.2 原文口径)

> "Two flow-control windows are applicable: the **stream** flow-control window
> and the **connection** flow-control window. The sender MUST NOT send a
> flow-controlled frame with a length that exceeds the space available in
> **either** of the flow-control windows."

| 规则 | 值/语义 |
| --- | --- |
| 初始窗口 | 流级 = 连接级 = **65,535** 字节 |
| 记账 | 发送 DATA 后**两个窗口都减**;9 字节帧头不计入 |
| 闸门 | 发送能力 = **min(流窗口, 连接窗口)** |
| 补充 | WINDOW_UPDATE(流级带 sid / 连接级 sid=0) |
| 上限 | 2³¹−1,超过 = **FLOW_CONTROL_ERROR**(连接错误) |
| 改初值 | SETTINGS_INITIAL_WINDOW_SIZE 按 **delta** 作用于**所有已开流** |
| 连接窗口 | **只能**用 WINDOW_UPDATE 调(SETTINGS 管不到它) |

两个高频坑:

1. **只补一边**:补了流窗口、连接窗口用光,发送照样停——必须两边都补;
2. **中途调小初值**:delta 作用于已开流,窗口可能直接**变负数**,
   流要等多次 WINDOW_UPDATE 回正才能继续发(§6.9.3 防御场景)。

## 3. Android 工程视角

- OkHttp 连接池里一条 HTTP/2 连接承载全部并发请求,
  默认窗口对大响应体是瓶颈(65535 ≈ 64KB)——OkHttp 会用 SETTINGS 通告
  更大初值并定期回 WINDOW_UPDATE;
- 上传大文件时客户端是"发送方":自己维护双窗口记账,
  卡住时先查是不是连接窗口没回。

## 自检

`python python/http2_mux.py` —— 6 项断言:流 ID 奇偶与单调 /
交错不乱序 / 双窗口同减 / min 闸门与"只补一边"陷阱 /
2³¹−1 上限 / SETTINGS 按 delta 作用(可为负)。
Go 侧 `go/http2_mux.go` 为同语义复刻(静态审查)。

## 参考资料(实读)

- [RFC 9113 — HTTP/2(§5 多路复用,§5.1.1 流标识符,§6.9 流控,§6.9.2 初始窗口)](https://www.rfc-editor.org/rfc/rfc9113.html)
- 本目录 [OkHttp/Retrofit 原理 demo](../)(连接池与拦截器链前置)

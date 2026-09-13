# 协议解析

## 关键问题

- **TCP 粘包/拆包**：基于长度 / 分隔符 / 固定头
- **HTTP 解析**：状态机
- **二进制协议**：TLV / Protobuf / FlatBuffers

## 已完成 demo

- ✅ [HTTP11-Parser/](./HTTP11-Parser/) — RFC 7230 状态机式 HTTP/1.1 请求解析器;Request-Line + Header Fields + 空行终结 + Content-Length/chunked body;mini server 演示实战;Python + Go
- ✅ [TLV-Codec/](./TLV-Codec/) — 简化版 ASN.1 BER(Type-Length-Value)编解码;2B BE tag + 短/长 length + bytes value;流式解码前向兼容;Person 业务示例;Python + Go

## 待研究

- [ ] HTTP/2 二进制帧解析(HPACK 头部压缩)
- [ ] Protobuf wire format 自实现(01-游戏开发/服务端 已先做,系统层视角补)
- [ ] FlatBuffers vs Protobuf 性能基准
- [ ] MQTT / CoAP 协议解析
- [ ] WebSocket 握手协议(02-Web开发/API设计 已先做,系统层视角补)
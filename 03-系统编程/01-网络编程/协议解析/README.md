# 协议解析

## 关键问题

- **TCP 粘包/拆包**：基于长度 / 分隔符 / 固定头
- **HTTP 解析**：状态机
- **二进制协议**：TLV / Protobuf / FlatBuffers

## 待研究

- [ ] TLV 编解码器实现
- [ ] HTTP/1.1 最小解析器
- [ ] Protobuf vs FlatBuffers 性能
# 协议设计（游戏服务端）

游戏通信协议的序列化与编码:如何把逻辑对象压成紧凑字节流,以及 TCP 流上的分帧。

## demo 索引

- ✅ [TCP粘包拆包/](./TCP粘包拆包/) — TCP 字节流的分帧(长度前缀/分隔符)
- ✅ [Protobuf序列化/](./Protobuf序列化/) — Protobuf wire format 编解码
- ✅ [FlatBuffers零拷贝/](./FlatBuffers零拷贝/) — FlatBuffers 零反序列化访问
- ✅ [协议压缩Snappy/](./协议压缩Snappy/) — Snappy 流式压缩

## 待研究

- [ ] 自定义二进制协议的版本演进与兼容
- [ ] varint / zigzag 与字段裁剪的体积对比

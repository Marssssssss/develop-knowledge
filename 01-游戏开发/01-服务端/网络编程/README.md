# 网络编程（游戏服务端视角）

聚焦**长连接、低延迟、可容忍少量丢包**场景下的网络编程。

## 子领域

- [IO 多路复用/](./IO多路复用/) — `select` / `poll` / `epoll` / `kqueue` / `IOCP`
- [协议设计/](./协议设计/) — 粘包拆包、Protobuf、FlatBuffers、Snappy
- [零拷贝/](./零拷贝/) — sendfile、splice、MSG_ZEROCOPY

## 为什么单独拎出来

游戏服务端的特点：
- 单服连接数从几千到几十万不等 → 必须用 IO 多路复用或异步 IO
- TCP 长连接心跳 / 半包 / 粘包处理
- UDP 实时同步（FPS/MOBA 类）
- 协议紧凑（自定义二进制优于 Protobuf 的体积开销）
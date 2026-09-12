# 缓存

## 已完成 demo

- [x] Redis 持久化（RDB 快照 + AOF 日志 + Fork 写时复制）— 见 [RedisPersistence/](./RedisPersistence/)（C / Python / Go）
- [x] Redis Cluster（16384 哈希槽 + CRC16/XMODEM + Gossip + 故障转移）— 见 [RedisCluster/](./RedisCluster/)（C / Python / Go）
- [x] 缓存淘汰算法（Memcached 精确 LRU vs Redis 近似 LRU + 候选池）— 见 [LRUEviction/](./LRUEviction/)（C / Python / Go）

## 待研究

- [ ] Redis 数据结构（String/List/Hash/Set/ZSet/Stream）
- [ ] Redis Sentinel 高可用
- [ ] Redis 与 Memcached 业务选型对比
- [ ] CDN 边缘缓存（一致性哈希 + 热点探测）
- [ ] Caffeine W-TinyLFU（JVM）
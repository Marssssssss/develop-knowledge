# 缓存

## 已完成 demo

- [x] Redis 持久化（RDB 快照 + AOF 日志 + Fork 写时复制）— 见 [RedisPersistence/](./RedisPersistence/)（C / Python / Go）
- [x] Redis Cluster（16384 哈希槽 + CRC16/XMODEM + Gossip + 故障转移）— 见 [RedisCluster/](./RedisCluster/)（C / Python / Go）
- [x] 缓存淘汰算法（Memcached 精确 LRU vs Redis 近似 LRU + 候选池）— 见 [LRUEviction/](./LRUEviction/)（C / Python / Go）
- [x] HTTP 缓存语义（RFC 9111 新鲜度/Age/Vary + RFC 5861 stale 扩展）— 见 [HTTP缓存语义/](./HTTP缓存语义/)（Python / Go）
- [x] 缓存一致性模式（写顺序竞态枚举 + Facebook lease 防 stale set/惊群）— 见 [缓存一致性模式/](./缓存一致性模式/)（C / Python / Go）
- [x] W-TinyLFU 准入策略（Caffeine 4-bit CMS + 窗口 LRU + 主区 SLRU）— 见 [WTinyLFU准入/](./WTinyLFU准入/)（Python / Go）
- [x] Redis 过期与淘汰（EXPIRE GT/LT + activeExpireCycle + LFU Morris 计数）— 见 [Redis过期与淘汰/](./Redis过期与淘汰/)（C / Python / Go）
- [x] Memcached Slab 分配器（39 个 class 建表 + 内部碎片 + 页卡死）— 见 [MemcachedSlab分配/](./MemcachedSlab分配/)（C / Python / Go）

## 待研究

- [ ] Redis 数据结构（String/List/Hash/Set/ZSet/Stream）
- [ ] Redis Sentinel 高可用
- [ ] Redis 与 Memcached 业务选型对比
- [ ] CDN 边缘缓存（一致性哈希 + 热点探测）
- [ ] Redis 单线程事件循环与 6.0 多线程 I/O
- [ ] 布隆过滤器与缓存穿透防护
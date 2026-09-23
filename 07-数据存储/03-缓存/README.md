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
- [x] Redis 跳表与有序集合（zskiplist 32 层 / P=0.25 + listpack 互转阈值）— 见 [Redis跳表与有序集合/](./Redis跳表与有序集合/)（Python / Go）
- [x] Redis Sentinel 故障转移（SDOWN/ODOWN 仲裁 + epoch 领头选举 + 7 态状态机）— 见 [RedisSentinel故障转移/](./RedisSentinel故障转移/)（Python / Go）
- [x] 布隆过滤器与缓存穿透防护（RedisBloom bpe 公式 + 可扩容链 + Cuckoo 指纹）— 见 [布隆过滤器与缓存穿透/](./布隆过滤器与缓存穿透/)（Python / Go）
- [x] Redis 事件循环与多线程 I/O（ae.c 派发顺序 / AE_BARRIER / 时间事件 + io-threads）— 见 [Redis事件循环与多线程IO/](./Redis事件循环与多线程IO/)（Python / Go）
- [x] CDN 一致性哈希（libketama md5 环 vs nginx chash crc32 链 + 重迁移实测）— 见 [CDN一致性哈希/](./CDN一致性哈希/)（Python / Go）

## 待研究

- [ ] Redis 数据结构（String/List/Hash/Set/ZSet/Stream）
- [ ] Redis 与 Memcached 业务选型对比
- [ ] Redis 6.0 多线程 I/O 的性能实测与线程数调优
- [ ] Redis Cluster 的 MOVED/ASK 重定向与迁移中的键归属
- [ ] CDN 边缘缓存的缓存键设计与 Vary/Cookie 归一化
- [ ] 缓存预热与缓存雪崩（TTL 抖动 + 熔断 + 多级缓存）
- [ ] Redis Stream 作为延迟队列与消费者组
- [ ] 本地缓存（Caffeine/Guava）与分布式缓存的一致性协同
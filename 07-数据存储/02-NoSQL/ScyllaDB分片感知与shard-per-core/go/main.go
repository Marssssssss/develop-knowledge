// 演示：token -> shard 映射与分片感知驱动的跨核转发代价。
// token 源用 FNV-1a 64 位代替官方 murmur3，只为拿到确定性均匀分布。
package main

import (
	"fmt"
	"hash/fnv"
)

type cluster struct {
	nodes  int
	shards uint
	vnodes int
	sharder *StaticSharder
	slots  int
	span   uint64
}

func newCluster(nodes int, shards uint, vnodes int) *cluster {
	c := &cluster{nodes: nodes, shards: shards, vnodes: vnodes, sharder: NewStaticSharder(shards, 0)}
	c.slots = nodes * vnodes
	c.span = ^uint64(0) / uint64(c.slots)
	return c
}

func tokenFor(key string) int64 {
	h := fnv.New64a()
	h.Write([]byte(key))
	return int64(h.Sum64())
}

func (c *cluster) nodeOf(v int64) int {
	pos := keyToken(v).unbias()
	return int(pos/c.span) % c.nodes
}

func (c *cluster) shardOf(v int64) uint64 {
	return c.sharder.ShardOf(keyToken(v))
}

func route(c *cluster, keys []string, aware bool) (int, int) {
	direct, forwarded := 0, 0
	for i, k := range keys {
		tv := tokenFor(k)
		owner := c.shardOf(tv)
		landed := owner
		if !aware {
			landed = uint64(i % int(c.shards)) // 非分片感知：连到节点上哪个 shard 由连接决定
		}
		if landed == owner {
			direct++
		} else {
			forwarded++
		}
	}
	return direct, forwarded
}

func main() {
	c := newCluster(3, 8, 8)
	keys := make([]string, 0, 1000)
	for i := 0; i < 1000; i++ {
		keys = append(keys, fmt.Sprintf("user:%d", i))
	}

	fmt.Println("ScyllaDB shard-per-core 路由代价（3 节点 x 8 shard，1000 key）")
	ad, af := route(c, keys, true)
	dd, df := route(c, keys, false)
	fmt.Printf("  分片感知   : 直连 %4d / 跨核转发 %4d\n", ad, af)
	fmt.Printf("  非分片感知 : 直连 %4d / 跨核转发 %4d\n", dd, df)
	fmt.Printf("  非分片感知期望直连率 = 1/shards = %.4f\n", 1.0/float64(c.shards))
	fmt.Printf("  实测直连率 = %.4f\n", float64(dd)/float64(len(keys)))

	fmt.Println()
	fmt.Println("token -> shard 映射（前 6 个 key）")
	for _, k := range keys[:6] {
		tv := tokenFor(k)
		fmt.Printf("  %-8s token=%-22d node=%d shard=%d\n", k, tv, c.nodeOf(tv), c.shardOf(tv))
	}

	fmt.Println()
	fmt.Println("sharding_ignore_msb_bits 的影响")
	tv := tokenFor("user:0")
	for _, msb := range []uint{0, 1, 2, 4} {
		s := NewStaticSharder(8, msb)
		fmt.Printf("  msb=%d -> shard %d\n", msb, s.ShardOf(keyToken(tv)))
	}

	fmt.Println()
	fmt.Println("tablet 迁移期：写入同时落两个 shard")
	s := NewStaticSharder(8, 0)
	t := keyToken(tv)
	fmt.Printf("  平时   : %v\n", s.ShardForWrites(t, false))
	fmt.Printf("  迁移中 : %v\n", s.ShardForWrites(t, true))

	fmt.Println()
	fmt.Println("fixed_shard_partitioner：token 高 16 位编码 shard")
	fp := FixedShardPartitioner{}
	ft := fp.TokenForShard(3, 0xABCDEF)
	fmt.Printf("  shard_of = %d, clamp 到 2 shard = %d\n", fp.ShardOf(ft), fp.ShardOfClamped(ft, 2))
}

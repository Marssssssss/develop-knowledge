// Package main：MongoDB 分片集群的 chunk 分裂、均衡阈值与 range 迁移流程。
// 官方口径见 README。
package main

import "fmt"

const defaultChunkMB = 128

func migrationThreshold(chunkSize int) int { return 3 * chunkSize }

func maxOf(xs []int) int {
	m := xs[0]
	for _, x := range xs {
		if x > m {
			m = x
		}
	}
	return m
}

func minOf(xs []int) int {
	m := xs[0]
	for _, x := range xs {
		if x < m {
			m = x
		}
	}
	return m
}

func needsBalancing(sizes []int, chunkSize int) bool {
	if len(sizes) < 2 {
		return false
	}
	return maxOf(sizes)-minOf(sizes) >= migrationThreshold(chunkSize)
}

// maxParallelMigrations n 个 shard 最多 floor(n/2) 个并发迁移。
func maxParallelMigrations(nShards int) int { return nShards / 2 }

// splitPlan 超过 chunkSize 就二分，返回 (分裂次数, 块数, 每块大小)。
func splitPlan(sizeMB, chunkSize int) (int, int, float64) {
	if sizeMB <= chunkSize {
		return 0, 1, float64(sizeMB)
	}
	splits, parts := 0, 1
	for float64(sizeMB)/float64(parts) > float64(chunkSize) {
		parts *= 2
		splits++
	}
	return splits, parts, float64(sizeMB) / float64(parts)
}

// Chunk 一个 chunk 区间与其唯一 shard key 值个数。
type Chunk struct {
	Low, High     string
	SizeMB        int
	DistinctKeys  int
	Jumbo         bool
}

func (c *Chunk) Divisible() bool { return c.DistinctKeys > 1 }

// Evaluate 超过 chunkSize 且不可分裂 -> jumbo。
func (c *Chunk) Evaluate(chunkSize int) bool {
	c.Jumbo = c.SizeMB > chunkSize && !c.Divisible()
	return c.Jumbo
}

func (c Chunk) String() string {
	return fmt.Sprintf("Chunk[%s,%s) %dMB keys=%d jumbo=%v", c.Low, c.High, c.SizeMB, c.DistinctKeys, c.Jumbo)
}

// Migration range 迁移状态机，7 步，删除阶段可异步。
type Migration struct {
	Name        string
	AsyncDelete bool
	Step        int
	Done        bool
	Deleted     bool
}

var steps = []string{
	"moveRange 下发到源分片",
	"源分片继续承接该 range 的写",
	"目标分片补齐索引",
	"目标分片拉取文档",
	"同步迁移期间的增量",
	"源分片更新 config 元数据",
	"源分片删除自己的副本（等无游标后）",
}

func (m *Migration) Advance() {
	if m.Done {
		if !m.Deleted {
			m.Deleted = true
		}
		return
	}
	m.Step++
	if m.AsyncDelete && m.Step >= len(steps)-1 {
		m.Done = true
		m.Deleted = false
	} else if m.Step >= len(steps) {
		m.Done = true
		m.Deleted = true
	}
}

// balanceRound 一轮均衡：反复从最多的 shard 往最少的搬，直到落在阈值内。
func balanceRound(sizes []int, chunkSize int) ([][]int, int) {
	cur := append([]int(nil), sizes...)
	rounds := [][]int{}
	moves := 0
	for needsBalancing(cur, chunkSize) && moves < 1000 {
		hi, lo := 0, 0
		for i := range cur {
			if cur[i] > cur[hi] {
				hi = i
			}
			if cur[i] < cur[lo] {
				lo = i
			}
		}
		amount := (cur[hi] - cur[lo]) / 2
		if amount <= 0 {
			break
		}
		cur[hi] -= amount
		cur[lo] += amount
		moves++
		rounds = append(rounds, append([]int(nil), cur...))
	}
	return rounds, moves
}

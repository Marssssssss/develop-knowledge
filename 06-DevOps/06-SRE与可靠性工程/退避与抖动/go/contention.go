package main

// OCC 竞争轮次模型,与 python/contention.py 同构。
// 简化点:网络延迟取固定 rtt(不含方差),同一时刻到达的写按 client id 定序。
// `slot` 把重试时刻向上取整到槽边界 —— 不离散化则连续随机几乎永不相等,
// 四种策略会退化成同一个数字,模型失去分辨力。

import (
	"math"
	"math/rand"
)

type client struct {
	id      int
	next    float64
	attempt int
	bo      interface{ Next(int) float64 }
	done    bool
}

// Simulate 返回 (总调用次数, 全部完成所用时间ms)。
func Simulate(nClients int, name string, rtt, base, cap, slot float64, seed int64) (int, float64, error) {
	rng := rand.New(rand.NewSource(seed))
	clients := make([]client, nClients)
	for i := range clients {
		bo, err := Make(name, base, cap, rng)
		if err != nil {
			return 0, 0, err
		}
		clients[i] = client{id: i, next: 0, attempt: 0, bo: bo}
	}
	version, calls, finished := 0, 0, 0
	now := 0.0
	maxTime := 5e6
	for finished < nClients && now <= maxTime {
		now = math.MaxFloat64
		for i := range clients {
			if !clients[i].done && clients[i].next < now {
				now = clients[i].next
			}
		}
		if now == math.MaxFloat64 {
			break
		}
		// 同一批发起:read 全部读到同一个版本号,write 按 id 定序,只有第一个成功
		readVersion := version
		for i := range clients {
			c := &clients[i]
			if c.done || c.next != now {
				continue
			}
			calls++
			if readVersion == version {
				version++
				c.done = true
				finished++
			} else {
				c.attempt++
				wake := now + 2*rtt + c.bo.Next(c.attempt)
				c.next = math.Ceil(wake/slot-1e-12) * slot
			}
		}
	}
	total := 0.0
	for i := range clients {
		if clients[i].next > total {
			total = clients[i].next
		}
	}
	return calls, total, nil
}

// eviction.go — 与 python/eviction.py 同语义的 Go 复刻(静态审查用)。
package main

import (
	"fmt"
	"math/rand"
)

type Key struct {
	Name       string
	LastAccess int
	HasExpire  bool
}

// Candidates:volatile-* 只在带过期键里挑,allkeys-* 全键。
func Candidates(policy string, keys []Key) []Key {
	if len(policy) >= 7 && policy[:7] == "allkeys" {
		return keys
	}
	var out []Key
	for _, k := range keys {
		if k.HasExpire {
			out = append(out, k)
		}
	}
	return out
}

// ApproxLRUEvict:随机抽 sample 个,淘汰其中最久未访问者(近似 LRU)。
func ApproxLRUEvict(cands []Key, sample int, rng *rand.Rand) Key {
	pool := append([]Key{}, cands...)
	rng.Shuffle(len(pool), func(i, j int) { pool[i], pool[j] = pool[j], pool[i] })
	if sample > len(pool) {
		sample = len(pool)
	}
	victim := pool[0]
	for _, k := range pool[:sample] {
		if k.LastAccess < victim.LastAccess {
			victim = k
		}
	}
	return victim
}

func main() {
	rng := rand.New(rand.NewSource(42))
	pool := []Key{}
	for i := 0; i < 100; i++ {
		pool = append(pool, Key{fmt.Sprintf("k%d", i), i, true})
	}
	pool = append(pool, Key{"config", 999, false}) // 永久键
	vol := Candidates("volatile-lru", pool)
	fmt.Println("volatile cands:", len(vol), "(config 被排除)")
	fmt.Println("victim:", ApproxLRUEvict(pool, 5, rng).Name) // 采样近似,多为 k 小者
}

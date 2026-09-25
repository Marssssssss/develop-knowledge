// idempotent.go — 与 python/idempotent.py 同语义的 Go 复刻(静态审查用)。
package main

import "fmt"

type RedisLike struct {
	store map[string]string
	coins map[string]int
}

func NewRedisLike() *RedisLike {
	return &RedisLike{store: map[string]string{}, coins: map[string]int{}}
}

// SetNX:仅当键不存在才设置(官方 NX 语义)。
func (r *RedisLike) SetNX(key, value string) bool {
	if _, ok := r.store[key]; ok {
		return false
	}
	r.store[key] = value
	return true
}

func (r *RedisLike) Incr(key string, by int) int {
	r.coins[key] += by
	return r.coins[key]
}

// GrantLua:占键与加币同一脚本内原子执行(服务器阻塞)。
func GrantLua(r *RedisLike, order, player string, amount int) (string, int) {
	if _, ok := r.store["idem:"+order]; ok {
		return "duplicate", r.coins["coin:"+player]
	}
	r.store["idem:"+order] = "granted"
	r.Incr("coin:"+player, amount)
	return "first", r.coins["coin:"+player]
}

func main() {
	r := NewRedisLike()
	fmt.Println(GrantLua(r, "o1", "alice", 100)) // first 100
	fmt.Println(GrantLua(r, "o1", "alice", 100)) // duplicate 100
	fmt.Println(GrantLua(r, "o1", "alice", 100)) // duplicate 100
}

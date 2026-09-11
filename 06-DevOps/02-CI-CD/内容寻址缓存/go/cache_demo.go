// cache_demo.go — 内容寻址缓存(hashFiles 键 + restore-keys 回退 + LRU/TTL 淘汰)
//
// 模拟 actions/cache 的核心语义:
//
//	demo 1 精确命中跳过构建  demo 2 lockfile 变更 + restore-keys 部分回退
//	demo 3 容量 LRU 淘汰 + 一周 TTL
package main

import (
	"crypto/sha256"
	"fmt"
	"sort"
	"strings"
)

const (
	capacity = 3      // GitHub 上限是单仓库 5GB,demo 用条目数近似
	ttlMin   = 10080  // 一周 = 7*24*60 分钟,一周未访问即淘汰
)

type entry struct {
	key        string
	data       string
	lastAccess int
}

type ciCache struct {
	capacity int
	entries   map[string]*entry
	clock     int // 逻辑时钟:每次 lookup/save 前进 1 分钟
}

func newCICache(cap int) *ciCache {
	return &ciCache{capacity: cap, entries: map[string]*entry{}}
}

func (c *ciCache) tick() int {
	c.clock++
	return c.clock
}

// keyFor hashFiles 语义:锁文件内容 SHA-256 => key(内容寻址)。
func (c *ciCache) keyFor(namespace, lockfile string) string {
	sum := sha256.Sum256([]byte(lockfile))
	return fmt.Sprintf("%s-%x", namespace, sum)
}

func (c *ciCache) evictExpired() {
	for k, e := range c.entries {
		if c.clock-e.lastAccess > ttlMin {
			fmt.Printf("    [evict] TTL(>7d unused): %.24s...\n", k)
			delete(c.entries, k)
		}
	}
}

// lookup 官方查找次序:精确命中 -> restore-keys 前缀(最近访问)-> miss。
// 返回 ("exact"|"partial"|"miss", data)。
func (c *ciCache) lookup(key string, restoreKeys []string) (string, string) {
	c.evictExpired()
	if e, ok := c.entries[key]; ok { // 1. 精确匹配
		e.lastAccess = c.tick()
		return "exact", e.data
	}
	for _, prefix := range restoreKeys { // 3. 前缀回退
		bestKey := ""
		for k, e := range c.entries {
			if strings.HasPrefix(k, prefix) &&
				(bestKey == "" || e.lastAccess > c.entries[bestKey].lastAccess) {
				bestKey = k // 最近访问的部分匹配
			}
		}
		if bestKey != "" {
			e := c.entries[bestKey]
			e.lastAccess = c.tick()
			return "partial", e.data
		}
	}
	return "miss", "" // 4. 全部未命中
}

// save job 成功后保存;超过容量按 LRU 淘汰(最近最少访问先走)。
func (c *ciCache) save(key, data string) {
	c.evictExpired()
	if e, ok := c.entries[key]; ok { // 已存在则更新
		e.lastAccess = c.tick()
		e.data = data
		return
	}
	for len(c.entries) >= c.capacity {
		lru := ""
		for k, e := range c.entries {
			if lru == "" || e.lastAccess < c.entries[lru].lastAccess {
				lru = k
			}
		}
		fmt.Printf("    [evict] LRU: %.24s... (data='%.20s...')\n",
			lru, c.entries[lru].data)
		delete(c.entries, lru)
	}
	c.entries[key] = &entry{key, data, c.tick()}
}

func (c *ciCache) keys() []string {
	ks := make([]string, 0, len(c.entries))
	for k := range c.entries {
		ks = append(ks, k)
	}
	sort.Strings(ks)
	return ks
}

// fakeInstall 模拟 npm install:产物内容取决于锁文件列出的依赖。
func fakeInstall(lockfile string) string {
	if strings.Contains(lockfile, "lodash") {
		return "node_modules[express@4.18.0, lodash@4.17.21]"
	}
	return "node_modules[express@4.18.0]"
}

func demoHitMiss() {
	fmt.Println("== demo 1: 精确命中 -> 跳过构建 ==")
	c := newCICache(capacity)
	lockV1 := `{"packages":{"node_modules/express":{"version":"4.18.0"}}}`
	key := c.keyFor("npm", lockV1)
	fmt.Printf("  lockfile v1 -> key %.28s...\n", key)

	kind, _ := c.lookup(key, []string{"npm-"})
	fmt.Printf("  run #1: %s -> run npm install (120 s)\n", kind)
	data := fakeInstall(lockV1)
	c.save(key, data)
	fmt.Printf("         saved '%s'\n", data)

	kind, data = c.lookup(key, []string{"npm-"})
	fmt.Printf("  run #2: %s -> skip install, restore '%s' (5 s)\n", kind, data)
}

func demoRestoreKeys() {
	fmt.Println("== demo 2: lockfile 变更 + restore-keys 部分回退 ==")
	c := newCICache(capacity)
	lockV1 := `{"packages":{"node_modules/express":{"version":"4.18.0"}}}`
	lockV2 := `{"packages":{"node_modules/express":{"version":"4.18.0"},` +
		`"node_modules/lodash":{"version":"4.17.21"}}}`
	k1, k2 := c.keyFor("npm", lockV1), c.keyFor("npm", lockV2)
	c.save(k1, fakeInstall(lockV1))
	fmt.Printf("  cache has v1 entry; v2 lockfile -> new key %.24s...\n", k2)

	kind, data := c.lookup(k2, []string{"npm-"}) // 前缀 "npm-" 命中 v1
	fmt.Printf("  run: %s -> restore old '%s' as base, npm install (60 s, 增量)\n",
		kind, data)
	c.save(k2, fakeInstall(lockV2))
	fmt.Printf("       saved new '%s'\n", c.entries[k2].data)
}

func demoEviction() {
	fmt.Println("== demo 3: 容量 LRU 淘汰 + 一周 TTL ==")
	c := newCICache(capacity)
	keys := make([]string, 4)
	for i := 0; i < 4; i++ {
		keys[i] = c.keyFor("npm", fmt.Sprintf("lock#%d", i))
	}
	for i := 0; i < 3; i++ {
		c.save(keys[i], fmt.Sprintf("deps-v%d", i))
	}
	fmt.Printf("  saved 3 entries, then touch v0\n")
	c.lookup(keys[0], nil) // 访问 v0 => v1 成为 LRU

	fmt.Printf("  save v3 (capacity=%d):\n", capacity)
	c.save(keys[3], "deps-v3") // 淘汰 v1(最久未访问)
	kind, _ := c.lookup(keys[1], nil)
	fmt.Printf("  lookup v1: %s (已被 LRU 淘汰)\n", kind)
	kind, _ = c.lookup(keys[0], nil)
	fmt.Printf("  lookup v0: %s (刚访问过,保留)\n", kind)

	c.clock += ttlMin + 1 // 逻辑时钟推进 8 天
	kind, _ = c.lookup(keys[3], nil)
	fmt.Printf("  8 天后 lookup v3: %s (一周未访问被 TTL 淘汰)\n", kind)
}

func main() {
	demoHitMiss()
	fmt.Println()
	demoRestoreKeys()
	fmt.Println()
	demoEviction()
}

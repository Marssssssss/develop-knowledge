// Prepared Statement + Connection Pool (Go version).
//
// Mirrors prepared_pool.py.
//
// Refs:
//   - PostgreSQL protocol-flow.html
//   - PgBouncer max_prepared_statements
//   - plan_cache_mode auto/custom/generic

package main

import (
	"container/list"
	"crypto/md5"
	"encoding/hex"
	"fmt"
)

type Stmt struct {
	SQL           string
	CustomCount   int
	CustomCosts   []float64
	GenericCost   float64
	GenericSwitch bool
}

type Plan struct {
	Steps []string
	Cost  float64
	Gen   bool
}

func customPlan(sql string, param int) Plan {
	sel := 1.0
	if param < 100 {
		sel = 0.1
	}
	return Plan{Steps: []string{"IndexScan"}, Cost: 10.0 + 5.0*sel}
}

func genericPlan(sql string) Plan {
	return Plan{Steps: []string{"SeqScan"}, Cost: 15.0}
}

type Cache struct {
	entries map[string]*list.Element
	lru     *list.List
	max     int
}

type cacheEntry struct {
	key  string
	stmt *Stmt
}

func NewCache(max int) *Cache {
	return &Cache{
		entries: map[string]*list.Element{},
		lru:     list.New(),
		max:     max,
	}
}

func hashKey(sql string) string {
	h := md5.Sum([]byte(sql))
	return hex.EncodeToString(h[:])[:16]
}

func (c *Cache) get(sql string) *Stmt {
	k := hashKey(sql)
	if e, ok := c.entries[k]; ok {
		c.lru.MoveToFront(e)
		return e.Value.(*cacheEntry).stmt
	}
	s := &Stmt{SQL: sql, GenericCost: 15.0}
	if c.lru.Len() >= c.max {
		old := c.lru.Back()
		if old != nil {
			c.lru.Remove(old)
			delete(c.entries, old.Value.(*cacheEntry).key)
		}
	}
	e := c.lru.PushFront(&cacheEntry{key: k, stmt: s})
	c.entries[k] = e
	return s
}

func (c *Cache) plan(s *Stmt, param int) Plan {
	s.CustomCount++
	cost := customPlan(s.SQL, param).Cost
	s.CustomCosts = append(s.CustomCosts, cost)
	if s.CustomCount >= 5 {
		avg := 0.0
		for _, v := range s.CustomCosts {
			avg += v
		}
		avg /= float64(len(s.CustomCosts))
		if avg > s.GenericCost {
			s.GenericSwitch = true
			return genericPlan(s.SQL)
		}
	}
	return customPlan(s.SQL, param)
}

type Backend struct {
	ID       int
	Prepared map[string]string // sql_hash → stmt_name
}

func (b *Backend) Has(sql string) bool {
	_, ok := b.Prepared[hashKey(sql)]
	return ok
}

func (b *Backend) Prepare(sql, name string) {
	if b.Has(sql) {
		return
	}
	b.Prepared[hashKey(sql)] = name
	if len(b.Prepared) > 64 {
		// evict arbitrary (simplified LRU not tracked per backend)
		for k := range b.Prepared {
		if len(b.Prepared) > 64 {
			delete(b.Prepared, k)
			break
		}
		}
	}
}

type Pool struct {
	Backends []*Backend
	Cache    *Cache
}

func NewPool(nBackends int, maxPrepPerBackend int) *Pool {
	p := &Pool{Cache: NewCache(100)}
	for i := 0; i < nBackends; i++ {
		p.Backends = append(p.Backends, &Backend{
			ID: i, Prepared: map[string]string{},
		})
	}
	return p
}

func (p *Pool) Execute(clientID int, sql string, param int) Plan {
	stmt := p.Cache.get(sql)
	be := p.Backends[clientID%len(p.Backends)]
	if !be.Has(sql) {
		be.Prepare(sql, stmt.SQL)
	}
	return p.Cache.plan(stmt, param)
}

func main() {
	pool := NewPool(3, 64)
	sql := "SELECT * FROM users WHERE country = $1"

	fmt.Println("=== Test 1: 6 executions of same SQL, different params ===")
	params := []int{42, 99, 7, 1000, 50, 88}
	for i, param := range params {
		plan := pool.Execute(1, sql, param)
		gen := ""
		if plan.Gen {
			gen = " [GENERIC]"
		}
		fmt.Printf("  exec %d  param=%d  cost=%.2f%s\n",
			i+1, param, plan.Cost, gen)
	}

	fmt.Println("\n=== Test 2: 3 clients, 3 backends ===")
	for _, cid := range []int{10, 20, 30} {
		plan := pool.Execute(cid, sql, 42)
		be := pool.Backends[cid%len(pool.Backends)]
		fmt.Printf("  client %d → backend %d  cost=%.2f  "
			"backend prepared=%d\n",
			cid, be.ID, plan.Cost, len(be.Prepared))
	}

	fmt.Println("\n=== Test 3: cache + backend distribution ===")
	fmt.Printf("  cache entries: %d  backends: %d\n",
		pool.Cache.lru.Len(), len(pool.Backends))
	for _, b := range pool.Backends {
		fmt.Printf("    backend %d: %d prepared\n", b.ID, len(b.Prepared))
	}
}
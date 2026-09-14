// Neo4j 并发控制最小实现 (Go 版): 锁管理器 + 等待图死锁检测 + 丢失更新.
// 语义同 Python 版, 依据 Neo4j Operations Manual "Concurrent data access".
package main

import (
	"fmt"
	"sync"
)

type DeadlockError struct{ Txid, Entity string }

func (e *DeadlockError) Error() string {
	return fmt.Sprintf("deadlock detected: tx %s -> %s", e.Txid, e.Entity)
}

// LockManager 实体级锁管理器 + 等待图死锁检测.
// 关键语义: 阻塞的事务在等待图中保留边(tx -> entity), 直到拿到锁或被终止.
type LockManager struct {
	mu          sync.Mutex
	holders     map[string]map[string]bool // entity -> set(txid)
	waiting     map[string]string          // txid -> entity (等待边)
	txEntities  map[string]map[string]bool // txid -> set(entity)
}

func NewLockManager() *LockManager {
	return &LockManager{
		holders:    map[string]map[string]bool{},
		waiting:    map[string]string{},
		txEntities: map[string]map[string]bool{},
	}
}

// Acquire 返回 (true=拿到锁); 阻塞返回 (false, nil); 死锁返回 (false, err).
func (lm *LockManager) Acquire(txid, entity string) (bool, error) {
	lm.mu.Lock()
	defer lm.mu.Unlock()
	holders := lm.holders[entity]
	if holders == nil {
		holders = map[string]bool{}
		lm.holders[entity] = holders
	}
	if len(holders) == 0 || (len(holders) == 1 && holders[txid]) {
		holders[txid] = true // 拿到锁, 等待边消失
		if lm.txEntities[txid] == nil {
			lm.txEntities[txid] = map[string]bool{}
		}
		lm.txEntities[txid][entity] = true
		delete(lm.waiting, txid)
		return true, nil
	}
	// 拿不到: 登记等待边(保留), 再检查等待图是否成环
	lm.waiting[txid] = entity
	if lm.findCycle(txid) {
		delete(lm.waiting, txid) // 死锁受害者放弃等待
		return false, &DeadlockError{txid, entity}
	}
	return false, nil // 调用方应阻塞/重试
}

// ReleaseAll 事务结束(提交或回滚)释放全部锁.
func (lm *LockManager) ReleaseAll(txid string) {
	lm.mu.Lock()
	defer lm.mu.Unlock()
	for entity := range lm.txEntities[txid] {
		delete(lm.holders[entity], txid)
	}
	delete(lm.txEntities, txid)
	delete(lm.waiting, txid)
}

// findCycle 等待图投影: 等待资源的 tx -> 持有该资源的 tx; 找回到起点的环.
func (lm *LockManager) findCycle(startTx string) bool {
	stack := []string{startTx}
	seen := map[string]bool{startTx: true}
	for len(stack) > 0 {
		tx := stack[len(stack)-1]
		stack = stack[:len(stack)-1]
		entity, blocked := lm.waiting[tx]
		if !blocked {
			continue
		}
		for holder := range lm.holders[entity] {
			if holder == startTx {
				return true // 回到起点: 成环
			}
			if !seen[holder] {
				seen[holder] = true
				stack = append(stack, holder)
			}
		}
	}
	return false
}

// Store read-committed 存储: 读不加锁, 写须先拿实体写锁.
type Store struct {
	mu    sync.Mutex
	data  map[string]int64
	locks *LockManager
}

func NewStore() *Store {
	return &Store{data: map[string]int64{}, locks: NewLockManager()}
}

func (s *Store) Read(key string) int64 { // 读不加锁(最后已提交值)
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.data[key]
}

func (s *Store) Write(key string, v int64) {
	s.mu.Lock()
	s.data[key] = v
	s.mu.Unlock()
}

// LostUpdateUnprotected 无写锁的丢失更新: 并发 +1, 最终值远小于 n.
func LostUpdateUnprotected(n int) int64 {
	s := NewStore()
	var wg sync.WaitGroup
	barrier := make(chan struct{})
	ready := sync.WaitGroup{}
	for i := 0; i < n; i++ {
		wg.Add(1)
		ready.Add(1)
		go func() {
			defer wg.Done()
			s.Write("warm", 0) // no-op 保证锁结构初始化
			ready.Done()
			<-barrier         // 全部就绪后同时放行
			v := s.Read("counter")
			s.Write("counter", v+1) // 各自读到同一旧值再写回
		}()
	}
	ready.Wait()
	close(barrier)
	wg.Wait()
	return s.data["counter"]
}

// LostUpdateProtected SET 右侧直接依赖被读属性 -> 自动写锁: 读写临界区串行化.
func LostUpdateProtected(n int) int64 {
	s := NewStore()
	var wg sync.WaitGroup
	barrier := make(chan struct{})
	ready := sync.WaitGroup{}
	for i := 0; i < n; i++ {
		wg.Add(1)
		ready.Add(1)
		go func(id int) {
			defer wg.Done()
			txid := fmt.Sprintf("tx-%d", id)
			ready.Done()
			<-barrier
			for { // 阻塞重试直到拿到锁
				ok, err := s.locks.Acquire(txid, "counter")
				if err != nil {
					panic(err) // 保护路径不应死锁(单一实体)
				}
				if ok {
					v := s.Read("counter")
					s.Write("counter", v+1)
					s.locks.ReleaseAll(txid) // 事务结束释放
					return
				}
			}
		}(i)
	}
	ready.Wait()
	close(barrier)
	wg.Wait()
	return s.data["counter"]
}

// CrossOrderDeadlock 交叉顺序: T1 持 A 等 B, T2 持 B 等 A -> 等待图成环.
func CrossOrderDeadlock() string {
	lm := NewLockManager()
	lm.Acquire("T1", "A")
	lm.Acquire("T2", "B")
	if _, err := lm.Acquire("T1", "B"); err != nil {
		return "unexpected"
	}
	_, err := lm.Acquire("T2", "A") // 环闭合 -> 死锁
	if err != nil {
		return "deadlock"
	}
	return "ok"
}

// SameOrderNoDeadlock 官方建议: 固定加锁顺序(先 A 后 B) -> 无环.
func SameOrderNoDeadlock() string {
	lm := NewLockManager()
	lm.Acquire("T1", "A")
	blocked, _ := lm.Acquire("T2", "A") // T2 只等 A, 不碰 B
	lm.Acquire("T1", "B")               // T1 按序拿完
	lm.ReleaseAll("T1")                 // T1 提交, 全部释放
	lm.Acquire("T2", "A")               // T2 重试成功
	lm.Acquire("T2", "B")
	if blocked {
		return "ok"
	}
	return "not-blocked"
}

func main() {
	fmt.Println("== 1. 读已提交: 读不加锁 -> 丢失更新 ==")
	fmt.Printf("  100 个并发 +1 无保护: 最终值 = %d (官方: 最坏低至 1)\n",
		LostUpdateUnprotected(100))

	fmt.Println("\n== 2. 写锁保护(SET 直接依赖被读属性 -> 自动加锁) ==")
	fmt.Printf("  100 个并发 +1 有写锁: 最终值 = %d (确定性 = 100)\n",
		LostUpdateProtected(100))

	fmt.Println("\n== 3. 哑属性技巧: 无直接依赖时手工加锁 ==")
	fmt.Println("  MATCH (n:Example {id:42}) SET n.dummy=true REMOVE n.dummy")
	fmt.Println("  -- 官方 workaround: 先写哑属性强制拿写锁, 再读 n.prop 计算新值")

	fmt.Println("\n== 4. 死锁: 交叉 vs 相同加锁顺序 ==")
	fmt.Printf("  T1(A→B) 与 T2(B→A) 交叉: %s (等待图成环, 终止其一)\n", CrossOrderDeadlock())
	fmt.Printf("  固定顺序(先 A 后 B): %s (官方建议防死锁)\n", SameOrderNoDeadlock())

	fmt.Println("\n== 5. 锁粒度(官方锁获取表) ==")
	for _, row := range [][2]string{
		{"创建/删除节点", "该节点写锁"},
		{"创建/删除关系", "该关系 + 两端节点写锁"},
		{"更新属性", "该节点/关系写锁"},
		{"更新标签", "该节点写锁"},
		{"密集节点(≥50 关系)", "共享度锁代替独占锁, 提交期才取精确排他锁"},
	} {
		fmt.Printf("  %-18s -> %s\n", row[0], row[1])
	}
}

// 缓存一致性模式的 Go 实现：写策略竞态枚举 + Facebook lease。
//
// 构建/运行： go run cache_consistency.go
package main

import "fmt"

// ------------------------------------------------------------ 竞态枚举

type Step int

const (
	WDel Step = iota
	WDBSet
	WAtomicBoth
	RDBGet
	RCacheSet
)

// simulate 按给定交错序执行。一致 = 缓存无该 key，或缓存值等于库值。
func simulate(seq []Step) bool {
	db, cachePresent, cache, read := 1, true, 1, 0
	for _, s := range seq {
		switch s {
		case WDel:
			cachePresent = false
		case WDBSet:
			db = 2
		case WAtomicBoth:
			db, cache, cachePresent = 2, 2, true
		case RDBGet:
			read = db
		case RCacheSet:
			cache, cachePresent = read, true
		}
	}
	return !cachePresent || cache == db
}

// staleCount 枚举所有保持内部顺序的交错（位掩码选出写者步骤的位置）。
func staleCount(w []Step) (total, bad int) {
	r := []Step{RDBGet, RCacheSet}
	L := len(w) + len(r)
	for mask := 0; mask < (1 << L); mask++ {
		bits := 0
		for i := 0; i < L; i++ {
			if (mask>>i)&1 == 1 {
				bits++
			}
		}
		if bits != len(w) {
			continue
		}
		seq := make([]Step, 0, L)
		wi, ri := 0, 0
		for i := 0; i < L; i++ {
			if (mask>>i)&1 == 1 {
				seq = append(seq, w[wi])
				wi++
			} else {
				seq = append(seq, r[ri])
				ri++
			}
		}
		total++
		if !simulate(seq) {
			bad++
		}
	}
	return
}

// ------------------------------------------------------------ Lease

const leaseTTL = 10 // 论文：每 key 每 10 秒只发一个 token

type state int

const (
	hit state = iota
	miss
	wait
	stale
)

type Lease struct {
	Token uint64
	Epoch int
}

type Srv struct {
	Has, Val             int
	Epoch                int
	HasLast, LastTokenAt int
	DeadHas, DeadVal     int
	Seq                  uint64
	DBReads              int
}

func (s *Srv) nextToken() uint64 {
	s.Seq = s.Seq*6364136223846793005 + 1442695040888963407
	return s.Seq
}

func (s *Srv) Get(now int, acceptStale bool, out *Lease) state {
	if s.Has == 1 {
		return hit
	}
	if acceptStale && s.DeadHas == 1 {
		return stale // 不消耗 token，不受限流
	}
	if s.HasLast == 1 && now-s.LastTokenAt < leaseTTL {
		return wait
	}
	s.HasLast, s.LastTokenAt = 1, now
	s.DBReads++
	if out != nil {
		out.Token, out.Epoch = s.nextToken(), s.Epoch
	}
	return miss
}

func (s *Srv) SetLease(val int, l *Lease) bool {
	if l.Epoch != s.Epoch {
		return false // token 已被 delete 作废
	}
	s.Has, s.Val, s.DeadHas = 1, val, 0
	return true
}

// SetPlain 无仲裁写入 —— 这正是 stale set 的成因。
func (s *Srv) SetPlain(val int) bool {
	s.Has, s.Val, s.DeadHas = 1, val, 0
	return true
}

func (s *Srv) Delete() {
	if s.Has == 1 {
		s.DeadHas, s.DeadVal, s.Has = 1, s.Val, 0
	}
	s.Epoch++
}

// thunderingHerd：n 个客户端并发读一个冷 key。
func thunderingHerd(withLease bool, n int) (dbReads, waits int) {
	s := &Srv{Seq: 3}
	var tok *Lease
	if withLease {
		tok = &Lease{}
	}
	for i := 0; i < n; i++ {
		var out *Lease
		if withLease {
			out = tok
		}
		switch s.Get(1000, false, out) {
		case miss:
			dbReads++
		case wait:
			waits++
			if !withLease {
				dbReads++ // 没有 lease 就没有「等」，每个客户端都去查库
			}
		}
	}
	if withLease {
		s.SetLease(2, tok)
	} else {
		for i := 0; i < n; i++ {
			s.SetPlain(2)
		}
	}
	return
}

// ------------------------------------------------------------ 自检

var fails []string

func check(label string, cond bool, detail string) {
	if cond {
		fmt.Printf("  ok   %s\n", label)
		return
	}
	fails = append(fails, label+" "+detail)
	fmt.Printf("  FAIL %s %s\n", label, detail)
}

func main() {
	fmt.Println("[1] 写顺序决定不一致窗口")
	t, b := staleCount([]Step{WDel, WDBSet})
	check("先删缓存再更新库 4/6", t == 6 && b == 4, fmt.Sprint(t, b))
	t, b = staleCount([]Step{WDBSet, WDel})
	check("先更新库再删缓存 1/6", t == 6 && b == 1, fmt.Sprint(t, b))
	t, b = staleCount([]Step{WDBSet, WDel, WDel})
	check("延迟双删 1/10", t == 10 && b == 1, fmt.Sprint(t, b))
	t, b = staleCount([]Step{WAtomicBoth})
	check("写穿透(单原子步) 1/3", t == 3 && b == 1, fmt.Sprint(t, b))

	fmt.Println("[2] 那条坏交错")
	check("删→读旧值→回填→库才更新 不一致",
		!simulate([]Step{WDel, RDBGet, RCacheSet, WDBSet}), "")
	check("写完整后读则一致",
		simulate([]Step{WDel, WDBSet, RDBGet, RCacheSet}), "")

	fmt.Println("[3] Lease")
	s := &Srv{Seq: 0x9E3779B97F4A7C15}
	tok := &Lease{}
	check("冷 key -> miss", s.Get(1000, false, tok) == miss, "")
	check("回写成功", s.SetLease(1, tok), "")
	check("读命中", s.Get(1001, false, nil) == hit, "")
	s.Delete()
	check("delete 作废在途 token", !s.SetLease(99, tok), "")
	check("无 lease 回写被接受(stale set)", s.SetPlain(99), "")
	check("缓存已污染", s.Val == 99, fmt.Sprint(s.Val))

	fmt.Println("[4] Stale value 与限流")
	s2 := &Srv{Seq: 1}
	t2 := &Lease{}
	s2.Get(2000, false, t2)
	s2.SetLease(1, t2)
	s2.Delete()
	check("accept_stale 返回 stale", s2.Get(2001, true, nil) == stale, "")
	check("stale 不限流", s2.Get(2002, true, nil) == stale, "")
	check("不接受 stale 则 wait", s2.Get(2002, false, nil) == wait, "")
	s3 := &Srv{Seq: 7}
	s3.Get(3000, false, nil)
	check("窗口内 wait", s3.Get(3009, false, nil) == wait, "")
	check("窗口外可再发 token", s3.Get(3010, false, nil) == miss, "")

	fmt.Println("[5] Thundering herd")
	nr, _ := thunderingHerd(false, 100)
	check("无 lease：100 次打库", nr == 100, fmt.Sprint(nr))
	nr2, w2 := thunderingHerd(true, 100)
	check("有 lease：1 次打库", nr2 == 1, fmt.Sprint(nr2))
	check("99 个收到稍等", w2 == 99, fmt.Sprint(w2))
	check("降低 100 倍", nr/nr2 == 100, fmt.Sprint(nr, nr2))
	check("论文实测 17K→1.3K = 13.08 倍",
		17000.0/1300.0 > 13.07 && 17000.0/1300.0 < 13.08, fmt.Sprint(17000.0/1300.0))

	fmt.Println()
	if len(fails) > 0 {
		fmt.Printf("FAILED %d\n", len(fails))
		return
	}
	fmt.Println("ALL PASS")
}

// Redis 键过期与淘汰策略的 Go 实现（与 redis_expiry.py 同构）。
package main

import "math"

// ------------------------------------------------------------ EXPIRE

const ttlNone = -1

// expireOpt：non-volatile 在 GT/LT 比较中视为 infinite TTL。
// 官方文档比的是**过期时间点**（绝对值），不是剩余 TTL。
func expireOpt(curExpireAt, ttl, now int, opt string) bool {
	cur := float64(curExpireAt)
	if curExpireAt == ttlNone {
		cur = math.Inf(1)
	}
	newAt := float64(now + ttl)
	switch opt {
	case "NX":
		return curExpireAt == ttlNone
	case "XX":
		return curExpireAt != ttlNone
	case "GT":
		return newAt > cur
	case "LT":
		return newAt < cur
	}
	return true
}

func clearsTTLStore(cmd string) bool { // *STORE 系列
	n := len(cmd)
	return n >= 5 && cmd[n-5:] == "STORE"
}

// -------------------------------------------------- activeExpireCycle

type aeParams struct {
	keysPerLoop, fastUs, slowPerc, acceptableStale int
}

func aeParamsOf(activeExpireEffort int) aeParams {
	e := activeExpireEffort - 1 // Rescale from 0 to 9
	return aeParams{
		keysPerLoop:     20 + 20/4*e,
		fastUs:          1000 + 1000/4*e,
		slowPerc:        25 + 2*e,
		acceptableStale: 10 - e,
	}
}

// ------------------------------------------------------------ LFU

const (
	lfuInitVal     = 5
	lfuMax         = 255
	lfuMinutesWrap = 65535
)

func lfuInitLRU(minutes int) int {
	return ((minutes & lfuMinutesWrap) << 8) | lfuInitVal
}
func lfuLDT(lru int) int     { return lru >> 8 }
func lfuCounter(lru int) int { return lru & 255 }

// lfuTimeElapsed：16 位分钟只环绕一次。
// 注意源码用 65535 而非 65536，跨回绕时会少算 1 分钟。
func lfuTimeElapsed(ldt, nowMinutes int) int {
	now := nowMinutes & lfuMinutesWrap
	if now >= ldt {
		return now - ldt
	}
	return lfuMinutesWrap - ldt + now
}

func lfuLogIncr(counter int, r float64, factor int) int {
	if counter == lfuMax {
		return lfuMax
	}
	base := float64(counter - lfuInitVal)
	if base < 0 {
		base = 0
	}
	p := 1.0 / (base*float64(factor) + 1)
	if r < p {
		return counter + 1
	}
	return counter
}

func lfuDecay(counter, ldt, nowMinutes, decayTime int) int {
	if decayTime == 0 {
		return counter // 0 = 永不衰减
	}
	periods := lfuTimeElapsed(ldt, nowMinutes) / decayTime
	if periods > 0 {
		if periods > counter {
			return 0
		}
		return counter - periods
	}
	return counter
}

// lfuExpected：均值场近似 c += 1/((c-5)*f+1)。
func lfuExpected(hits, factor int) float64 {
	c := float64(lfuInitVal)
	for i := 0; i < hits; i++ {
		if c >= lfuMax {
			return lfuMax
		}
		base := c - float64(lfuInitVal)
		if base < 0 {
			base = 0
		}
		c += 1.0 / (base*float64(factor) + 1)
	}
	if c < lfuMax {
		return c
	}
	return lfuMax
}

// lfuExpectedExact：精确马尔可夫链（仅小 hits 使用）。
func lfuExpectedExact(hits, factor int) float64 {
	dist := map[int]float64{lfuInitVal: 1.0}
	for i := 0; i < hits; i++ {
		nxt := map[int]float64{}
		for c, p := range dist {
			if c >= lfuMax {
				nxt[lfuMax] += p
				continue
			}
			base := c - lfuInitVal
			if base < 0 {
				base = 0
			}
			pr := 1.0 / (float64(base)*float64(factor) + 1)
			nxt[c+1] += p * pr
			nxt[c] += p * (1 - pr)
		}
		dist = nxt
	}
	var e float64
	for c, p := range dist {
		e += float64(c) * p
	}
	return e
}

// ------------------------------------------------------------ 策略

func evictionError(policy string, hasVolatileKeys bool) bool {
	if policy == "noeviction" {
		return true
	}
	if len(policy) >= 8 && policy[:8] == "volatile" && !hasVolatileKeys {
		return true // 官方文档：volatile-xxx behave like noeviction
	}
	return false
}

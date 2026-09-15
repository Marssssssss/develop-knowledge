// sync_world.go — 帧同步世界模型、校验和与确定性实验
//
// 与 lockstep.go 同属 package main。本文件承载:
//   B. 世界状态 + FNV-1a 校验和 + 多 peer 对比;
//   C. 浮点/定点的顺序敏感性与 Q16.16 舍入方向实验。
package main

import (
	"encoding/binary"
	"math"
	"sort"
)

// ==================================================== B. 世界与校验和

const (
	modeFixed = 0 // 定点池(Q16.16) + 整数加法: 与求和顺序无关
	modeFloat = 1 // 浮点池 + 浮点加法: 换顺序就分叉
)

var rateF = [nUnits]float64{1.0, 1.0 / 2, 1.0 / 3, 1.0 / 4, 1.0 / 5, 1.0 / 6, 1.0 / 7, 1.0 / 8}

var rateQ [nUnits]int64

func init() {
	for i := range rateQ {
		rateQ[i] = int64(rateF[i]*float64(scale) + 0.5) // 四舍五入到最近的 1/65536
	}
}

type wcmd struct {
	peer int
	uid  int
	dx   int64
	dy   int64
}

type world struct {
	mode     int
	extraRng bool // 故意每条指令多取一次随机数(破坏 RNG 同步)
	pool     int64
	poolF    float64
	px       [nUnits]int64
	hp       [nUnits]int64
	rng      uint32
}

func newWorld(mode int, extraRng bool) *world {
	w := &world{mode: mode, extraRng: extraRng, rng: 20260915}
	for i := 0; i < nUnits; i++ {
		w.hp[i] = 100
	}
	return w
}

func rngNext(s *uint32) uint32 {
	*s = uint32((uint64(*s)*1103515245 + 12345) & 0x7FFFFFFF)
	return *s
}

// step: 顺序无关地施加指令, 再用给定遍历顺序做「生产累积」(顺序敏感点),
// 最后消耗随机数。desc=true 表示反向遍历(模拟「不同编译器/不同实现顺序」)。
func (w *world) step(cmds []wcmd, desc bool) {
	c := append([]wcmd(nil), cmds...) // 复制, 避免排序污染调用方的切片
	sort.Slice(c, func(i, j int) bool {
		if c[i].uid != c[j].uid {
			return c[i].uid < c[j].uid
		}
		return c[i].peer < c[j].peer
	})
	for _, v := range c {
		w.px[v.uid] += v.dx
	}

	accI, accF := w.pool, w.poolF
	for i := 0; i < nUnits; i++ {
		k := i
		if desc {
			k = nUnits - 1 - i
		}
		if w.mode == modeFixed {
			accI += rateQ[k]
		} else {
			accF += rateF[k]
		}
	}
	w.pool, w.poolF = accI, accF

	r := rngNext(&w.rng)
	if w.extraRng {
		rngNext(&w.rng)
	}
	w.hp[r%nUnits]--
}

// checksum 用 FNV-1a 覆盖池、位置、血量与 RNG 状态。
func (w *world) checksum() uint64 {
	buf := make([]byte, 0, 128)
	if w.mode == modeFixed {
		buf = binary.LittleEndian.AppendUint64(buf, uint64(w.pool))
	} else {
		buf = binary.LittleEndian.AppendUint64(buf, math.Float64bits(w.poolF))
	}
	for i := 0; i < nUnits; i++ {
		buf = binary.LittleEndian.AppendUint64(buf, uint64(w.px[i]))
	}
	for i := 0; i < nUnits; i++ {
		buf = binary.LittleEndian.AppendUint64(buf, uint64(w.hp[i]))
	}
	buf = binary.LittleEndian.AppendUint32(buf, w.rng)

	var h uint64 = 0xCBF29CE484222325
	for _, b := range buf {
		h ^= uint64(b)
		h *= 0x100000001B3
	}
	return h
}

// localCmd: peer 在 turn 回合产生的本地指令(dx 单位为 1/65536 格)
func localCmd(peer, turn int) wcmd {
	return wcmd{
		peer: peer,
		uid:  (peer*3 + turn) % nUnits,
		dx:   int64(peer+1) * scale / 64,
		dy:   int64(turn%3 - 1),
	}
}

// runPeers 让 3 个 peer 跑同一份模拟, 逐回合比校验和。
// 返回首次失同步回合(0 = 全程一致) 与 是否全程一致。
func runPeers(mode, bugPeer int, extraRng bool) (int, bool) {
	ws := [3]*world{}
	for p := 0; p < 3; p++ {
		ws[p] = newWorld(mode, p == bugPeer && extraRng)
	}
	first, agree := 0, true
	for turn := 1; turn <= nTurns; turn++ {
		cmds := make([]wcmd, 3)
		for p := 0; p < 3; p++ {
			cmds[p] = localCmd(p, turn)
		}
		for p := 0; p < 3; p++ {
			ws[p].step(cmds, p == bugPeer) // bugPeer 反向遍历
		}
		if ws[0].checksum() != ws[1].checksum() || ws[0].checksum() != ws[2].checksum() {
			agree = false
			if first == 0 {
				first = turn
			}
		}
	}
	return first, agree
}

// ==================================================== C. 浮点 vs 定点

// firstOrderDivergence: 累加同一个多重集合, 一次升序一次降序, 找首次不等的回合。
func firstOrderDivergence() (int, float64, float64) {
	var asc, desc float64
	for t := 1; t <= 200; t++ {
		a, b := asc, desc
		for i := 0; i < nUnits; i++ {
			a += rateF[i]
		}
		for i := nUnits - 1; i >= 0; i-- {
			b += rateF[i]
		}
		asc, desc = a, b
		if asc != desc {
			return t, asc, desc
		}
	}
	return 0, asc, desc
}

// fixedOrderSum: 定点整数加法与顺序无关。
func fixedOrderSum() (int64, int64, bool) {
	var ia, ib int64
	for i := 0; i < nUnits; i++ {
		ia += rateQ[i]
	}
	for i := nUnits - 1; i >= 0; i-- {
		ib += rateQ[i]
	}
	return ia, ib, ia == ib
}

// mulRoundingDirections: 同一个 Q16.16 乘法, 算术右移(向下取整) 与 向零截断
// 在负数处分叉 —— 说明改用定点后仍必须跨 peer 约定同一个舍入方向。
func mulRoundingDirections() (int64, int64) {
	x := -(3 * scale / 2)
	y := scale / 3
	p := int64(x) * int64(y)
	arith := p >> 16
	var zero int64
	if p >= 0 {
		zero = p >> 16
	} else {
		zero = -((-p) >> 16)
	}
	return arith, zero
}

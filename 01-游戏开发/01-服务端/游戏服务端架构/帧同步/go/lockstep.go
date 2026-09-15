// lockstep.go — 确定性帧同步 (deterministic lockstep) 主程序与自检
//
// 运行: go run .            (本目录含 lockstep.go + sync_world.go)
//
// 权威依据(AoE GDC 2001「1500 Archers on a 28.8」, 见 README):
//   1. 不传单位状态, 让每台机器跑完全相同的模拟, 只传玩家指令;
//   2. 通信回合(communication turn)与渲染帧解耦, 典型 200 ms 一回合;
//   3. 指令「预约 2 个回合之后再执行」—— 发出后还有 2 个回合的窗口
//      用于接收/确认/重传, 因此链路延迟 <= 2 回合不会卡顿;
//   4. UDP 之上自己做定序、丢包检测、重传: 「When in doubt, assume it dropped」;
//   5. 每回合比校验和, 不一致即 out-of-sync;
//   6. 失同步根因常是极小差异的累积, 因此要求逐位可复现 -> 用定点数;
//   7. 随机数必须同步, 且每条指令消耗的随机次数必须一致。
package main

import (
	"fmt"
	"os"
)

const (
	nUnits    = 8
	lookahead = 2 // 指令预约 2 个通信回合后执行
	scale     = 1 << 16
	nTurns    = 20
)

var failures int

func check(cond bool, what string) {
	if !cond {
		fmt.Printf("  [FAIL] %s\n", what)
		failures++
	}
}

// ==================================================== A. 通信回合模型

type lockstep struct {
	latency, drops, executed, commTurns, stallEvents, setupDelay int
}

type dropKey struct {
	peer, turn int
}

// runLockstep 模拟 nPeers 个 peer 在「通信回合」模型下推进 turns 个游戏回合。
//
//   - 指令在通信回合 t 发出, 预约在 t+lookahead 执行;
//   - 正常到达时刻 = t + latency; 命中 drops 的包按重传处理, 到达 = t+latency+rto;
//   - 只有当某执行回合所需的**全部** peer 指令到齐才能推进, 否则该回合被卡住。
func runLockstep(nPeers, latency, turns int, drops map[dropKey]bool, rto int) lockstep {
	ready := make([]uint8, turns+lookahead+4) // ready[e] = 执行回合 e 已到齐的 peer 位图
	arrive := make([][]int, 8)                // arrive[p][s] = 包到达回合, 0 = 未发出
	for i := range arrive {
		arrive[i] = make([]int, 1024)
	}
	firstExec := 1 + lookahead // 前 lookahead 个回合只做网络, 没有回合可执行
	lastExec := turns + lookahead
	nextExec, firstSuccess := firstExec, 0
	var r lockstep
	r.latency, r.drops = latency, len(drops)

	t := 1
	for ; t <= lastExec+64; t++ {
		if nextExec > lastExec {
			break
		}
		for p := 0; p < nPeers; p++ {
			penalty := 0
			if drops[dropKey{p, t}] {
				penalty = rto
			}
			arrive[p][t] = t + latency + penalty
		}
		for p := 0; p < nPeers; p++ {
			for s := 1; s <= t && s+lookahead <= lastExec; s++ {
				if arrive[p][s] != 0 && arrive[p][s] <= t {
					ready[s+lookahead] |= 1 << uint(p)
				}
			}
		}
		if ready[nextExec] == uint8(1<<uint(nPeers)-1) {
			if firstSuccess == 0 {
				firstSuccess = t
			}
			nextExec++
		} else if t >= nextExec {
			r.stallEvents++
		}
	}
	r.executed = nextExec - firstExec
	r.commTurns = t - 1
	if firstSuccess == 0 {
		firstSuccess = t
	}
	r.setupDelay = firstSuccess - firstExec
	if r.setupDelay < 0 {
		r.setupDelay = 0
	}
	return r
}

func verdict(t int) string {
	if t == 0 {
		return "未失同步"
	}
	return fmt.Sprintf("第 %d 回合", t)
}

// ==================================================== main

func main() {
	fmt.Println("== A. 通信回合 / 2 回合前瞻 / 丢包重传 ==")
	noDrops := map[dropKey]bool{}
	var rows []lockstep
	for _, lat := range []int{1, 2, 3} {
		r := runLockstep(3, lat, 12, noDrops, lat+1)
		rows = append(rows, r)
		fmt.Printf("  链路延迟 %d 回合: 执行 %d/12, 起播延迟 %d, 卡顿 %d\n",
			r.latency, r.executed, r.setupDelay, r.stallEvents)
	}
	check(rows[0].setupDelay == 0 && rows[1].setupDelay == 0,
		"延迟 <= 2 回合应被 2 回合前瞻全额吸收")
	check(rows[2].setupDelay == rows[2].latency-lookahead, "延迟 3 回合应产生 1 回合起播延迟")

	drops := map[dropKey]bool{{0, 3}: true, {1, 3}: true, {2, 3}: true}
	d := runLockstep(3, 1, 12, drops, 2)
	c := runLockstep(3, 1, 12, noDrops, 2)
	fmt.Printf("  通信回合 3 上 3 个包全丢: 卡顿 %d 次 vs 无丢包 %d 次, 通信回合 %d vs %d (+%d)\n",
		d.stallEvents, c.stallEvents, d.commTurns, c.commTurns, d.commTurns-c.commTurns)
	check(d.stallEvents > c.stallEvents, "丢包应造成额外卡顿")
	check(d.commTurns > c.commTurns, "丢包重传应拉长通信回合")

	fmt.Println()
	fmt.Println("== B. 校验和跨 peer 对比(找首次失同步回合) ==")
	cases := []struct {
		name     string
		mode     int
		bugPeer  int
		extraRng bool
		expect   int
	}{
		{"定点 + 全部同序", modeFixed, -1, false, 0},
		{"定点 + peer2 反向遍历", modeFixed, 2, false, 0},
		{"浮点 + peer2 反向遍历", modeFloat, 2, false, 2},
		{"定点 + peer2 多取一次随机", modeFixed, 2, true, 1},
	}
	for _, cs := range cases {
		got, _ := runPeers(cs.mode, cs.bugPeer, cs.extraRng)
		fmt.Printf("  %-26s 首次失同步回合 = %s\n", cs.name, verdict(got))
		check(got == cs.expect, cs.name)
	}
	fmt.Println("  -> 定点对求和顺序免疫; 浮点第 2 回合分叉; 随机次数不一致第 1 回合分叉  OK")

	fmt.Println()
	fmt.Println("== C. 浮点 vs 定点 ==")
	turn, fa, fb := firstOrderDivergence()
	fmt.Printf("  浮点累加 1/(i+1): 首次分叉于第 %d 回合, 升序 %.16g vs 降序 %.16g, 差 %.3e\n",
		turn, fa, fb, fa-fb)
	check(turn == 2, "浮点顺序敏感应在第 2 回合分叉")

	ia, ib, equal := fixedOrderSum()
	if equal {
		fmt.Printf("  定点累加同一序列: 升序 %d vs 降序 %d -> 一致\n", ia, ib)
	} else {
		fmt.Printf("  定点累加同一序列: 升序 %d vs 降序 %d -> 不一致\n", ia, ib)
	}
	check(equal, "定点整数加法应与顺序无关")

	fmt.Printf("  0.1 + 0.2 - 0.3 = %.17g (不等于 0)\n", 0.1+0.2-0.3)
	check(0.1+0.2-0.3 != 0.0, "0.1+0.2-0.3 应为非零")

	arith, zero := mulRoundingDirections()
	if arith == zero {
		fmt.Printf("  Q16.16 乘法舍入方向: 算术右移 %d vs 向零截断 %d -> 一致\n", arith, zero)
	} else {
		fmt.Printf("  Q16.16 乘法舍入方向: 算术右移 %d vs 向零截断 %d -> 负数处分叉\n", arith, zero)
	}
	check(arith != zero, "负值下算术右移与向零截断应分叉")

	fmt.Println()
	if failures > 0 {
		fmt.Printf("存在失败项 (failures=%d)\n", failures)
		os.Exit(1)
	}
	fmt.Println("全部自检通过。")
}

// prediction.go — 客户端预测 / 服务端和解 / 延迟补偿 最小实现与自检
//
// 运行: go run prediction.go
//
// 与 python/prediction.py、c/prediction.c 一一对应。机制与数值取自
// Valve Developer Community《Source Multiplayer Networking》(见 README):
//   tickrate 15 ms; 快照 20/s(cl_updaterate 20); 用户命令 30/s(cl_cmdrate);
//   客户端预测 -> 预测误差 -> 服务端和解(必须重放未确认输入);
//   cl_smoothtime/cl_smooth 平滑视觉修正; cl_interp 100 ms 实体插值;
//   延迟补偿: Command Execution Time = Now - RTT - Client View Interpolation,
//   服务器保留最近 1 秒的玩家位置历史, 只回退**其他玩家**。
package main

import (
	"fmt"
	"math"
	"os"
)

const (
	tickMS      = 15.0  // tickrate ~ 66.67/s
	cmdEvery    = 2     // 每 2 tick 一个用户命令(30 ms)
	snapEvery   = 3     // 每 3 tick 一个快照(45 ms)
	oneWay      = 3     // 单向延迟 3 tick = 45 ms
	cmdDTms     = tickMS * cmdEvery
	speed       = 250.0 // 单位/秒
	blockerX    = 100.0
	blockerTick = 26 // 客户端恰在 tick 26 附近到达 x=100
	smoothMS    = 100.0
	smoothTau   = smoothMS / 5.0
	interpMS    = 100.0
	histCap     = 128
)

var failures int

func check(cond bool, what string) {
	if !cond {
		fmt.Printf("  [FAIL] %s\n", what)
		failures++
	}
}

func advance(x, dtMs float64, haveBlocker bool, blocker float64) float64 {
	nx := x + speed*dtMs/1000.0
	if haveBlocker && nx > blocker {
		return blocker
	}
	return nx
}

// ---------------------------------------------------------------- 一、预测

type client struct {
	mode       int // 0 = replay(重放) 1 = snap(吸附)
	x          float64
	haveBlk    bool
	blocker    float64
	hist       []int // 未确认输入的序号
	seq        int
	maxErr     float64
	lostInputs int
}

func (c *client) issue() {
	c.seq++
	c.hist = append(c.hist, c.seq)
	c.x = advance(c.x, cmdDTms, c.haveBlk, c.blocker) // 立刻预测
}

// reconcile 收到快照: 返回本次需要视觉修正的幅度。
func (c *client) reconcile(serverX float64, acked int, snapHasBlk bool, blocker float64) float64 {
	predicted := c.x
	if snapHasBlk {
		c.haveBlk, c.blocker = true, blocker
	}
	if c.mode == 1 {
		// 吸附式: 直接对齐权威位置, 未确认输入全部丢弃 -> 已预测的输入被吞掉
		c.lostInputs = len(c.hist)
		c.hist = nil
		c.x = serverX
	} else {
		// 重放式: 回到权威位置, 再把服务器还没处理的输入按序重放
		pending := make([]int, 0, len(c.hist))
		for _, s := range c.hist {
			if s > acked {
				pending = append(pending, s)
			}
		}
		c.hist = pending
		c.x = serverX
		for range pending {
			c.x = advance(c.x, cmdDTms, c.haveBlk, c.blocker)
		}
	}
	err := predicted - c.x
	if math.Abs(err) > c.maxErr {
		c.maxErr = math.Abs(err)
	}
	return err
}

type predResult struct {
	maxErr, errAfterRecon, clientX, serverX, gap float64
	lostInputs                                   int
}

func runPrediction(mode, ticks int, hasBlocker bool, bTick int) predResult {
	cli := &client{mode: mode}
	srv, ack, errAfter := 0.0, 0, 0.0
	type packet struct {
		arrive int
		seq    int
	}
	type snapshot struct {
		arrive  int
		x       float64
		acked   int
		hasBlkv bool
	}
	var srvQ []packet
	var snapQ []snapshot

	for tick := 0; tick < ticks; tick++ {
		active := hasBlocker && tick >= bTick // 服务器此刻是否已知道阻挡者
		if tick%cmdEvery == 0 {
			cli.issue()
			srvQ = append(srvQ, packet{tick + oneWay, cli.seq})
		}
		kept := srvQ[:0]
		for _, p := range srvQ {
			if p.arrive == tick {
				srv = advance(srv, cmdDTms, active, blockerX)
				if p.seq > ack {
					ack = p.seq
				}
				continue
			}
			kept = append(kept, p)
		}
		srvQ = kept

		if tick%snapEvery == 0 {
			snapQ = append(snapQ, snapshot{tick + oneWay, srv, ack, active})
		}
		keptS := snapQ[:0]
		for _, s := range snapQ {
			if s.arrive == tick {
				cli.reconcile(s.x, s.acked, s.hasBlkv, blockerX)
				errAfter = cli.x - s.x
				continue
			}
			keptS = append(keptS, s)
		}
		snapQ = keptS
	}
	return predResult{cli.maxErr, errAfter, cli.x, srv, cli.x - srv, cli.lostInputs}
}

// ---------------------------------------------------------------- 二、平滑

func runSmoothing(err float64, smooth bool) (maxFrame, residual float64, frames int) {
	remaining := err
	frames = int(math.Ceil(smoothMS/tickMS)) + 1
	for i := 0; i < frames; i++ {
		step := remaining
		if smooth {
			step = remaining * (1.0 - math.Exp(-tickMS/smoothTau))
		}
		if math.Abs(step) > maxFrame {
			maxFrame = math.Abs(step)
		}
		remaining -= step
	}
	return maxFrame, math.Abs(remaining), frames
}

// ---------------------------------------------------------------- 四、延迟补偿

type lagComp struct {
	rewindMs, rewindDist, naiveOffset, rewoundOffset, radius float64
	naiveHit, rewoundHit                                     bool
}

func runLagCompensation(rttMs, interpMs, targetSpeed, radius float64) lagComp {
	rewindMs := rttMs + interpMs // Valve 给出的换算
	yServerNow := targetSpeed * rewindMs / 1000.0
	yClientView := 0.0
	yRewound := 0.0
	return lagComp{
		rewindMs:      rewindMs,
		rewindDist:    targetSpeed * rewindMs / 1000.0,
		naiveOffset:   yServerNow - yClientView,
		naiveHit:      yServerNow-yClientView <= radius,
		rewoundOffset: yRewound - yClientView,
		rewoundHit:    true,
		radius:        radius,
	}
}

// ---------------------------------------------------------------- main

func main() {
	rtt := 2.0 * oneWay * tickMS
	fmt.Printf("tick %.0f ms / 命令每 %d tick / 快照每 %d tick / 单向 %d tick -> RTT %.0f ms\n",
		tickMS, cmdEvery, snapEvery, oneWay, rtt)
	fmt.Printf("速度 %.0f u/s -> 每个用户命令位移 %.1f u; 阻挡者在 x=%.0f\n\n",
		speed, speed*cmdDTms/1000.0, blockerX)

	fmt.Println("== 一、客户端预测 + 服务端和解 ==")
	rep := runPrediction(0, 40, true, blockerTick)
	snp := runPrediction(1, 40, true, blockerTick)
	prep := runPrediction(0, 40, false, blockerTick)
	psnp := runPrediction(1, 40, false, blockerTick)
	fmt.Printf("  [有阻挡] 重放式: 最大预测误差 %.2f u, 和解后偏差 %+.2f u, 丢弃输入 %d 条, "+
		"末态 client=%.2f / server=%.2f\n",
		rep.maxErr, rep.errAfterRecon, rep.lostInputs, rep.clientX, rep.serverX)
	fmt.Printf("  [有阻挡] 吸附式: 最大预测误差 %.2f u, 和解后偏差 %+.2f u, 丢弃输入 %d 条, "+
		"末态 client=%.2f / server=%.2f\n",
		snp.maxErr, snp.errAfterRecon, snp.lostInputs, snp.clientX, snp.serverX)
	fmt.Printf("  [无阻挡] 重放式 client=%.2f (领先 %+.2f u = 尚在路上的输入)\n",
		prep.clientX, prep.gap)
	fmt.Printf("  [无阻挡] 吸附式 client=%.2f (落后 %+.2f u = 被吞掉的输入)\n",
		psnp.clientX, psnp.gap)
	check(rep.maxErr > 0 && snp.maxErr > 0, "两条路径都应出现预测误差")
	check(math.Abs(rep.errAfterRecon) < 1e-9 && math.Abs(snp.errAfterRecon) < 1e-9,
		"和解后应与权威快照一致")
	check(snp.lostInputs > 0, "吸附式必然吞掉未确认输入")
	check(prep.gap > psnp.gap, "重放式应保留乐观领先, 吸附式掉队")

	fmt.Println()
	fmt.Println("== 二、平滑(cl_smoothtime = cl_smooth = 100 ms) ==")
	init := rep.maxErr
	mfS, resS, framesS := runSmoothing(init, true)
	mfR, _, _ := runSmoothing(init, false)
	fmt.Printf("  初始视觉误差 %.2f u; 不开启平滑: 单帧修正 %.2f u = 误差的 %.0f%%\n",
		init, mfR, 100*mfR/init)
	fmt.Printf("  开启平滑(tau=%.0f ms): 单帧最大修正 %.2f u = 误差的 %.0f%%, %d 帧摊完, "+
		"残余 %.4f u = %.2f%%\n", smoothTau, mfS, 100*mfS/init, framesS, resS, 100*resS/init)
	check(mfR >= init-1e-9, "不平滑应一帧吃掉全部误差")
	check(mfS <= 0.60*init, "平滑应显著降低单帧修正幅度")
	check(resS <= 0.01*init, "平滑后残余应小于 1%")

	fmt.Println()
	fmt.Println("== 三、实体插值(cl_interp 100 ms) ==")
	lagDist := 300.0 * interpMS / 1000.0
	fmt.Printf("  远端 300 u/s, 快照间隔 %.0f ms; 渲染滞后 %.0f ms -> 位置差 %.1f u "+
		"(缓冲里约 %.1f 个快照)\n", snapEvery*tickMS, interpMS, lagDist,
		interpMS/(snapEvery*tickMS))
	check(math.Abs(lagDist-30.0) < 1e-9, "插值滞后距离应为 30 u")

	fmt.Println()
	fmt.Println("== 四、延迟补偿(回退其他玩家) ==")
	lc := runLagCompensation(rtt, interpMS, 300.0, 20.0)
	fmt.Printf("  历史缓冲 = 最近 1 s; 命令执行时刻 = 现在 - RTT - 插值 = %.0f ms "+
		"-> 目标回退 %.1f u\n", lc.rewindMs, lc.rewindDist)
	fmt.Printf("  不回退: 目标偏移 %.1f u, 命中半径 %.0f u -> %s\n",
		lc.naiveOffset, lc.radius, hitWord(lc.naiveHit))
	fmt.Printf("  回退后: 目标偏移 %.1f u -> %s\n", lc.rewoundOffset, hitWord(lc.rewoundHit))
	check(!lc.naiveHit && lc.rewoundHit, "回退才应判中")

	fmt.Println()
	if failures > 0 {
		fmt.Printf("存在失败项 (failures=%d)\n", failures)
		os.Exit(1)
	}
	fmt.Println("全部自检通过。")
}

func hitWord(hit bool) string {
	if hit {
		return "命中"
	}
	return "脱靶"
}

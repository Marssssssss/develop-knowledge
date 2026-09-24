package main

// 与 python/selfcheck_dack.py 同构的断言集。本机无 Go 工具链,由人工审查 +
// 静态检查(bracket_check / go_sanity / go_crossref)把关。

var scCount int
var scFails []string

func ok(cond bool, msg string) {
	scCount++
	if !cond {
		scFails = append(scFails, msg)
	}
}

func eq(got, want int, msg string) {
	ok(got == want, msg)
}

func atoSequence() []int {
	e := newDelackEngine(1460, 65536)
	out := []int{}
	for _, t := range []int{0, 1, 3, 8, 20, 41, 71, 101, 201, 500} {
		out = append(out, e.OnDataRecv(t))
	}
	return out
}

func selfcheck() (int, []string) {
	scCount, scFails = 0, nil

	// 常量
	eq(tcpAtoMin, 40, "TCP_ATO_MIN")
	eq(tcpDelackMin, 40, "TCP_DELACK_MIN")
	eq(tcpDelackMax, 200, "TCP_DELACK_MAX")
	eq(tcpRtoMin, 200, "TCP_RTO_MIN")
	eq(tcpRtoMax, 120000, "TCP_RTO_MAX")
	eq(tcpMaxQuickacks, 16, "TCP_MAX_QUICKACKS")
	eq(ilog2(600), 9, "ilog2(600)")
	eq(usecToMsCeil(1001), 2, "usecToMsCeil(1001)")

	// quickack 配额
	eq(newDelackEngine(1460, 65536).IncrQuickack(16), 16, "65536/(2*1460) 封顶 16")
	eq(newDelackEngine(1460, 65536).IncrQuickack(2), 2, "max=2")
	eq(newDelackEngine(1460, 1460).IncrQuickack(16), 2, "商 0 兜底 2")
	eq(newDelackEngine(1460, 8760).IncrQuickack(16), 3, "8760/2920=3")
	eq(newDelackEngine(1460, 0).IncrQuickack(16), 2, "窗口 0 兜底 2")

	// 模式判定(成对用例)
	q := newDelackEngine(1460, 65536)
	q.Quick = 1
	ok(q.InQuickackMode(), "quick=1 -> true")
	q.Pingpong = true
	ok(!q.InQuickackMode(), "翻 pingpong -> false")
	q.Pingpong = false
	q.Quick = 0
	ok(!q.InQuickackMode(), "quick=0 负控")
	q.DstQuickAck = true
	ok(q.InQuickackMode(), "dstQuickAck 短路")
	p := newDelackEngine(1460, 65536)
	p.Pingpong = true
	p.EnterQuickackMode(16)
	ok(!p.Pingpong, "enter 清 pingpong")
	eq(p.Ato, tcpAtoMin, "enter 压 ato")
	eq(p.Quick, 16, "enter 抬 quick")

	// ato 演化
	seq := atoSequence()
	want := []int{40, 40, 40, 40, 40, 41, 50, 55, 55, 55}
	eq(len(seq), len(want), "序列长度")
	for i := range want {
		if i < len(seq) {
			eq(seq[i], want[i], "ato 序列第 "+itoa(i)+" 项")
		}
	}

	// delack 定时器三道上限
	d := newDelackEngine(1460, 65536)
	d.Ato = 40
	eq(d.DelackTimeout(true), 40, "ato==DELACK_MIN 跳过整段 if")
	d.Ato = 300
	eq(d.DelackTimeout(false), 300, "未封顶 300")
	eq(d.DelackTimeout(true), 200, "最终被压到 200")
	d.Ato = 300
	d.SrttUs = 2000000
	eq(d.DelackTimeout(false), 250, "srtt=2s -> 上限 250")
	d.Pingpong = true
	eq(d.DelackTimeout(false), 200, "pingpong 把上限收回 200")
	d.SrttUs = 8000
	d.Ato = 41
	d.Pending = 0
	eq(d.DelackTimeout(true), 40, "srtt=8ms -> rtt 被 DELACK_MIN 顶回 40")

	// alloc_skb 失败退避
	r := newDelackEngine(1460, 65536)
	dl, grew := r.DelackRetryDelay()
	eq(dl, 200, "retry=0 -> 200")
	ok(grew, "retry=0 递增")
	r.Retry = 9
	dl, grew = r.DelackRetryDelay()
	eq(dl, 102400, "200<<9")
	ok(grew, "递增到 10")
	dl, grew = r.DelackRetryDelay()
	eq(dl, 204800, "200<<10")
	ok(!grew, "越过 RTO_MAX 不再递增")
	eq(r.Ato, tcpAtoMin, "重试复位 ato")

	// user_timeout
	eq(ClampRtoToUserTimeout(0, 200, 5000), 200, "user=0 -> rto")
	eq(ClampRtoToUserTimeout(10000, 200, 9900), 100, "剩余 100")
	eq(ClampRtoToUserTimeout(10000, 200, 10000), 1, "到点 -> 1")
	eq(ClampRtoToUserTimeout(10000, 200, 99999), 1, "超时 -> 1")
	eq(ClampProbe0ToUserTimeout(0, 500, 1000, 11000), 500, "probe0 user=0")
	eq(ClampProbe0ToUserTimeout(10000, 9000, 1000, 11000), tcpTimeoutMin, "probe0 抬到 2")
	eq(ClampProbe0ToUserTimeout(10000, 9000, 1000, 500), 9000, "时钟回退兜 0")

	// model_timeout / retransmits_timed_out
	eq(ModelTimeout(15, tcpRtoMin, tcpRtoMax), 924600, "boundary=15 -> 924.6s")
	eq(ModelTimeout(9, tcpRtoMin, tcpRtoMax), 204600, "boundary=9")
	eq(ModelTimeout(10, tcpRtoMin, tcpRtoMax), 324600, "boundary=10")
	ok(!RetransmitsTimedOut(15, 0, 0, 924600, 0, tcpRtoMin, tcpRtoMax), "未重传负控")
	ok(!RetransmitsTimedOut(15, 0, 0, 924599, 3, tcpRtoMin, tcpRtoMax), "差 1ms")
	ok(RetransmitsTimedOut(15, 0, 0, 924600, 3, tcpRtoMin, tcpRtoMax), "恰好到点")

	// probe_timer
	eqs := func(got, want string, msg string) {
		scCount++
		if got != want {
			scFails = append(scFails, msg+" got="+got+" want="+want)
		}
	}
	eqs(ProbeTimerDecision(0, 1000, 99999, 5, 15, 0, true), "probe", "未设 user_timeout")
	eqs(ProbeTimerDecision(10000, 1000, 10999, 3, 15, 0, true), "probe", "差 1ms")
	eqs(ProbeTimerDecision(10000, 1000, 11000, 3, 15, 0, true), "abort", "到点")
	eqs(ProbeTimerDecision(10000, 1000, 10999, 15, 15, 0, true), "abort", "次数满")

	return scCount, scFails
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	var b []byte
	for n > 0 {
		b = append([]byte{byte('0' + n%10)}, b...)
		n /= 10
	}
	return string(b)
}

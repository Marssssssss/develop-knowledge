package main

// pollRuntimePollSetDeadline 对应 poll_runtime_pollSetDeadline。
func pollRuntimePollSetDeadline(np *Netpoll, pd *PollDesc, d, now int64, mode int) bool {
	rd0, wd0 := pd.rd, pd.wd
	combo0 := rd0 > 0 && rd0 == wd0
	if d > 0 {
		d += now
		if d <= 0 { // 溢出（Go 里是回绕成负数）
			d = maxDeadline
		}
	}
	if mode == modeR || mode == modeRW {
		pd.rd = d
	}
	if mode == modeW || mode == modeRW {
		pd.wd = d
	}
	combo := pd.rd > 0 && pd.rd == pd.wd
	if !pd.rrun {
		if pd.rd > 0 {
			pd.rseq++
			pd.rrun = true
		}
	} else if pd.rd != rd0 || combo != combo0 {
		pd.rseq++ // 让旧 timer 失效
		if pd.rd <= 0 {
			pd.rrun = false
		}
	}
	if !pd.wrun {
		if pd.wd > 0 && !combo {
			pd.wseq++
			pd.wrun = true
		}
	} else if pd.wd != wd0 || combo != combo0 {
		pd.wseq++
		if !(pd.wd > 0 && !combo) {
			pd.wrun = false
		}
	}
	var delta int32
	if pd.rd < 0 {
		g, d := netpollunblock(pd, modeR, false, delta)
		delta = d
		if g != 0 {
			pd.ready = append(pd.ready, g)
		}
	}
	if pd.wd < 0 {
		g, d := netpollunblock(pd, modeW, false, delta)
		delta = d
		if g != 0 {
			pd.ready = append(pd.ready, g)
		}
	}
	np.AdjustWaiters(delta)
	return combo
}

// netpolldeadlineimpl 对应 netpoll.go 的 netpolldeadlineimpl。
func netpolldeadlineimpl(np *Netpoll, pd *PollDesc, seq uintptr, read, write bool) bool {
	if read {
		if pd.rd <= 0 || !pd.rrun {
			panic("runtime: inconsistent read deadline")
		}
		if seq != pd.rseq {
			return false // 描述符被复用或 timer 被重置 → 丢弃
		}
		pd.rd = -1
		pd.expiredRead = true
	}
	if write {
		if pd.wd <= 0 || (!pd.wrun && !read) {
			panic("runtime: inconsistent write deadline")
		}
		if seq != pd.wseq {
			return false
		}
		pd.wd = -1
		pd.expiredWrite = true
	}
	var delta int32
	if read {
		g, d := netpollunblock(pd, modeR, false, delta)
		delta = d
		if g != 0 {
			pd.ready = append(pd.ready, g)
		}
	}
	if write {
		g, d := netpollunblock(pd, modeW, false, delta)
		delta = d
		if g != 0 {
			pd.ready = append(pd.ready, g)
		}
	}
	np.AdjustWaiters(delta)
	return true
}

// pollRuntimePollUnblock 对应 poll_runtime_pollUnblock。
func pollRuntimePollUnblock(np *Netpoll, pd *PollDesc) (uintptr, uintptr) {
	if pd.closing {
		panic("runtime: unblock on closing polldesc")
	}
	pd.closing = true
	pd.rseq++
	pd.wseq++
	var delta int32
	rg, delta := netpollunblock(pd, modeR, false, delta)
	wg, delta := netpollunblock(pd, modeW, false, delta)
	pd.rrun = false
	pd.wrun = false
	if rg != 0 {
		pd.ready = append(pd.ready, rg)
	}
	if wg != 0 {
		pd.ready = append(pd.ready, wg)
	}
	np.AdjustWaiters(delta)
	return rg, wg
}

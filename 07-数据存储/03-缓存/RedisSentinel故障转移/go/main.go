package main

import "fmt"

const now0 = int64(1000000)

func main() {
	zero := func() int { return 0 }

	fmt.Println("=== 1. SDOWN -> ODOWN（quorum=2，共 3 个 sentinel）===")
	master := NewInstance("mymaster", "M", 2, "10.0.0.100", 6379)
	master.Sentinels["S1"] = NewInstance("s1", "S1", 0, "", 0)
	master.Sentinels["S2"] = NewInstance("s2", "S2", 0, "", 0)
	me := &Sentinel{MyID: "S0"}
	master.Flags |= SRI_SDOWN
	master.SDownSinceTime = now0
	fmt.Println("  只有我判定 SDOWN ->", me.CheckObjectivelyDown(master, now0, nil))
	master.Sentinels["S1"].Flags |= SRI_MASTERDOWN
	fmt.Println("  S1 也报告 master down ->",
		me.CheckObjectivelyDown(master, now0, nil), "(quorum 计数 2/2)")

	fmt.Println("\n=== 2. 纪元单调的领导者选举 ===")
	master.ODownSinceTime = now0
	master.FailoverStartTime = now0 - SentinelDefaultFailoverTimeo*2
	fmt.Println("  启动故障转移:", me.StartFailoverIfNeeded(master, now0, zero))
	fmt.Printf("  failover_epoch=%d current_epoch=%d state=%s\n",
		master.FailoverEpoch, me.CurrentEpoch, FailoverStateNames[master.FailoverState])

	fmt.Println("\n=== 3. 选出要提升的从节点 ===")
	offsets := []int64{1000, 900, 1200}
	priorities := []int{100, 100, 50}
	for i := 0; i < 3; i++ {
		s := NewInstance(fmt.Sprintf("slave%d", i), fmt.Sprintf("R%d", i), 0,
			fmt.Sprintf("10.0.0.%d", i+1), 6379)
		s.Flags |= SRI_SLAVE
		s.SlaveReplOffset = offsets[i]
		s.SlavePriority = priorities[i]
		s.LastAvailTime = now0
		s.InfoRefresh = now0
		master.Slaves[s.RunID] = s
	}
	master.FailoverState = StateSelectSlave
	events := []string{}
	me.FailoverStateMachine(master, now0, zero, &events)
	fmt.Println("  事件:", events)
	fmt.Println("  被提升:", master.PromotedSlave.Name, "->",
		FailoverStateNames[master.FailoverState])

	fmt.Println("\n=== 4. 其余从节点指向新 master ===")
	me.FailoverStateMachine(master, now0, zero, &events)
	master.FailoverState = StateReconfSlaves
	master.FailoverStateChangeTime = now0
	for i := 0; i < 4; i++ {
		me.FailoverStateMachine(master, now0, zero, &events)
		for _, s := range master.Slaves {
			if s == master.PromotedSlave {
				continue
			}
			s.SlaveMasterHost = master.PromotedSlave.AddrHost
			s.SlaveMasterPort = master.PromotedSlave.AddrPort
			s.SlaveMasterLinkStatus = MasterLinkUp
			RefreshSlaveReconf(master, s, &events)
		}
	}
	fmt.Println("  最终状态:", FailoverStateNames[master.FailoverState])
	fmt.Println("  事件流尾部:", events[max(0, len(events)-6):])
}

func max(a, b int) int {
	if a > b {
		return a
	}
	return b
}

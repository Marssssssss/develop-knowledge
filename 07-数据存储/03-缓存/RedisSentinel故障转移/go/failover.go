package main

// AbortFailover 对应 src/sentinel.c:5404：只能在 <= WAIT_PROMOTION 时中止。
func AbortFailover(m *Instance, now int64) {
	m.Flags &= ^(SRI_FAILOVERINPROGRESS | SRI_FORCEFAILOVER)
	m.FailoverState = StateNone
	m.FailoverStateChangeTime = now
	if m.PromotedSlave != nil {
		m.PromotedSlave.Flags &= ^SRI_PROMOTED
		m.PromotedSlave = nil
	}
}

// FailoverWaitStart 对应 src/sentinel.c:5151：不是 leader 且超过选举超时就放弃。
func (s *Sentinel) FailoverWaitStart(m *Instance, now int64, rnd func() int, events *[]string) {
	leader := s.GetLeader(m, m.FailoverEpoch, now, rnd)
	if leader != s.MyID && m.Flags&SRI_FORCEFAILOVER == 0 {
		electionTimeout := int64(SentinelElectionTimeout)
		if electionTimeout > m.FailoverTimeout {
			electionTimeout = m.FailoverTimeout
		}
		if now-m.FailoverStartTime > electionTimeout {
			if events != nil {
				*events = append(*events, "-failover-abort-not-elected")
			}
			AbortFailover(m, now)
		}
		return
	}
	if events != nil {
		*events = append(*events, "+elected-leader")
	}
	m.FailoverState = StateSelectSlave
	m.FailoverStateChangeTime = now
}

// FailoverSelectSlave 对应 src/sentinel.c:5178：选不到好从节点直接中止。
func FailoverSelectSlave(m *Instance, now int64, events *[]string) {
	slave := SelectSlave(m, now)
	if slave == nil {
		if events != nil {
			*events = append(*events, "-failover-abort-no-good-slave")
		}
		AbortFailover(m, now)
		return
	}
	if events != nil {
		*events = append(*events, "+selected-slave "+slave.Name)
	}
	slave.Flags |= SRI_PROMOTED
	m.PromotedSlave = slave
	m.FailoverState = StateSendSlaveofNoone
	m.FailoverStateChangeTime = now
}

// FailoverSendSlaveofNoone 对应 src/sentinel.c:5194。
func FailoverSendSlaveofNoone(m *Instance, now int64, events *[]string) {
	if m.PromotedSlave.LinkDisconnected {
		if now-m.FailoverStateChangeTime > m.FailoverTimeout {
			if events != nil {
				*events = append(*events, "-failover-abort-slave-timeout")
			}
			AbortFailover(m, now)
		}
		return
	}
	if events != nil {
		*events = append(*events, "+failover-state-wait-promotion")
	}
	m.FailoverState = StateWaitPromotion
	m.FailoverStateChangeTime = now
}

// FailoverWaitPromotion 对应 src/sentinel.c:5227：这一态只处理超时。
func FailoverWaitPromotion(m *Instance, now int64, events *[]string) {
	if now-m.FailoverStateChangeTime > m.FailoverTimeout {
		if events != nil {
			*events = append(*events, "-failover-abort-slave-timeout")
		}
		AbortFailover(m, now)
	}
}

// FailoverDetectEnd 对应 src/sentinel.c:5241。
func FailoverDetectEnd(m *Instance, now int64, events *[]string) {
	if m.PromotedSlave == nil || m.PromotedSlave.Flags&SRI_SDOWN != 0 {
		return
	}
	notReconfigured := 0
	for _, s := range m.Slaves {
		if s.Flags&(SRI_PROMOTED|SRI_RECONFDONE) != 0 {
			continue
		}
		if s.Flags&SRI_SDOWN != 0 {
			continue
		}
		notReconfigured++
	}
	if now-m.FailoverStateChangeTime > m.FailoverTimeout {
		notReconfigured = 0
		if events != nil {
			*events = append(*events, "+failover-end-for-timeout")
		}
	}
	if notReconfigured == 0 {
		if events != nil {
			*events = append(*events, "+failover-end")
		}
		m.FailoverState = StateUpdateConfig
		m.FailoverStateChangeTime = now
	}
}

// FailoverReconfNextSlave 对应 src/sentinel.c:5292：并发受 ParallelSyncs 限制。
func FailoverReconfNextSlave(m *Instance, now int64, events *[]string) {
	inProgress := 0
	for _, s := range m.Slaves {
		if s.Flags&(SRI_RECONFSENT|SRI_RECONFINPROG) != 0 {
			inProgress++
		}
	}
	for _, s := range m.Slaves {
		if inProgress >= m.ParallelSyncs {
			break
		}
		if s.Flags&(SRI_PROMOTED|SRI_RECONFDONE) != 0 {
			continue
		}
		// 注意：置 DONE 后源码**没有** continue，会继续走到下面再发一次 SLAVEOF。
		if s.Flags&SRI_RECONFSENT != 0 &&
			now-s.SlaveReconfSentTime > SentinelSlaveReconfTimeout {
			s.Flags &= ^SRI_RECONFSENT
			s.Flags |= SRI_RECONFDONE
		}
		if s.Flags&(SRI_RECONFSENT|SRI_RECONFINPROG) != 0 {
			continue
		}
		if s.LinkDisconnected {
			continue
		}
		s.Flags |= SRI_RECONFSENT
		s.SlaveReconfSentTime = now
		inProgress++
		if events != nil {
			*events = append(*events, "+slave-reconf-sent "+s.Name)
		}
	}
	FailoverDetectEnd(m, now, events)
}

// RefreshSlaveReconf 对应 src/sentinel.c:2748：SENT→INPROG→DONE 由 INFO 解析驱动，
// **不在状态机里**。
func RefreshSlaveReconf(m *Instance, s *Instance, events *[]string) {
	if s.Flags&SRI_SLAVE == 0 {
		return
	}
	if s.Flags&(SRI_RECONFSENT|SRI_RECONFINPROG) == 0 {
		return
	}
	if s.Flags&SRI_RECONFSENT != 0 && m.PromotedSlave != nil &&
		s.SlaveMasterHost == m.PromotedSlave.AddrHost &&
		s.SlaveMasterPort == m.PromotedSlave.AddrPort {
		s.Flags &= ^SRI_RECONFSENT
		s.Flags |= SRI_RECONFINPROG
		if events != nil {
			*events = append(*events, "+slave-reconf-inprog "+s.Name)
		}
	}
	if s.Flags&SRI_RECONFINPROG != 0 && s.SlaveMasterLinkStatus == MasterLinkUp {
		s.Flags &= ^SRI_RECONFINPROG
		s.Flags |= SRI_RECONFDONE
		if events != nil {
			*events = append(*events, "+slave-reconf-done "+s.Name)
		}
	}
}

// FailoverStateMachine 对应 src/sentinel.c:5374。
func (s *Sentinel) FailoverStateMachine(m *Instance, now int64, rnd func() int, events *[]string) {
	if m.Flags&SRI_FAILOVERINPROGRESS == 0 {
		return
	}
	switch m.FailoverState {
	case StateWaitStart:
		s.FailoverWaitStart(m, now, rnd, events)
	case StateSelectSlave:
		FailoverSelectSlave(m, now, events)
	case StateSendSlaveofNoone:
		FailoverSendSlaveofNoone(m, now, events)
	case StateWaitPromotion:
		FailoverWaitPromotion(m, now, events)
	case StateReconfSlaves:
		FailoverReconfNextSlave(m, now, events)
	}
}

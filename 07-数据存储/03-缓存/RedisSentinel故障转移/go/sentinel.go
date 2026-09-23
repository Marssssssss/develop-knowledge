// Package main 复刻 redis/redis@unstable src/sentinel.c 的下线判定、领导者选举与从节点挑选。
package main

// 实例标志位（src/sentinel.c:47-56）
const (
	SRI_SLAVE      = 1 << 1
	SRI_SDOWN      = 1 << 3
	SRI_ODOWN      = 1 << 4
	SRI_MASTERDOWN = 1 << 5
	SRI_FAILOVERINPROGRESS = 1 << 6
	SRI_PROMOTED           = 1 << 7
	SRI_RECONFSENT         = 1 << 8
	SRI_RECONFINPROG      = 1 << 9
	SRI_RECONFDONE        = 1 << 10
	SRI_LEADER            = 1 << 17
	SRI_FORCEFAILOVER     = 1 << 18
)

// 故障转移七态（src/sentinel.c:90-96）
const (
	StateNone = iota
	StateWaitStart
	StateSelectSlave
	StateSendSlaveofNoone
	StateWaitPromotion
	StateReconfSlaves
	StateUpdateConfig
)

// 时间与并发常量（src/sentinel.c:63-81）
const (
	SentinelPingPeriod           = 1000
	SentinelInfoPeriod           = 10000
	SentinelMaxDesync            = 1000
	SentinelElectionTimeout      = 10000
	SentinelSlaveReconfTimeout   = 10000
	SentinelDefaultDownAfter     = 30000
	SentinelDefaultFailoverTimeo = 60 * 3 * 1000
	SentinelDefaultParallelSyncs = 1
)

// 链路状态
const (
	MasterLinkDown = 0
	MasterLinkUp   = 1
)

// FailoverStateNames 对应 src/sentinel.c:3401 的 sentinelFailoverStateStr。
var FailoverStateNames = map[int]string{
	StateNone:             "none",
	StateWaitStart:        "wait_start",
	StateSelectSlave:      "select_slave",
	StateSendSlaveofNoone: "send_slaveof_noone",
	StateWaitPromotion:    "wait_promotion",
	StateReconfSlaves:     "reconf_slaves",
	StateUpdateConfig:     "update_config",
}

// Instance 建模 sentinelRedisInstance。
type Instance struct {
	Name  string
	RunID string
	Flags int

	AddrHost string
	AddrPort int

	Quorum        int
	Sentinels     map[string]*Instance
	Slaves        map[string]*Instance
	Leader        string
	LeaderEpoch   int64
	FailoverState int
	FailoverStateChangeTime int64
	FailoverEpoch           int64
	FailoverStartTime       int64
	PromotedSlave           *Instance
	ParallelSyncs           int
	DownAfterPeriod         int64
	FailoverTimeout         int64
	SDownSinceTime          int64
	ODownSinceTime          int64

	SlavePriority          int
	SlaveReplOffset        int64
	MasterLinkDownTime     int64
	SlaveReconfSentTime    int64
	SlaveMasterHost        string
	SlaveMasterPort        int
	SlaveMasterLinkStatus  int

	LinkDisconnected bool
	LastAvailTime    int64
	InfoRefresh      int64
}

// NewInstance 建一个带默认配置的实例。
func NewInstance(name, runID string, quorum int, addrHost string, addrPort int) *Instance {
	return &Instance{
		Name: name, RunID: runID, AddrHost: addrHost, AddrPort: addrPort,
		Quorum: quorum, Sentinels: map[string]*Instance{}, Slaves: map[string]*Instance{},
		ParallelSyncs: SentinelDefaultParallelSyncs,
		DownAfterPeriod: SentinelDefaultDownAfter,
		FailoverTimeout: SentinelDefaultFailoverTimeo,
		SlavePriority: 100, SlaveMasterLinkStatus: MasterLinkDown,
	}
}

// Sentinel 是本 sentinel 自己的状态，CurrentEpoch 全局单调。
type Sentinel struct {
	MyID         string
	CurrentEpoch int64
}

// CheckObjectivelyDown 对应 src/sentinel.c:4654：quorum 从 1 起算（含自己）。
func (s *Sentinel) CheckObjectivelyDown(m *Instance, now int64, events *[]string) bool {
	quorum := 0
	odown := false
	if m.Flags&SRI_SDOWN != 0 {
		quorum = 1
		for _, ri := range m.Sentinels {
			if ri.Flags&SRI_MASTERDOWN != 0 {
				quorum++
			}
		}
		if quorum >= m.Quorum {
			odown = true
		}
	}
	if odown {
		if m.Flags&SRI_ODOWN == 0 {
			m.Flags |= SRI_ODOWN
			m.ODownSinceTime = now
			if events != nil {
				*events = append(*events, "+odown")
			}
		}
	} else if m.Flags&SRI_ODOWN != 0 {
		m.Flags &= ^SRI_ODOWN
		if events != nil {
			*events = append(*events, "-odown")
		}
	}
	return m.Flags&SRI_ODOWN != 0
}

// VoteLeader 对应 src/sentinel.c:4792：一个纪元只投一票，LeaderEpoch 记 CurrentEpoch。
func (s *Sentinel) VoteLeader(m *Instance, reqEpoch int64, reqRunID string, now int64, rnd func() int) (string, int64) {
	if reqEpoch > s.CurrentEpoch {
		s.CurrentEpoch = reqEpoch
	}
	if m.LeaderEpoch < reqEpoch && s.CurrentEpoch <= reqEpoch {
		m.Leader = reqRunID
		m.LeaderEpoch = s.CurrentEpoch
		if m.Leader != s.MyID {
			m.FailoverStartTime = now + int64(rnd()%SentinelMaxDesync)
		}
	}
	return m.Leader, m.LeaderEpoch
}

// GetLeader 对应 src/sentinel.c:4848：胜出要同时过「绝对多数」与「至少 quorum」两道门槛。
func (s *Sentinel) GetLeader(m *Instance, epoch int64, now int64, rnd func() int) string {
	counters := map[string]int{}
	voters := len(m.Sentinels) + 1
	for _, ri := range m.Sentinels {
		if ri.Leader != "" && ri.LeaderEpoch == s.CurrentEpoch {
			counters[ri.Leader]++
		}
	}
	winner := ""
	maxVotes := 0
	for cand, v := range counters {
		if v > maxVotes {
			maxVotes = v
			winner = cand
		}
	}
	target := s.MyID
	if winner != "" {
		target = winner
	}
	myvote, leaderEpoch := s.VoteLeader(m, epoch, target, now, rnd)
	if myvote != "" && leaderEpoch == epoch {
		counters[myvote]++
		if counters[myvote] > maxVotes {
			maxVotes = counters[myvote]
			winner = myvote
		}
	}
	votersQuorum := voters/2 + 1
	if winner != "" && (maxVotes < votersQuorum || maxVotes < m.Quorum) {
		return ""
	}
	return winner
}

// StartFailover 对应 src/sentinel.c:4991。
func (s *Sentinel) StartFailover(m *Instance, now int64, rnd func() int) {
	m.FailoverState = StateWaitStart
	m.Flags |= SRI_FAILOVERINPROGRESS
	s.CurrentEpoch++
	m.FailoverEpoch = s.CurrentEpoch
	m.FailoverStartTime = now + int64(rnd()%SentinelMaxDesync)
	m.FailoverStateChangeTime = now
}

// StartFailoverIfNeeded 对应 src/sentinel.c:5015 的三道门。
func (s *Sentinel) StartFailoverIfNeeded(m *Instance, now int64, rnd func() int) bool {
	if m.Flags&SRI_ODOWN == 0 {
		return false
	}
	if m.Flags&SRI_FAILOVERINPROGRESS != 0 {
		return false
	}
	if now-m.FailoverStartTime < m.FailoverTimeout*2 {
		return false
	}
	s.StartFailover(m, now, rnd)
	return true
}

// CompareSlavesForPromotion 对应 src/sentinel.c:5079：priority 升序 → offset 降序 → runid 升序，
// runid 为空视为最大。
func CompareSlavesForPromotion(a, b *Instance) int {
	if a.SlavePriority != b.SlavePriority {
		return a.SlavePriority - b.SlavePriority
	}
	if a.SlaveReplOffset > b.SlaveReplOffset {
		return -1
	}
	if a.SlaveReplOffset < b.SlaveReplOffset {
		return 1
	}
	if a.RunID == "" && b.RunID == "" {
		return 0
	}
	if a.RunID == "" {
		return 1
	}
	if b.RunID == "" {
		return -1
	}
	if a.RunID < b.RunID {
		return -1
	}
	if a.RunID > b.RunID {
		return 1
	}
	return 0
}

// SelectSlave 对应 src/sentinel.c:5105 的六道过滤 + 排序取首。
func SelectSlave(m *Instance, now int64) *Instance {
	maxMasterDownTime := int64(0)
	if m.Flags&SRI_SDOWN != 0 {
		maxMasterDownTime += now - m.SDownSinceTime
	}
	maxMasterDownTime += m.DownAfterPeriod * 10

	candidates := make([]*Instance, 0, len(m.Slaves))
	for _, s := range m.Slaves {
		if s.Flags&(SRI_SDOWN|SRI_ODOWN) != 0 {
			continue
		}
		if s.LinkDisconnected {
			continue
		}
		if now-s.LastAvailTime > SentinelPingPeriod*5 {
			continue
		}
		if s.SlavePriority == 0 {
			continue
		}
		infoValidity := int64(SentinelInfoPeriod * 3)
		if m.Flags&SRI_SDOWN != 0 {
			infoValidity = SentinelPingPeriod * 5
		}
		if now-s.InfoRefresh > infoValidity {
			continue
		}
		if s.MasterLinkDownTime > maxMasterDownTime {
			continue
		}
		candidates = append(candidates, s)
	}
	if len(candidates) == 0 {
		return nil
	}
	best := candidates[0]
	for _, c := range candidates[1:] {
		if CompareSlavesForPromotion(c, best) < 0 {
			best = c
		}
	}
	return best
}

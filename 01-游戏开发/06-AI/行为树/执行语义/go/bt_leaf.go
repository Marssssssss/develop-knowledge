package main

// ---------------------------------------------------------------- Leaf

type leaf struct {
	name      string
	script    []NodeStatus
	remaining int
	ticks     int
	halts     int
	status    NodeStatus
}

func newLeaf(name string, script []NodeStatus) *leaf {
	return &leaf{name: name, script: script, remaining: 0, status: Idle}
}

func (l *leaf) Name() string       { return l.name }
func (l *leaf) Status() NodeStatus { return l.status }
func (l *leaf) Halt()              { l.halts++ }
func (l *leaf) ResetStatus()       { l.status = Idle }

func (l *leaf) ExecuteTick() (NodeStatus, error) {
	l.ticks++
	if l.remaining >= len(l.script) {
		return Idle, errIdleChild // 剧本耗尽：模拟子进程返回 IDLE
	}
	s := l.script[l.remaining]
	if l.remaining < len(l.script)-1 {
		l.remaining++ // 只有多个值的剧本才推进，最后一个值恒定
	}
	l.status = s
	return s, nil
}

type skippedLeaf struct {
	name   string
	status NodeStatus
}

func (s *skippedLeaf) Name() string                          { return s.name }
func (s *skippedLeaf) Status() NodeStatus                    { return s.status }
func (s *skippedLeaf) Halt()                                 {}
func (s *skippedLeaf) ResetStatus()                          { s.status = Idle }
func (s *skippedLeaf) ExecuteTick() (NodeStatus, error)      { s.status = Skipped; return Skipped, nil }

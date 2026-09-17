// lsm_levels.go — 分层目标 / 得分 / 挑层(与 lsm_compact.go 同属 package main)
//
// 拆文件只为满足单文件 ≤300 行的约束;Go 同包共享符号,零语义改动。
// 算法出处见 lsm_compact.go / lsm_engine.go 文件头。
package main

// StaticLevelTarget LevelDB 口径:level-L 的上限是 10^L MB。
func (e *LSMEngine) StaticLevelTarget(level int) int {
	if level <= 0 {
		return e.Cfg.MaxBytesForLevelBase
	}
	v := e.Cfg.MaxBytesForLevelBase
	for i := 1; i < level; i++ {
		v *= e.Cfg.MaxBytesForLevelMultiplier
	}
	return v
}

// DynamicTargetsFrom 纯函数版动态层级目标:给定各层大小直接算,便于断言。
func DynamicTargetsFrom(sizes map[int]int, numLevels, base, multiplier int) map[int]int {
	last := numLevels - 1
	targets := map[int]int{last: sizes[last]}
	mult := float64(multiplier)
	v := float64(targets[last])
	for l := last - 1; l >= 1; l-- {
		v = v / mult
		targets[l] = int(v)
	}
	floor := float64(base) / mult
	for l := range targets {
		if float64(targets[l]) < floor {
			targets[l] = 0 // 官方:目标低于 base/multiplier 的层保持空
		}
	}
	return targets
}

// DynamicLevelTargets RocksDB 动态层级:末层目标 = 末层实际大小,逐层除以 multiplier。
func (e *LSMEngine) DynamicLevelTargets() map[int]int {
	sizes := map[int]int{}
	for l := 0; l < e.Cfg.NumLevels; l++ {
		sizes[l] = e.LevelSizeBytes(l)
	}
	return DynamicTargetsFrom(sizes, e.Cfg.NumLevels,
		e.Cfg.MaxBytesForLevelBase, e.Cfg.MaxBytesForLevelMultiplier)
}

// LevelTarget 某一层的目标大小。
func (e *LSMEngine) LevelTarget(level int) int {
	if level <= 0 {
		return e.Cfg.MaxBytesForLevelBase
	}
	if e.Cfg.DynamicLevelBytes {
		if v, ok := e.DynamicLevelTargets()[level]; ok {
			return v
		}
		return 0
	}
	return e.StaticLevelTarget(level)
}

// TotalDowncompactBytes 官方:估算从 L0..L(n-1) 压下来的总字节数(compaction 债务)。
func (e *LSMEngine) TotalDowncompactBytes(level int) int {
	t := 0
	for l := 0; l < level; l++ {
		t += e.LevelSizeBytes(l)
	}
	return t
}

// CompactionScores 得分越高越该压。L0 未达触发文件数时官方不触发,得分记 0。
func (e *LSMEngine) CompactionScores() map[int]float64 {
	scores := map[int]float64{}
	l0 := len(e.Levels[0])
	if l0 < e.Cfg.Level0FileNumCompactionTrigger {
		scores[0] = 0
	} else {
		byCount := float64(l0) / float64(e.Cfg.Level0FileNumCompactionTrigger)
		bySize := float64(e.LevelSizeBytes(0)) / float64(e.Cfg.MaxBytesForLevelBase)
		if bySize > byCount {
			scores[0] = bySize
		} else {
			scores[0] = byCount
		}
	}
	for l := 1; l < e.Cfg.NumLevels; l++ {
		tgt := e.LevelTarget(l)
		if tgt <= 0 {
			continue
		}
		denom := float64(tgt)
		if e.Cfg.DynamicLevelBytes {
			denom += float64(e.TotalDowncompactBytes(l))
		}
		scores[l] = float64(e.LevelSizeBytes(l)) / denom
	}
	return scores
}

// PickLevel 取得分 >= 1 且最高的那一层;同分取层号小的。
func (e *LSMEngine) PickLevel() (int, bool) {
	scores := e.CompactionScores()
	best := -1
	bestScore := 0.0
	for l := 0; l < e.Cfg.NumLevels; l++ {
		s, ok := scores[l]
		if !ok || s < 1.0 {
			continue
		}
		if best < 0 || s > bestScore {
			best, bestScore = l, s
		}
	}
	return best, best >= 0
}

// MaybeCompact 若有层需要压缩就压一层,返回是否做了工作。
func (e *LSMEngine) MaybeCompact() bool {
	level, ok := e.PickLevel()
	if !ok || level == e.Cfg.NumLevels-1 {
		return false // 末层无处可去
	}
	e.CompactLevel(level)
	return true
}


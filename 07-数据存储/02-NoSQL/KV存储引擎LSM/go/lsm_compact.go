// lsm_compact.go — flush / 分层目标 / compaction 挑层与执行 / 放大系数统计
//
// 权威来源:
//   google/leveldb  doc/impl.md
//     * 生成的 sorted table 进 level-0;young 文件超阈值后与 L1 重叠文件合并
//     * level-L 超过 10^L MB 时与 L+1 重叠文件合并;每个新 L1 文件 2MB
//     * 压缩丢弃被覆盖的值;删除标记只在更深层无文件覆盖该 key 时丢弃
//   facebook/rocksdb  wiki/Leveled-Compaction.md
//     * 非 0 层得分 = 层大小 / 目标大小;L0 得分 = max(文件数/触发数, 大小/base)
//     * L0 未达 level0_file_num_compaction_trigger 时不触发,不论得分多高
//     * 动态层级:末层目标 = 末层实际大小;逐层除以 multiplier;
//       目标低于 base/multiplier 的层保持空 → 保证约 90% 数据在末层
//     * 开动态层级时得分 = 层大小 / (层目标 + total_downcompact_bytes)
package main

import "sort"

// CompactEntries 合并多路版本流:每个 key 只保留最新版本;删除标记择机丢弃。
func CompactEntries(entries []Entry, canDropDelete func(string) bool) []Entry {
	ordered := NewestFirst(entries)
	out := []Entry{}
	prevKey := ""
	hasPrev := false
	for _, e := range ordered {
		if hasPrev && e.Key == prevKey {
			continue // 被更新的版本覆盖
		}
		prevKey, hasPrev = e.Key, true
		if e.IsDelete() && canDropDelete(e.Key) {
			continue // 更深层不再有更旧的值,标记失去意义
		}
		out = append(out, e)
	}
	return out
}

// FlushOne 把最老的不可变 memtable 落成 L0 文件;flush 时做一次行内 GC。
func (e *LSMEngine) FlushOne() *SSTable {
	if len(e.Immutables) == 0 {
		return nil
	}
	m := e.Immutables[0]
	e.Immutables = e.Immutables[1:]
	merged := CompactEntries(m.Drain(), func(string) bool { return false })
	if len(merged) == 0 {
		return nil
	}
	sst := NewSSTable(e.NextFileNum, 0, merged)
	e.NextFileNum++
	e.Levels[0] = append(e.Levels[0], sst)
	e.DiskBytesWritten += sst.SizeBytes()
	if len(e.WalBatches) > 1 {
		e.WalBatches = e.WalBatches[1:] // 该 memtable 对应的 WAL 已无用了
	}
	return sst
}

// BackgroundWork 一轮后台工作:先尽量 flush,再挑得分最高的层做一次 compaction。
func (e *LSMEngine) BackgroundWork() {
	for len(e.Immutables) > 0 {
		e.FlushOne()
	}
	for i := 0; i < 8; i++ {
		if !e.MaybeCompact() {
			break
		}
	}
}

// CanDropDelete 删除标记只有在更深层没有文件覆盖该 key 时才能丢(LevelDB 原文)。
func (e *LSMEngine) CanDropDelete(key string, outputLevel int) bool {
	for l := outputLevel + 1; l < e.Cfg.NumLevels; l++ {
		for _, t := range e.Levels[l] {
			if t.Overlaps(key, key) {
				return false
			}
		}
	}
	return true
}

// SplitOutput 输出文件按 target_file_size_base 切分(LevelDB:每个 L1 文件 2MB)。
func (e *LSMEngine) SplitOutput(entries []Entry) [][]Entry {
	out := [][]Entry{}
	cur := []Entry{}
	size := 0
	for _, x := range entries {
		cur = append(cur, x)
		size += x.SizeBytes()
		if size >= e.Cfg.TargetFileSizeBase {
			out = append(out, cur)
			cur, size = []Entry{}, 0
		}
	}
	if len(cur) > 0 {
		out = append(out, cur)
	}
	return out
}

// CompactLevel 把 level 的一个(或 L0 的全部)文件与 level+1 的重叠文件合并推到 level+1。
func (e *LSMEngine) CompactLevel(level int) {
	if len(e.Levels[level]) == 0 {
		return
	}
	picked := []*SSTable{}
	if level == 0 {
		picked = append(picked, e.Levels[0]...) // L0 文件互相重叠,通常全取
	} else {
		picked = append(picked, e.Levels[level][0])
	}
	lo, hi := picked[0].Smallest(), picked[0].Largest()
	for _, t := range picked {
		if t.Smallest() < lo {
			lo = t.Smallest()
		}
		if t.Largest() > hi {
			hi = t.Largest()
		}
	}
	nxt := []*SSTable{}
	for _, t := range e.Levels[level+1] {
		if t.Overlaps(lo, hi) {
			nxt = append(nxt, t)
		}
	}
	inputs := append(append([]*SSTable{}, picked...), nxt...)
	drop := map[*SSTable]bool{}
	for _, t := range inputs {
		drop[t] = true
	}
	keepCur := []*SSTable{}
	for _, t := range e.Levels[level] {
		if !drop[t] {
			keepCur = append(keepCur, t)
		}
	}
	e.Levels[level] = keepCur
	keepNext := []*SSTable{}
	for _, t := range e.Levels[level+1] {
		if !drop[t] {
			keepNext = append(keepNext, t)
		}
	}

	ents := []Entry{}
	for _, t := range inputs {
		ents = append(ents, t.Entries...)
	}
	outLevel := level + 1
	merged := CompactEntries(ents, func(k string) bool { return e.CanDropDelete(k, outLevel) })
	for _, chunk := range e.SplitOutput(merged) {
		sst := NewSSTable(e.NextFileNum, outLevel, chunk)
		e.NextFileNum++
		keepNext = append(keepNext, sst)
		e.DiskBytesWritten += sst.SizeBytes()
	}
	sort.SliceStable(keepNext, func(i, j int) bool {
		return keepNext[i].Smallest() < keepNext[j].Smallest()
	})
	e.Levels[outLevel] = keepNext

	released := 0
	for _, t := range inputs {
		released += t.SizeBytes()
	}
	e.PendingCompactionBytes -= released
	if e.PendingCompactionBytes < 0 {
		e.PendingCompactionBytes = 0
	}
}

// WriteAmplification 落盘字节 / 用户字节。
func (e *LSMEngine) WriteAmplification() float64 {
	if e.UserBytesWritten == 0 {
		return 0
	}
	return float64(e.DiskBytesWritten) / float64(e.UserBytesWritten)
}

// SpaceAmplification 磁盘字节 / 存活 key 数。
func (e *LSMEngine) SpaceAmplification() float64 {
	logical := map[string]Entry{}
	absorb := func(ents []Entry) {
		for _, x := range ents {
			cur, ok := logical[x.Key]
			if !ok || x.Seq > cur.Seq {
				logical[x.Key] = x
			}
		}
	}
	absorb(e.Mem.Entries)
	for _, m := range e.Immutables {
		absorb(m.Entries)
	}
	for l := 0; l < e.Cfg.NumLevels; l++ {
		for _, t := range e.Levels[l] {
			absorb(t.Entries)
		}
	}
	live := 0
	for _, x := range logical {
		if !x.IsDelete() {
			live++
		}
	}
	if live == 0 {
		return 0
	}
	return float64(e.TotalSizeBytes()) / float64(live)
}

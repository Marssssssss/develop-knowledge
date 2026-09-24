package main

// 链起点解码与链遍历:复刻 ChainedFixups.cpp 的两个 forEach 与 imports 解析。

// SegmentStarts 对应 dyld_chained_starts_in_segment。
type SegmentStarts struct {
	PageSize      uint64
	PointerFormat uint16
	PageStart     []uint16
	PageCount     int
}

// Segment 段的原始内容:页内偏移 -> 条目值。
type Segment struct {
	Index    int
	Content  map[uint64]uint64
	PageSize uint64
}

// ChainStartsOnPage 复刻 page_start 的三态解码。
// 顺序很关键:0xFFFF 同时满足 NONE 与 MULTI,先按 NONE 拦截。
func ChainStartsOnPage(st SegmentStarts, page int) []uint16 {
	ps := st.PageStart[page]
	if ps == startNone {
		return nil
	}
	if ps&startMulti != 0 {
		idx := ps & ^uint16(startMulti)
		out := []uint16{}
		for {
			entry := st.PageStart[idx]
			last := entry&startLast != 0
			out = append(out, entry&^uint16(startLast))
			idx++
			if last {
				return out
			}
		}
	}
	return []uint16{ps}
}

// ForEachFixupInChain 复刻 forEachFixupLocationInChain:先算 next 再回调,
// next 严格大于页末地址才断链(恰好等于不算出页)。
func ForEachFixupInChain(f Format, seg Segment, chainStart, page int, pref uint64) ([]uint64, []Fixup, error) {
	startPage := uint64(page) * seg.PageSize
	endPage := startPage + seg.PageSize
	if uint64(chainStart) < startPage || uint64(chainStart) > endPage {
		return nil, nil, nil // 源码:chain is not on page
	}
	var locs []uint64
	var fxs []Fixup
	loc := uint64(chainStart)
	for {
		raw, ok := seg.Content[loc]
		if !ok {
			return nil, nil, errNoContent
		}
		next, has := f.NextLocation(loc, raw)
		locs = append(locs, loc)
		fxs = append(fxs, f.Parse(raw, pref))
		if !has {
			return locs, fxs, nil
		}
		if next > endPage {
			return locs, fxs, nil // 源码:chain went off end of page
		}
		loc = next
	}
}

// ImportEntry 一个 imports 表项(imports_format == IMPORT_ADDEND)。
type ImportEntry struct {
	LibOrdinal int64
	WeakImport uint64
	NameOffset uint64
	Addend     int64
}

// ParseImportAddend 解析 DYLD_CHAINED_IMPORT_ADDEND:两个 uint32,后一个是 addend。
func ParseImportAddend(w0, w1 uint32) ImportEntry {
	return ImportEntry{
		LibOrdinal: signExtend(uint64(bits(uint64(w0), 0, 7)), 8),
		WeakImport: bits(uint64(w0), 8, 8),
		NameOffset: bits(uint64(w0), 9, 31),
		Addend:     signExtend(uint64(w1), 32),
	}
}

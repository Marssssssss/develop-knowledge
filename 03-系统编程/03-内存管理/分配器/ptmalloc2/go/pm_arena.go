package main

// arena:主分配区(教学简化版),保留 glibc 的 bins/tcache 与查找顺序。

type arena struct {
	chunks        []*chunk
	topSize       int
	tcache        map[int][]*chunk
	tcacheCount   int
	fastbins      map[int][]*chunk
	unsorted      []*chunk
	smallbins     map[int][]*chunk
	largebins     map[int][]*chunk
	mmapThreshold int
	trimThreshold int
	mmapMax       int
	arenaMax      int
	path          []string
	munmaps       int
	trims         int
}

func newArena(tcacheCount, cpu int) *arena {
	return &arena{
		topSize: topChunkSize, tcache: map[int][]*chunk{}, tcacheCount: tcacheCount,
		fastbins: map[int][]*chunk{}, smallbins: map[int][]*chunk{},
		largebins: map[int][]*chunk{}, mmapThreshold: defaultMmapThreshold,
		trimThreshold: defaultTrimThresh, mmapMax: defaultMmapMax,
		arenaMax: maxArenasFactor * cpu,
	}
}

func (a *arena) step(name string) { a.path = append(a.path, name) }
func (a *arena) resetPath()       { a.path = nil }

func (a *arena) malloc(req int) *chunk {
	c := a.allocate(req)
	c.inuse = true
	return c
}

func (a *arena) allocate(req int) *chunk {
	a.resetPath()
	nb := request2size(req)

	a.step("check-mmap-threshold")
	if nb >= a.mmapThreshold {
		a.step("mmap-direct")
		a.munmaps++
		return &chunk{size: nb, mmapped: true}
	}

	if slots := a.tcache[csize2tidx(nb)]; len(slots) > 0 {
		a.step("tcache-hit")
		c := slots[len(slots)-1]
		a.tcache[csize2tidx(nb)] = slots[:len(slots)-1]
		return c
	}
	a.step("tcache-miss-exact-only")

	if nb < maxFastSize && len(a.fastbins[nb]) > 0 {
		a.step("fastbin-hit")
		a.step("prefill-tcache-from-fastbin")
		lst := a.fastbins[nb]
		c := lst[len(lst)-1]
		a.fastbins[nb] = lst[:len(lst)-1]
		return c
	}

	if inSmallbinRange(nb) {
		if lst := a.smallbins[smallbinIndex(nb)]; len(lst) > 0 {
			a.step("smallbin-hit")
			a.step("prefill-tcache-from-smallbin")
			a.smallbins[smallbinIndex(nb)] = lst[1:]
			return lst[0]
		}
	}

	if !inSmallbinRange(nb) {
		a.flushFastbinsToUnsorted()
		a.step("large:flush-fastbins-to-unsorted")
	}

	if hit := a.scanUnsorted(nb); hit != nil {
		a.step("unsorted-scan-hit")
		return hit
	}

	if !inSmallbinRange(nb) {
		if hit := a.searchLargeBins(nb); hit != nil {
			a.step("largebin-hit")
			return hit
		}
	}

	if len(a.fastbins) > 0 {
		a.flushFastbinsToUnsorted()
		a.step("small:consolidate-fastbins-and-retry")
		if hit := a.scanUnsorted(nb); hit != nil {
			return hit
		}
	}

	a.step("split-top")
	return a.carveTop(nb)
}

func (a *arena) flushFastbinsToUnsorted() {
	for _, lst := range a.fastbins {
		a.unsorted = append(a.unsorted, lst...)
	}
	a.fastbins = map[int][]*chunk{}
}

// scanUnsorted 手册:这是**唯一**把 chunk 放进 small/large bin 的地方。
func (a *arena) scanUnsorted(nb int) *chunk {
	var hit *chunk
	keep := make([]*chunk, 0, len(a.unsorted))
	for _, c := range a.unsorted {
		if hit == nil && c.size >= nb {
			hit = c
			continue
		}
		keep = append(keep, c)
	}
	a.unsorted = keep
	for _, c := range keep {
		if inSmallbinRange(c.size) {
			i := smallbinIndex(c.size)
			a.smallbins[i] = append(a.smallbins[i], c)
		} else {
			i := largebinIndex64(c.size)
			a.largebins[i] = append(a.largebins[i], c)
		}
	}
	if hit != nil && hit.size > nb+minSize {
		a.split(hit, nb)
	}
	return hit
}

func (a *arena) searchLargeBins(nb int) *chunk {
	for idx := largebinIndex64(nb); idx < nBins; idx++ {
		lst := a.largebins[idx]
		if len(lst) == 0 {
			continue
		}
		best := -1
		for i, c := range lst {
			if c.size >= nb && (best < 0 || c.size < lst[best].size) {
				best = i
			}
		}
		if best >= 0 {
			c := lst[best]
			a.largebins[idx] = append(append([]*chunk{}, lst[:best]...), lst[best+1:]...)
			if c.size > nb+minSize {
				a.split(c, nb)
			}
			return c
		}
	}
	return nil
}

func (a *arena) carveTop(nb int) *chunk {
	if a.topSize < nb {
		panic("top chunk exhausted(真实 glibc 会 sbrk/mmap 新 heap)")
	}
	a.topSize -= nb
	c := &chunk{off: len(a.chunks) * 4096, size: nb}
	a.chunks = append(a.chunks, c)
	return c
}

func (a *arena) split(c *chunk, nb int) {
	rest := &chunk{off: c.off + nb, size: c.size - nb}
	c.size = nb
	i := a.indexOf(c)
	a.chunks = append(a.chunks, nil)
	copy(a.chunks[i+2:], a.chunks[i+1:])
	a.chunks[i+1] = rest
}

func (a *arena) indexOf(c *chunk) int {
	for i, x := range a.chunks {
		if x == c {
			return i
		}
	}
	return -1
}

func (a *arena) free(c *chunk) string {
	a.resetPath()
	c.inuse = false
	if c.mmapped {
		a.step("munmap")
		a.munmaps++
		return "munmap"
	}

	tidx := csize2tidx(c.size)
	if len(a.tcache[tidx]) < a.tcacheCount {
		a.step("tcache-store")
		a.tcache[tidx] = append(a.tcache[tidx], c)
		return "tcache"
	}

	if c.size < maxFastSize {
		a.step("fastbin")
		a.fastbins[c.size] = append(a.fastbins[c.size], c)
		return "fastbin"
	}

	a.step("coalesce")
	merged := a.coalesce(c)
	if len(a.chunks) > 0 && a.chunks[len(a.chunks)-1] == merged {
		a.step("absorb-into-top")
		a.topSize += merged.size
		i := a.indexOf(merged)
		a.chunks = append(a.chunks[:i], a.chunks[i+1:]...)
		return "top"
	}

	a.step("unsorted")
	a.unsorted = append(a.unsorted, merged)
	if merged.size >= a.trimThreshold {
		a.step("trim")
		a.trims++
		return "unsorted+trim"
	}
	return "unsorted"
}

func (a *arena) coalesce(c *chunk) *chunk {
	for {
		i := a.indexOf(c)
		merged := false
		for _, j := range []int{i + 1, i - 1} {
			if j >= 0 && j < len(a.chunks) && !a.chunks[j].inuse {
				other := a.chunks[j]
				if other.off < c.off {
					c.off = other.off
				}
				c.size += other.size
				k := a.indexOf(other)
				a.chunks = append(a.chunks[:k], a.chunks[k+1:]...)
				merged = true
				break
			}
		}
		if !merged {
			return c
		}
	}
}

// heapBytes 应用当前实际持有(in-use)的字节数。
func (a *arena) heapBytes() int {
	n := 0
	for _, c := range a.chunks {
		if c.inuse {
			n += c.size
		}
	}
	return n
}

// inArenaBytes 从内核视角仍属于本进程的堆字节数:in-use + free + top。
func (a *arena) inArenaBytes() int {
	n := a.topSize
	for _, c := range a.chunks {
		n += c.size
	}
	return n
}

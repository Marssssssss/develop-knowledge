package main

import (
	"fmt"
	"math"
	"sort"
)

func demoClauses() []*Clause {
	type spec struct {
		name string
		n    int
		idf  float64
	}
	specs := []spec{{"a", 1200, 3.0}, {"b", 800, 2.0}, {"c", 500, 1.0}, {"d", 300, 0.5}}
	out := make([]*Clause, 0, len(specs))
	seed := uint32(11)
	next := func(m int) int { // xorshift，确定性随机
		seed ^= seed << 13
		seed ^= seed >> 17
		seed ^= seed << 5
		return int(seed) % m
	}
	for _, s := range specs {
		docs := make([]int, s.n)
		for i := range docs {
			docs[i] = next(4000)
		}
		sort.Ints(docs)
		freqs := make([]float64, s.n)
		norms := make([]float64, s.n)
		for i := range docs {
			freqs[i] = float64(1 + next(5))
			norms[i] = 0.5 + float64(next(200))/100.0
		}
		out = append(out, newClause(s.name, docs, freqs, norms, s.idf, 128))
	}
	return out
}

type hit struct {
	score float64
	doc   int
}

type topK struct {
	k      int
	hits   []hit
	minCom float64
}

func (t *topK) collect(doc int, score float64) {
	if len(t.hits) >= t.k && score <= t.minCom {
		return
	}
	t.hits = append(t.hits, hit{score, doc})
	sort.Slice(t.hits, func(i, j int) bool { return t.hits[i].score < t.hits[j].score })
	if len(t.hits) > t.k {
		t.hits = t.hits[len(t.hits)-t.k:]
	}
	if len(t.hits) >= t.k {
		t.minCom = t.hits[0].score
	}
}

func docList(t *topK) []int {
	out := make([]int, 0, len(t.hits))
	for i := len(t.hits) - 1; i >= 0; i-- {
		out = append(out, t.hits[i].doc)
	}
	return out
}

func naiveOr(cs []*Clause, k, maxDoc int) (*topK, int) {
	t := &topK{k: k}
	pos := make([]map[int]float64, len(cs))
	for i, c := range cs {
		pos[i] = map[int]float64{}
		for j, d := range c.docs {
			pos[i][d] = c.scores[j]
		}
	}
	seen := map[int]bool{}
	scored := 0
	for _, c := range cs {
		for _, d := range c.docs {
			if d >= maxDoc || seen[d] {
				continue
			}
			seen[d] = true
			s := 0.0
			for i := range cs {
				s += pos[i][d]
			}
			scored++
			t.collect(d, s)
		}
	}
	return t, scored
}

func blockMaxWand(cs []*Clause, k, maxDoc int) (*topK, int, int) {
	t := &topK{k: k}
	pos := make([]map[int]float64, len(cs))
	for i, c := range cs {
		pos[i] = map[int]float64{}
		for j, d := range c.docs {
			pos[i][d] = c.scores[j]
		}
	}
	costs := make([]float64, len(cs))
	for i, c := range cs {
		costs[i] = float64(c.cost)
	}
	order := make([]int, len(cs))
	for i := range order {
		order[i] = i
	}
	full, cand := 0, 0
	windowMin := 0
	for windowMin < maxDoc {
		outerMax := math.Inf(1)
		for _, i := range order {
			up := cs[i].advanceShallow(windowMin)
			if up+1 < outerMax {
				outerMax = up + 1
			}
		}
		if outerMax > float64(maxDoc) {
			outerMax = float64(maxDoc)
		}
		mws := make([]float64, len(cs))
		for i, c := range cs {
			d := c.nextDocAtLeast(windowMin)
			if float64(d) < outerMax {
				c.advanceShallow(windowMin)
				mws[i] = c.getMaxScore(outerMax - 1)
			} else {
				mws[i] = 0
			}
		}
		p := partitionScorers(mws, costs, order, t.minCom)
		if p == nil {
			windowMin = int(outerMax)
			continue
		}
		order = p.order
		candSet := map[int]bool{}
		for _, i := range order[p.firstEssential:] {
			for _, d := range cs[i].docs {
				if float64(d) >= float64(windowMin) && float64(d) < outerMax {
					candSet[d] = true
				}
			}
		}
		docs := make([]int, 0, len(candSet))
		for d := range candSet {
			docs = append(docs, d)
		}
		sort.Ints(docs)
		scores := make([]float64, len(docs))
		for j, d := range docs {
			for _, i := range order[p.firstEssential:] {
				scores[j] += pos[i][d]
			}
		}
		cand += len(docs)
		for j := p.firstEssential - 1; j >= 0; j-- {
			docs, scores = filterCompetitiveHits(docs, scores, p.maxScoreSums[j], t.minCom, len(cs))
			if j >= p.firstRequired {
				docs, scores = applyRequiredClause(docs, scores, pos[order[j]])
			} else {
				scores = applyOptionalClause(docs, scores, pos[order[j]])
			}
		}
		full += len(docs)
		for j, d := range docs {
			t.collect(d, scores[j])
		}
		windowMin = int(outerMax)
	}
	return t, full, cand
}

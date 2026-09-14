// Cost-Based Optimizer — Go version: statistics + DP join enumeration.
//
// Mirrors cbo.py.
//
// Refs:
//   - PostgreSQL planner-stats.html + geqo.html
//   - Selinger et al. 1979 (System R DP)

package main

import (
	"fmt"
	"math/rand"
	"time"
)

const (
	SeqPageCost    = 1.0
	RandomPageCost = 4.0
	CPUTupleCost   = 0.01
	CPUIndexCost   = 0.005
	CPUOpCost      = 0.0025
	GEQOThreshold  = 8
)

type TableStat struct {
	Name     string
	NRows    int
	NPages   int
	HasIdx   bool
	MCV      map[string]float64 // for = selectivity
	NDV      int
	NullFrac float64
}

type Optimizer struct {
	Tables map[string]*TableStat
	Order  []string
}

// Selectivity for col=value using MCV table; falls back to 1/NDV.
func (t *TableStat) SelectivityEq(val string) float64 {
	if f, ok := t.MCV[val]; ok {
		return f
	}
	return 1.0 / float64(max(1, t.NDV))
}

func max(a, b int) int {
	if a > b {
		return a
	}
	return b
}

func (o *Optimizer) SeqCost(t *TableStat) float64 {
	return SeqPageCost*float64(t.NPages) + CPUTupleCost*float64(t.NRows)
}

func (o *Optimizer) IdxCost(t *TableStat, sel float64) float64 {
	if !t.HasIdx {
		return 1e18
	}
	idxPages := 0.1 * float64(t.NRows)
	nFetched := float64(t.NRows) * sel
	return RandomPageCost*idxPages + RandomPageCost*nFetched +
		CPUIndexCost*float64(t.NRows) + CPUTupleCost*nFetched
}

// Best single-relation access path.
func (o *Optimizer) BestAccess(name string, sel float64) float64 {
	t := o.Tables[name]
	seq := o.SeqCost(t)
	idx := o.IdxCost(t, sel)
	if idx < seq {
		return idx
	}
	return seq
}

// DP join enumeration over bitmask sets.
func (o *Optimizer) DPJoin(names []string) float64 {
	n := len(names)
	// memo[mask] = best cost
	memo := make(map[int]float64)
	for i, nm := range names {
		mask := 1 << i
		memo[mask] = o.BestAccess(nm, 0.1)
	}
	for mask := 1; mask < (1 << n); mask++ {
		if memo[mask] > 0 {
			continue
		}
		best := 1e18
		for last := 0; last < n; last++ {
			if mask&(1<<last) == 0 {
				continue
			}
			rest := mask &^ (1 << last)
			c := memo[rest] + o.BestAccess(names[last], 0.1)
			if c < best {
				best = c
			}
		}
		memo[mask] = best
	}
	return memo[(1 << n) - 1]
}

// GEQO-style random permutation search.
func (o *Optimizer) GEQO(names []string, gens, poolSize int) []string {
	n := len(names)
	rand.Seed(time.Now().UnixNano())
	pop := make([][]int, poolSize)
	fit := make([]float64, poolSize)
	for i := 0; i < poolSize; i++ {
		pop[i] = rand.Perm(n)
		fit[i] = fitness(o, pop[i], names)
	}
	for g := 0; g < gens; g++ {
		for i := 0; i < poolSize; i++ {
			for j := i + 1; j < poolSize; j++ {
				if fit[j] > fit[i] {
					fit[i], fit[j] = fit[j], fit[i]
					pop[i], pop[j] = pop[j], pop[i]
				}
			}
		}
		for i := poolSize / 2; i < poolSize; i++ {
			a := pop[rand.Intn(poolSize/2)]
			b := pop[rand.Intn(poolSize/2)]
			cut := 1 + rand.Intn(n-1)
			seen := map[int]bool{}
			child := make([]int, 0, n)
			for k := 0; k < cut; k++ {
				child = append(child, a[k])
				seen[a[k]] = true
			}
			for k := 0; k < n; k++ {
				if !seen[b[k]] {
					child = append(child, b[k])
					seen[b[k]] = true
				}
			}
			pop[i] = child
			fit[i] = fitness(o, child, names)
		}
	}
	best := 0
	for i := 1; i < poolSize; i++ {
		if fit[i] > fit[best] {
			best = i
		}
	}
	out := make([]string, n)
	for i, idx := range pop[best] {
		out[i] = names[idx]
	}
	return out
}

func fitness(o *Optimizer, order []int, names []string) float64 {
	c := 0.0
	for _, idx := range order {
		c += o.BestAccess(names[idx], 0.1)
	}
	return 1.0 / (1.0 + c)
}

func main() {
	o := &Optimizer{Tables: map[string]*TableStat{}}
	o.Tables["users"] = &TableStat{
		Name: "users", NRows: 100000, NPages: 2500, HasIdx: true,
		MCV: map[string]float64{"US": 0.4, "CN": 0.2, "IN": 0.1}, NDV: 200,
	}
	o.Tables["orders"] = &TableStat{
		Name: "orders", NRows: 1000000, NPages: 25000, HasIdx: true,
		MCV: map[string]float64{}, NDV: 100000,
	}
	o.Tables["items"] = &TableStat{
		Name: "items", NRows: 5000000, NPages: 125000, HasIdx: true,
		MCV: map[string]float64{}, NDV: 1000000,
	}

	cost := o.DPJoin([]string{"users", "orders", "items"})
	fmt.Printf("DP best plan cost = %.2f\n", cost)

	// 10-table GEQO
	big := make([]string, 10)
	for i := 0; i < 10; i++ {
		nm := fmt.Sprintf("t%d", i)
		big[i] = nm
		o.Tables[nm] = &TableStat{
			Name: nm, NRows: 10000 * (i + 1), NPages: 250 * (i + 1),
			HasIdx: true, MCV: map[string]float64{}, NDV: 1000,
		}
	}
	order := o.GEQO(big, 60, 30)
	fmt.Printf("GEQO 10-table order: %v\n", order)
}
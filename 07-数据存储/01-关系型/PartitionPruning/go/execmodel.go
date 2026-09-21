package main

// PrunePlan 是裁剪三阶段模型: 计划期 / 执行期初始化 / 执行期逐次。
type PrunePlan struct {
	Parts    []Partition
	Names    []string
	Enable   bool
	clauses  [][2]interface{}
}

// NewPrunePlan 构造一个裁剪计划(默认开启裁剪)。
func NewPrunePlan(parts []Partition, enable bool) *PrunePlan {
	names := []string{}
	for _, p := range parts {
		names = append(names, p.Name())
	}
	return &PrunePlan{Parts: parts, Names: names, Enable: enable}
}

// WithClauses 设置当前生效的裁剪条件(AND 组合)。
func (pp *PrunePlan) WithClauses(cs [][2]interface{}) *PrunePlan {
	pp.clauses = cs
	return pp
}

func (pp *PrunePlan) survivors() []string {
	if !pp.Enable || len(pp.clauses) == 0 {
		return pp.Names
	}
	sets := [][]string{}
	for _, c := range pp.clauses {
		op, _ := c[0].(string)
		hits, _ := Prune(pp.Parts, op, c[1])
		sets = append(sets, hits)
	}
	return Intersect(sets)
}

// PlannerPrune 计划期裁剪: 被裁的分区不出现在计划里。
func (pp *PrunePlan) PlannerPrune() ([]string, int) {
	s := pp.survivors()
	return s, len(pp.Names) - len(s)
}

// InitialPrune 执行期初始化: 返回存活分区、Subplans Removed 数、仍加锁的分区。
func (pp *PrunePlan) InitialPrune() ([]string, int, []string) {
	s := pp.survivors()
	return s, len(pp.Names) - len(s), pp.Names // 文档: 被裁的分区仍会被加锁
}

// ExecPrune 执行期逐次裁剪: 返回每个分区的 loops 与 never executed 列表。
func (pp *PrunePlan) ExecPrune(outerValues []interface{}, op string) (map[string]int, []string) {
	loops := map[string]int{}
	for _, n := range pp.Names {
		loops[n] = 0
	}
	for _, v := range outerValues {
		if !pp.Enable {
			for _, n := range pp.Names {
				loops[n]++
			}
			continue
		}
		hits, _ := Prune(pp.Parts, op, v)
		for _, n := range hits {
			loops[n]++
		}
	}
	never := []string{}
	for _, n := range pp.Names {
		if loops[n] == 0 {
			never = append(never, n)
		}
	}
	return loops, never
}

// Render 生成与 EXPLAIN 同构的 Append 计划文本。
func (pp *PrunePlan) Render(survivors []string, removed int) string {
	out := "Append"
	for _, n := range survivors {
		out += "\n  -> Seq Scan on " + n
	}
	if removed > 0 {
		out += "\n  Subplans Removed: " + itoa(removed)
	}
	return out
}

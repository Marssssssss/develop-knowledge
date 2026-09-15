// 物化视图与刷新策略(全量 vs CONCURRENTLY 增量)最小模拟。
// 依据 postgresql.org/docs/current/rules-materializedviews.html:
// 物化视图持久化查询结果、不可直接 DML;REFRESH 全量替换;
// CONCURRENTLY 需唯一索引,diff 增量,读不阻塞。
package main

import "fmt"

type MatView struct {
	data         map[string]int // 物化结果
	uniqueIndex bool
	deleted      int            // 上次 diff 统计
	inserted     int
	updated      int
}

func summarize(rows [][2]interface{}) map[string]int {
	agg := map[string]int{}
	for _, r := range rows {
		k := r[0].(string)
		agg[k] += r[1].(int)
	}
	return agg
}

// Refresh 全量刷新:整体重算 + 一次性替换。
func (m *MatView) Refresh(rows [][2]interface{}) string {
	m.data = summarize(rows) // 替换期间等价于持独占锁
	return "full"
}

// RefreshConcurrently 增量刷新:要求唯一索引;算 diff 后原子切换。
func (m *MatView) RefreshConcurrently(rows [][2]interface{}) (string, error) {
	if !m.uniqueIndex {
		return "", fmt.Errorf("CONCURRENTLY requires a UNIQUE index")
	}
	tmp := summarize(rows)
	m.deleted, m.inserted, m.updated = 0, 0, 0
	for k := range m.data {
		if _, ok := tmp[k]; !ok {
			m.deleted++
		}
	}
	for k := range tmp {
		if _, ok := m.data[k]; !ok {
			m.inserted++
		} else if tmp[k] != m.data[k] {
			m.updated++
		}
	}
	m.data = tmp // 原子切换(真实 PG:临时表 + rename)
	return "concurrent", nil
}

func main() {
	rows := [][2]interface{}{{"a", 1}, {"a", 2}, {"b", 5}}
	mv := &MatView{}
	fmt.Println("full refresh:", mv.Refresh(rows))
	fmt.Println("data:", mv.data)

	_, err := mv.RefreshConcurrently(rows)
	fmt.Println("concurrent without unique index:", err)

	mv.uniqueIndex = true
	rows = append(rows, [2]interface{}{"c", 9})
	mode, _ := mv.RefreshConcurrently(rows)
	fmt.Printf("%s diff: -%d +%d ~%d\n", mode, mv.deleted, mv.inserted, mv.updated)
	fmt.Println("data:", mv.data)

	// 静态自检(人工审查替代编译,见 README)
	assert := func(b bool, msg string) {
		if !b {
			panic("assert failed: " + msg)
		}
	}
	assert(mv.data["a"] == 3 && mv.data["b"] == 5 && mv.data["c"] == 9, "aggregation")
	assert(mv.inserted == 1 && mv.updated == 0 && mv.deleted == 0, "diff counts")
	fmt.Println("go static checks passed")
}

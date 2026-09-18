// OFFSET 与键集分页（keyset / seek method）的 Go 版对照实现。
//
// 口径来源（实读）：
//   - PostgreSQL 18 手册 7.6 "LIMIT and OFFSET"（postgresql.org/docs/current/queries-limit.html）：
//     OFFSET 先跳过 N 行再数 LIMIT 行；"The rows skipped by an OFFSET clause still have to be
//     computed inside the server; therefore a large OFFSET might be inefficient."；
//     必须用 ORDER BY 限定唯一序，否则"you will get an unpredictable subset"；
//     "the query optimizer takes LIMIT into account when generating query plans" →
//     不同 LIMIT/OFFSET 取不同子集会给出不一致结果（除非 ORDER BY 唯一）；
//     "OFFSET 0 is the same as omitting the OFFSET clause"。
//   - use-the-index-luke.com/no-offset（Markus Winand，引用 SQL:2023 Part 2 §4.17.3）：
//     "the rows in the derived table are first sorted according to the <order by clause> and then
//     limited by dropping the number of rows specified in the <result offset clause> from the
//     beginning"；keyset 基本写法 `WHERE id < ?last_seen_id ORDER BY id DESC FETCH FIRST 10 ROWS ONLY`；
//     插入会导致 offset 分页出现重复；keyset 无法直接跳到任意页。
//
// 运行：go run keyset_pagination.go（本机无 Go 工具链，代码经人工审查 + 结构校验）
package main

import (
	"fmt"
	"os"
	"sort"
)

const pageSize = 10

// row 是索引里的一行：分数 + 唯一主键（主键充当并列时的 tie-breaker）。
type row struct {
	score int
	id    int
}

// table 持有按 (score DESC, id DESC) 排序的索引，并统计「数据库实际取出的行数」。
type table struct {
	rows        []row
	rowsFetched int
	sql         string
}

func newTable(n int) *table {
	rows := make([]row, 0, n)
	for i := 1; i <= n; i++ {
		rows = append(rows, row{score: (i * 7919) % 501, id: i})
	}
	sortRows(rows)
	return &table{rows: rows}
}

func sortRows(rows []row) {
	sort.SliceStable(rows, func(i, j int) bool {
		if rows[i].score != rows[j].score {
			return rows[i].score > rows[j].score
		}
		return rows[i].id > rows[j].id
	})
}

// offsetPage 完整复现 OFFSET 语义：先排序，再丢弃 page*size 行，最后取 size 行。
func (t *table) offsetPage(page int) []row {
	ordered := append([]row(nil), t.rows...)
	sortRows(ordered)
	skipped := page * pageSize
	t.rowsFetched += skipped + pageSize // 被丢弃的行仍然被服务器取出来过
	t.sql = fmt.Sprintf("SELECT ... ORDER BY score DESC, id DESC LIMIT %d OFFSET %d", pageSize, skipped)
	if skipped >= len(ordered) {
		return nil
	}
	end := skipped + pageSize
	if end > len(ordered) {
		end = len(ordered)
	}
	return ordered[skipped:end]
}

// keysetPage 用「上一页最后一个键」定位；cursor 为 nil 表示首页。
// 行值比较 (score, id) < (cursor.score, cursor.id) 用一次二分替代「丢弃 N 行」。
func (t *table) keysetPage(cursor *row) []row {
	ordered := append([]row(nil), t.rows...)
	sortRows(ordered)
	start := 0
	if cursor == nil {
		t.sql = fmt.Sprintf("SELECT ... ORDER BY score DESC, id DESC LIMIT %d", pageSize)
	} else {
		start = sort.Search(len(ordered), func(i int) bool { // 第一个「不大于游标」的位置
			return ordered[i].score < cursor.score ||
				(ordered[i].score == cursor.score && ordered[i].id < cursor.id)
		})
		t.rowsFetched += bits(start) // 定位代价 O(log N)
		t.sql = fmt.Sprintf("SELECT ... WHERE (score, id) < (%d, %d) ORDER BY score DESC, id DESC LIMIT %d",
			cursor.score, cursor.id, pageSize)
	}
	t.rowsFetched += pageSize
	end := start + pageSize
	if end > len(ordered) {
		end = len(ordered)
	}
	return ordered[start:end]
}

// keysetPageByScoreOnly 是「只按 score 做游标、漏掉 tie-breaker」的错误实现（对照用）。
func (t *table) keysetPageByScoreOnly(lastScore int) []row {
	ordered := append([]row(nil), t.rows...)
	sortRows(ordered)
	start := sort.Search(len(ordered), func(i int) bool { return ordered[i].score < lastScore })
	t.rowsFetched += pageSize
	end := start + pageSize
	if end > len(ordered) {
		end = len(ordered)
	}
	return ordered[start:end]
}

func bits(n int) int {
	c := 0
	for n > 0 {
		c++
		n >>= 1
	}
	return c
}

func keys(rows []row) []string {
	out := make([]string, 0, len(rows))
	for _, r := range rows {
		out = append(out, fmt.Sprintf("%d/%d", r.score, r.id))
	}
	return out
}

func same(a, b []row) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

var checks = struct{ pass, fail int }{}

func check(label string, cond bool, detail string) {
	if cond {
		checks.pass++
		fmt.Printf("  [PASS] %s\n", label)
		return
	}
	checks.fail++
	fmt.Printf("  [FAIL] %s :: %s\n", label, detail)
}

func main() {
	fmt.Println("1) OFFSET：丢弃前 N 行仍要先把它们取出来")
	t := newTable(10000)
	p := t.offsetPage(999)
	check("第 1000 页（每页 10）要取出 10000 行才返回 10 行", t.rowsFetched == 9990+pageSize, fmt.Sprint(t.rowsFetched))
	check("SQL 里 OFFSET = page × size", t.sql == "SELECT ... ORDER BY score DESC, id DESC LIMIT 10 OFFSET 9990", t.sql)
	check("确实返回了 10 行", len(p) == 10, fmt.Sprint(len(p)))

	fmt.Println("\n2) 键集分页：一次二分定位 + 取 10 行")
	kt := newTable(10000)
	first := kt.keysetPage(nil)
	second := kt.keysetPage(&first[len(first)-1])
	check("第 2 页只取 10 行 + 一次 O(log N) 定位",
		kt.rowsFetched == pageSize+pageSize+bits(10), fmt.Sprint(kt.rowsFetched))
	check("SQL 用行值比较 (score, id) < (?, ?)",
		len(kt.sql) >= 32 && kt.sql[17:32] == "(score, id) < (", kt.sql)
	check("键集第 2 页与 OFFSET 第 2 页结果一致", same(second, newTable(10000).offsetPage(1)),
		fmt.Sprint(keys(second)))

	fmt.Println("\n3) 深分页代价对比（同为一次「取第 1000 页」请求）")
	deepO := newTable(10000)
	deepO.offsetPage(999)
	deepK := newTable(10000)
	ordered := append([]row(nil), deepK.rows...)
	sortRows(ordered)
	cursor := ordered[9989] // 已知游标
	deepK.keysetPage(&cursor)
	ratio := float64(deepO.rowsFetched) / float64(deepK.rowsFetched)
	check("OFFSET 取行数是键集的 100 倍以上", ratio > 100,
		fmt.Sprintf("offset=%d keyset=%d ratio=%.1f", deepO.rowsFetched, deepK.rowsFetched, ratio))

	fmt.Println("\n4) 翻页期间插入新行：OFFSET 出重复，键集不重复不漏行")
	ot := newTable(100)
	op1 := ot.offsetPage(0)
	ot.rows = append([]row{{score: 999, id: 100000}}, ot.rows...)
	op2 := ot.offsetPage(1)
	overlap := 0
	for _, a := range op1 {
		for _, b := range op2 {
			if a == b {
				overlap++
			}
		}
	}
	check("OFFSET 第 1/2 页出现重复行", overlap > 0, fmt.Sprint(overlap))

	kt2 := newTable(100)
	kp1 := kt2.keysetPage(nil)
	kt2.rows = append([]row{{score: 999, id: 100000}}, kt2.rows...)
	kp2 := kt2.keysetPage(&kp1[len(kp1)-1])
	dup := 0
	for _, a := range kp1 {
		for _, b := range kp2 {
			if a == b {
				dup++
			}
		}
	}
	check("键集第 1/2 页无重复", dup == 0, fmt.Sprint(dup))
	full := append([]row(nil), kt2.rows...)
	sortRows(full)
	check("键集第 2 页正好是新序里第 1 页末行之后的 10 行", same(kp2, full[11:21]),
		fmt.Sprint(keys(kp2)))
	check("新插入的行落在第 1 页，不破坏已读页", full[0] == (row{score: 999, id: 100000}))

	fmt.Println("\n5) 并列 score 必须带 tie-breaker")
	tie := newTable(40)
	for i := range tie.rows {
		tie.rows[i] = row{score: 100, id: 40 - i} // 全部同分
	}
	sortRows(tie.rows)
	badPage := tie.keysetPageByScoreOnly(100)
	ordered2 := append([]row(nil), tie.rows...)
	sortRows(ordered2)
	goodPage := tie.keysetPage(&ordered2[9])
	badPage2 := tie.keysetPageByScoreOnly(100)
	check("只用 score 做游标时无论翻多少次都停在第一页（退化成 offset 行为）",
		same(badPage, badPage2) && same(badPage, ordered2[:10]), fmt.Sprint(keys(badPage)[:2]))
	check("(score, id) 行值比较能正确越过同行", goodPage[0] == ordered2[10],
		fmt.Sprintf("%v vs %v", goodPage[0], ordered2[10]))

	fmt.Println("\n6) 换 LIMIT 触发不同计划时页与页不衔接（文档：inconsistent results）")
	base := newTable(200)
	byIndex := append([]row(nil), base.rows...)
	sortRows(byIndex)
	bySort := append([]row(nil), base.rows...)
	sort.SliceStable(bySort, func(i, j int) bool { return bySort[i].score < bySort[j].score })
	inter := 0
	for _, a := range byIndex[:10] {
		for _, b := range bySort[10:20] {
			if a == b {
				inter++
			}
		}
	}
	check("两套顺序下第 1/2 页完全不衔接（交集为空）", inter == 0, fmt.Sprint(inter))
	check("OFFSET 0 与键集首页取到同一页", same(base.offsetPage(0), newTable(200).keysetPage(nil)))

	fmt.Printf("\n断言结果：pass=%d fail=%d\n", checks.pass, checks.fail)
	if checks.fail > 0 {
		os.Exit(1)
	}
}

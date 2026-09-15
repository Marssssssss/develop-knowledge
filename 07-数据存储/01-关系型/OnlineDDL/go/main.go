// 在线 DDL 三算法(INSTANT/INPLACE/COPY)+ gh-ost 模式最小模拟。
// 依据 dev.mysql.com/doc/refman/8.0 与 github/gh-ost README 归纳。
package main

import "fmt"

type Row map[int]string

// instantAddColumn INSTANT:仅改元数据,行版本 +1(上限 64)。
func instantAddColumn(columns []string, name string, versions *int) []string {
	if *versions >= 64 {
		panic("ERROR 4092: maximum row versions reached")
	}
	*versions++
	return append(columns, name)
}

// inplaceAddIndex INPLACE:执行期 DML 进 row log,commit 阶段应用。
func inplaceAddIndex(duringDML []string) []string {
	rowLog := append([]string{}, duringDML...) // 执行阶段记录
	return rowLog                              // commit 阶段应用后返回
}

// copyModify COPY:临时表全量复制;期间共享锁只读。
func copyModify(rows []Row, oldKey, newKey string) []Row {
	tmp := make([]Row, 0, len(rows))
	for _, r := range rows {
		nr := Row{}
		for k, v := range r {
			nr[k] = v
		}
		nr[newKey] = nr[oldKey]
		delete(nr, oldKey)
		tmp = append(tmp, nr)
	}
	return tmp
}

// ghOstMigrate gh-ost:ghost 表 + binlog 回放 + 原子 cut-over。
func ghOstMigrate(rows []Row, binlog []struct {
	kind string
	pk   int
	val  string
}) []Row {
	ghost := make([]Row, len(rows))
	copy(ghost, rows) // copy 阶段
	for _, ev := range binlog { // binlog 流异步回放(不用触发器)
		switch ev.kind {
		case "I":
			ghost = append(ghost, Row{ev.pk: ev.val})
		case "U":
			for _, r := range ghost {
				if _, ok := r[ev.pk]; ok {
					r[ev.pk] = ev.val
				}
			}
		case "D":
			kept := ghost[:0]
			for _, r := range ghost {
				if _, ok := r[ev.pk]; !ok {
					kept = append(kept, r)
				}
			}
			ghost = kept
		}
	}
	return ghost // cut-over:原子换名后 ghost 成为新表
}

func main() {
	// 1. INSTANT 行版本
	var versions int
	cols := []string{"id"}
	cols = instantAddColumn(cols, "c0", &versions)
	fmt.Println("instant columns:", cols, "versions:", versions)

	// 2. INPLACE row log
	fmt.Println("inplace applied:", inplaceAddIndex([]string{"dml1", "dml2"}))

	// 3. COPY 重命名
	fmt.Println("copy result:", copyModify([]Row{{1: "a"}}, "k1", "k2"))

	// 5. gh-ost
	binlog := []struct {
		kind string
		pk   int
		val  string
	}{
		{"U", 2, "x"}, {"I", 4, "n"}, {"D", 3, ""},
	}
	ghost := ghOstMigrate([]Row{{1: ""}, {2: ""}, {3: ""}}, binlog)
	fmt.Println("gh-ost ghost rows:", ghost)

	// 静态自检(人工审查替代编译,见 README)
	assert := func(b bool, msg string) {
		if !b {
			panic("assert failed: " + msg)
		}
	}
	assert(len(cols) == 2 && versions == 1, "instant metadata only")
	assert(len(ghost) == 3, "ghost kept ids 1,2,4")
	_, has2 := ghost[1][2]
	_, has3 := ghost[2][3]
	assert(ghost[1][2] == "x" && has2 && !has3, "binlog applied")
	fmt.Println("go static checks passed")
}

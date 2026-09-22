package main

import "fmt"

func buildCluster() []*SSTableIndex {
	ka0 := PrimaryKey{PK: "pk_a0", Token: 40}
	ka1 := PrimaryKey{PK: "pk_a1", Token: 20}
	kb0 := PrimaryKey{PK: "pk_b0", Token: 5}
	kb1 := PrimaryKey{PK: "pk_b1", Token: 30}

	ma := NewMemtableIndex("name")
	ma.Add("John", ka1)
	ma.Add("Boris", ka0)
	a := BuildSSTableIndex("A", ma, []PrimaryKey{ka0, ka1})

	mb := NewMemtableIndex("name")
	mb.Add("John", kb0)
	mb.Add("Caleb", kb1)
	b := BuildSSTableIndex("B", mb, []PrimaryKey{kb0, kb1})
	return []*SSTableIndex{a, b}
}

func main() {
	idxs := buildCluster()

	fmt.Println("=== 1. 两层索引结构 ===")
	for _, layer := range []string{"per_sstable", "per_column"} {
		fmt.Printf("  %-12s %v\n", layer, indexComponents()[layer])
	}

	fmt.Println()
	fmt.Println("=== 2. 磁盘结构按类型选 ===")
	for _, v := range []interface{}{"John", 21, 3.5, true} {
		fmt.Printf("  %-8v -> %s\n", v, onDiskStructure(v))
	}

	fmt.Println()
	fmt.Println("=== 3. rowID 是 SSTable 局部的 ===")
	for _, idx := range idxs {
		fmt.Printf("  SSTable %s: rowID 0 -> %v, rowID 1 -> %v\n",
			idx.Name, idx.RowMapping.KeyFor(0), idx.RowMapping.KeyFor(1))
	}

	fmt.Println()
	fmt.Println("=== 4. 查询 John ===")
	good := Search(idxs, "John")
	bad := NaiveSearch(idxs, "John")
	fmt.Printf("  正确（带 SSTable 标识 + token 序）: %v\n", good)
	fmt.Printf("  错误（把 rowID 当全局）          : %v\n", bad)
	if fmt.Sprint(good) != fmt.Sprint(bad) {
		fmt.Println("  差异: 结果不同，错误实现张冠李戴")
	}

	fmt.Println()
	fmt.Println("=== 5. 结果按 token 序 ===")
	for _, term := range []string{"John", "Boris", "Caleb"} {
		var names []string
		for _, h := range Search(idxs, term) {
			names = append(names, h.PK)
		}
		if len(names) == 0 {
			names = []string{"无命中"}
		}
		fmt.Printf("  %-6s -> %v\n", term, names)
	}

	fmt.Println()
	fmt.Println("=== 6. 多列索引共享同一套 rowID ===")
	ka0 := PrimaryKey{PK: "pk_a0", Token: 40}
	ka1 := PrimaryKey{PK: "pk_a1", Token: 20}
	mage := NewMemtableIndex("age")
	mage.Add("21", ka1)
	mage.Add("50", ka0)
	ageIdx := BuildSSTableIndex("A", mage, []PrimaryKey{ka0, ka1})
	fmt.Printf("  name 索引里 pk_a1 的 rowID = %d\n", idxs[0].RowMapping.RowIDFor(ka1))
	fmt.Printf("  age  索引里 pk_a1 的 rowID = %d（相同 -> offset/token 只存一份）\n",
		ageIdx.RowMapping.RowIDFor(ka1))
}

package main


// Entry 是一个 (value, repetition, definition) 三元组；IsNull 表示值为 NULL。
type Entry struct {
	Value    any
	IsNull   bool
	Rep      int
	Def      int
}

// ShredColumn 把若干文档在某一列路径上切成三元组序列。
func ShredColumn(records []map[string]any, path []*Node) []Entry {
	var out []Entry
	ridx := RepeatedIndex(path)
	maxDef := MaxDefinitionLevel(path)

	var walk func(container any, i, curDef, curRep int)
	walk = func(container any, i, curDef, curRep int) {
		node := path[i]
		get := func() any {
			if m, ok := container.(map[string]any); ok {
				return m[node.Name]
			}
			return nil
		}
		switch node.Rep {
		case Repeated:
			items, _ := get().([]any)
			if len(items) == 0 {
				out = append(out, Entry{IsNull: true, Rep: curRep, Def: curDef})
				return
			}
			k := ridx[i]
			for j, item := range items {
				d := curDef + 1
				r := curRep
				if j > 0 {
					r = k
				}
				if node.IsLeaf() {
					out = append(out, Entry{Value: item, Rep: r, Def: d})
				} else {
					walk(item, i+1, d, r)
				}
			}
		case Optional:
			v := get()
			if v == nil {
				out = append(out, Entry{IsNull: true, Rep: curRep, Def: curDef})
				return
			}
			d := curDef + 1
			if node.IsLeaf() {
				out = append(out, Entry{Value: v, Rep: curRep, Def: d})
			} else {
				walk(v, i+1, d, curRep)
			}
		default: // required
			v := get()
			if node.IsLeaf() {
				out = append(out, Entry{Value: v, Rep: curRep, Def: curDef})
			} else {
				walk(v, i+1, curDef, curRep)
			}
		}
	}
	for _, rec := range records {
		walk(rec, 0, 0, 0)
	}
	for _, e := range out {
		if e.Def > maxDef {
			panic("definition level exceeds max")
		}
	}
	return out
}

// AssembleColumn 把三元组序列还原成每个文档在该路径上的嵌套结构。
func AssembleColumn(entries []Entry, path []*Node) []map[string]any {
	counts := DefCounts(path)
	var docs []map[string]any
	var repStack []map[string]any

	for _, e := range entries {
		if e.Rep == 0 {
			docs = append(docs, map[string]any{})
			repStack = nil
		} else if e.Rep-1 < len(repStack) {
			// r = k：第 k 个 repeated 节点开了新出现，只保留前 k-1 层
			repStack = repStack[:e.Rep-1]
		}
		parent := docs[len(docs)-1]
		k := 0
		for i, node := range path {
			if e.Def < counts[i] {
				break
			}
			if node.Rep == Repeated {
				k++
				lst, _ := parent[node.Name].([]any)
				if node.IsLeaf() {
					parent[node.Name] = append(lst, e.Value)
					break
				}
				var occ map[string]any
				if k < e.Rep && len(lst) > 0 {
					occ, _ = lst[len(lst)-1].(map[string]any)
				} else {
					occ = map[string]any{}
					lst = append(lst, occ)
					parent[node.Name] = lst
				}
				if len(repStack) >= k {
					repStack[k-1] = occ
				} else {
					repStack = append(repStack, occ)
				}
				parent = occ
				continue
			}
			if node.IsLeaf() {
				parent[node.Name] = e.Value
				break
			}
			sub, _ := parent[node.Name].(map[string]any)
			if sub == nil {
				sub = map[string]any{}
				parent[node.Name] = sub
			}
			parent = sub
		}
	}
	return docs
}


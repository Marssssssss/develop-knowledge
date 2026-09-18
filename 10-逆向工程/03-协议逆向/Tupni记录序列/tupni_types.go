// §3.4.4 长度决定方式与 §3.5 记录类型（从 tupni.go 拆出）。
package main


// collapseChildLoops §3.5：把属于某个子循环执行的整段折叠成一条虚指令。
func collapseChildLoops(Qi []string, segs [][3]interface{}) []string {
	out := []string{}
	for i := 0; i < len(Qi); {
		hit := -1
		vid := ""
		for _, sg := range segs {
			s0, e0 := sg[0].(int), sg[1].(int)
			if s0 <= i && i < e0 {
				hit, vid = e0, sg[2].(string)
				break
			}
		}
		if hit >= 0 {
			out = append(out, "V:"+vid)
			i = hit
		} else {
			out = append(out, Qi[i])
			i++
		}
	}
	return out
}

// qiOf §3.5：Qi = 第 i 次迭代里访问了该记录内字段的指令（保持出现顺序）。
func qiOf(iter []Inst, lo, hi int) []string {
	seq := []string{}
	for _, ins := range iter {
		inside := false
		for _, o := range ins.Offs {
			if lo <= o && o < hi {
				inside = true
			}
		}
		if !inside {
			continue
		}
		dup := false
		for _, x := range seq {
			if x == ins.Eip {
				dup = true
			}
		}
		if !dup {
			seq = append(seq, ins.Eip)
		}
	}
	return seq
}

// determineLength §3.4.4：(a) 终止记录 (b) 长度字段 (c) 隐式固定。
func determineLength(n int, eqChecks [][3]interface{}, condFields int) string {
	m := map[int]map[int]bool{}
	for _, c := range eqChecks {
		it, con, ok := c[0].(int), c[1].(int), c[2].(bool)
		if m[con] == nil {
			m[con] = map[int]bool{}
		}
		m[con][it] = ok
	}
	for _, checks := range m {
		allFalse, lastTrue := true, checks[n]
		for i := 1; i < n; i++ {
			if checks[i] {
				allFalse = false
			}
		}
		if allFalse && lastTrue {
			return "a"
		}
	}
	if condFields > 0 {
		return "b"
	}
	return "c"
}

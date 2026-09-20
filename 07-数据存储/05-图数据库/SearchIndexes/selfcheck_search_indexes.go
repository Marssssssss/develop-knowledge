// SearchIndexes 自检：谓词可解性、planner 选择、命名/幂等/复合收录。
package main

import "fmt"

var okCount int
var failures []string

func ck(name string, cond bool, detail string) {
	if cond {
		okCount++
		return
	}
	failures = append(failures, fmt.Sprintf("%s  %s", name, detail))
}

func eqStr(name, got, want string) {
	ck(name, got == want, fmt.Sprintf("got=%s want=%s", got, want))
}

func eqInt(name string, got, want int) {
	ck(name, got == want, fmt.Sprintf("got=%d want=%d", got, want))
}

func mustErr(name string, err error) {
	if err != nil {
		okCount++
		return
	}
	failures = append(failures, name+"  未返回 error")
}

func main() {
	// ---------------------------------------------- 谓词可解性
	eqInt("Range 谓词数", len(predsByType[Range]), 5)
	eqInt("Text 谓词数", len(predsByType[Text]), 5)
	eqInt("Point 谓词数", len(predsByType[Point]), 3)
	eqInt("Token 谓词数", len(predsByType[Token]), 2)
	ck("Range 不能解 contains", !predsByType[Range]["contains"], "")
	ck("Range 不能解 ends_with", !predsByType[Range]["ends_with"], "")
	ck("Text 能解 contains", predsByType[Text]["contains"], "")
	ck("Token 不能解 eq", !predsByType[Token]["eq"], "")
	ck("Point 不能解 range", !predsByType[Point]["range"], "")

	// ---------------------------------------------- trigram
	eqStr("trigrams(developer)", fmt.Sprint(Trigrams("developer")),
		"[dev eve vel elo lop ope per]")
	eqInt("trigram 个数 = len-2", len(Trigrams("developer")), 7)
	eqInt("两字符切不出 trigram", len(Trigrams("ab")), 0)
	eqStr("三字符恰好 1 个", fmt.Sprint(Trigrams("abc")), "[abc]")
	ck("CONTAINS 'vel' 命中", TextMatches("developer", "vel"), "")
	ck("CONTAINS 'per' 命中", TextMatches("developer", "per"), "")
	ck("CONTAINS 'xyz' 不命中", !TextMatches("developer", "xyz"), "")
	ck("短查询串退化为子串判定", TextMatches("developer", "ev"), "")

	// ---------------------------------------------- 建索引与默认类型
	s := NewSchema()
	s.DefaultTokenIndexes()
	eqInt("建库自带 2 个 token 索引", len(s.Indexes), 2)
	eqStr("自带的是 token 类型", s.Indexes[0].Type+","+s.Indexes[1].Type,
		Token+","+Token)

	i1, err := s.CreateIndex("idx_surname", NodeTarget, "Person",
		[]string{"surname"}, "", false)
	ck("建索引成功", err == nil, fmt.Sprint(err))
	eqStr("不指定类型 → Range", i1.Type, Range)
	i2, _ := s.CreateIndex("idx_nick", NodeTarget, "Person",
		[]string{"nickname"}, Text, false)
	eqStr("显式 TEXT", i2.Type, Text)
	_, _ = s.CreateIndex("idx_loc", NodeTarget, "Person",
		[]string{"sublocation"}, Point, false)
	i4, _ := s.CreateIndex("idx_knows", RelTarget, "KNOWS", []string{"since"},
		"", false)
	eqStr("关系索引 target", i4.Target, RelTarget)

	_, e1 := s.CreateIndex("bad", NodeTarget, "Person", []string{"x"}, Token,
		false)
	mustErr("token 索引不能带属性", e1)
	_, e2 := s.CreateIndex("bad2", NodeTarget, "Person", nil, "", false)
	mustErr("无属性的属性索引非法", e2)

	// ---------------------------------------------- 命名唯一
	_, e3 := s.CreateIndex("idx_surname", NodeTarget, "Person",
		[]string{"other"}, "", false)
	mustErr("重名索引报错", e3)
	s.Constraints = append(s.Constraints, "idx_surname2")
	_, e4 := s.CreateIndex("idx_surname2", NodeTarget, "Person",
		[]string{"x"}, "", false)
	mustErr("名字被约束占用也报错", e4)

	// ---------------------------------------------- 幂等
	_, e5 := s.CreateIndex("idx_surname3", NodeTarget, "Person",
		[]string{"surname"}, "", false)
	mustErr("默认重复创建（同模式同类型）报错", e5)
	before := len(s.Indexes)
	same, _ := s.CreateIndex("idx_surname3", NodeTarget, "Person",
		[]string{"surname"}, "", true)
	eqInt("IF NOT EXISTS 不新增索引", len(s.Indexes), before)
	eqStr("IF NOT EXISTS 返回既有索引", same.Name, "idx_surname")
	ck("IF NOT EXISTS 产生通知", len(s.Notification) >= 1, "")
	ck("通知含 has no effect",
		len(s.Notification) > 0 && contains(s.Notification[0], "has no effect"), "")

	// ---------------------------------------------- planner 选择
	s2 := NewSchema()
	s2.DefaultTokenIndexes()
	r, _ := s2.CreateIndex("range_name", NodeTarget, "Person",
		[]string{"name"}, "", false)
	_, _ = s2.CreateIndex("text_name", NodeTarget, "Person", []string{"name"},
		Text, false)
	_, _ = s2.CreateIndex("point_loc", NodeTarget, "Person", []string{"home"},
		Point, false)

	eqStr("eq 选 Range", Planner(s2.Indexes, "eq", "name", "", "").Name,
		"range_name")
	eqStr("starts_with 选 Range",
		Planner(s2.Indexes, "starts_with", "name", "", "").Name, "range_name")
	eqStr("contains 只能选 Text",
		Planner(s2.Indexes, "contains", "name", "", "").Name, "text_name")
	eqStr("ends_with 只能选 Text",
		Planner(s2.Indexes, "ends_with", "name", "", "").Name, "text_name")
	eqStr("distance 只能选 Point",
		Planner(s2.Indexes, "distance", "home", "", "").Name, "point_loc")
	eqStr("within_bbox 只能选 Point",
		Planner(s2.Indexes, "within_bbox", "home", "", "").Name, "point_loc")
	eqStr("label 只能选 token",
		Planner(s2.Indexes, "label", "", NodeTarget, "").Type, Token)
	ck("无属性覆盖 → 选不到",
		Planner(s2.Indexes, "eq", "unknown_prop", "", "") == nil, "")
	ck("只有 Range 时 contains 选不到",
		Planner([]*Index{r}, "contains", "name", "", "") == nil, "")

	hint, _ := PlanWithHint(s2.Indexes, "text_name", "eq", "name")
	eqStr("USING 强制走 Text", hint.Name, "text_name")
	_, e6 := PlanWithHint(s2.Indexes, "nope", "eq", "name")
	mustErr("USING 不存在的索引报错", e6)

	// ---------------------------------------------- 复合索引收录
	full := map[string]bool{"age": true, "country": true}
	ck("标签不符不收录",
		!IndexedMembers(full, []string{"age", "country"}, "Company", "Person"), "")
	ck("属性不全不收录",
		!IndexedMembers(map[string]bool{"age": true}, []string{"age", "country"},
			"Person", "Person"), "")
	ck("标签+全属性才收录",
		IndexedMembers(full, []string{"age", "country"}, "Person", "Person"), "")

	// ---------------------------------------------- can_solve 属性覆盖
	ck("覆盖属性才可解", CanSolve(r, "eq", "name"), "")
	ck("未覆盖属性不可解", !CanSolve(r, "eq", "other"), "")
	tk := &Index{Name: "t", Target: NodeTarget, Entity: "*", Type: Token}
	ck("token 可解 label", CanSolve(tk, "label", ""), "")
	ck("token 不可解 eq", !CanSolve(tk, "eq", "name"), "")

	fmt.Printf("断言通过: %d\n", okCount)
	if len(failures) > 0 {
		fmt.Printf("失败 %d 条:\n", len(failures))
		for _, f := range failures {
			fmt.Println("  - " + f)
		}
		return
	}
	fmt.Println("ALL OK")
}

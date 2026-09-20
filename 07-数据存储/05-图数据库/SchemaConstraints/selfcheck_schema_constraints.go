// SchemaConstraints 自检：四类约束的建立、命名/幂等、写入校验。
package main

import (
	"fmt"
	"strings"
)

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
	failures = append(failures, name+"  未报错")
}

func mustOK(name string, err error) {
	if err == nil {
		okCount++
		return
	}
	failures = append(failures, fmt.Sprintf("%s  意外报错: %v", name, err))
}

func node(id int, entity string, props map[string]interface{}) *Entity {
	return &Entity{ID: id, Target: NodeT, Entity: entity, Props: props}
}

func main() {
	// ---------------------------------------------- 四类约束的建立
	db := NewDatabase()
	u, e1 := db.CreateConstraint("uniq_email", Uniqueness, NodeT, "Person",
		[]string{"email"}, "", false)
	mustOK("建唯一性约束", e1)
	eqStr("唯一性 kind", u.Kind, Uniqueness)
	_, e2 := db.CreateConstraint("ex_name", Existence, NodeT, "Author",
		[]string{"name"}, "", false)
	mustOK("建存在性约束", e2)
	t, e3 := db.CreateConstraint("type_title", TypeCon, NodeT, "Movie",
		[]string{"title"}, "STRING", false)
	mustOK("建类型约束", e3)
	eqStr("类型约束 typeSpec", t.TypeSpec, "STRING")
	k, e4 := db.CreateConstraint("key_id", KeyCon, NodeT, "Director",
		[]string{"imdbId"}, "", false)
	mustOK("建 Key 约束", e4)
	eqStr("Key kind", k.Kind, KeyCon)
	r, e5 := db.CreateConstraint("uniq_order", Uniqueness, RelT, "SEQUEL_OF",
		[]string{"order"}, "", false)
	mustOK("建关系约束", e5)
	eqStr("关系约束 target", r.Target, RelT)

	_, b1 := db.CreateConstraint("bad", TypeCon, NodeT, "Movie",
		[]string{"title"}, "", false)
	mustErr("TYPE 约束必须给类型", b1)
	_, b2 := db.CreateConstraint("bad2", Existence, NodeT, "A",
		[]string{"x", "y"}, "", false)
	mustErr("EXISTENCE 只支持单属性", b2)
	_, b3 := db.CreateConstraint("bad3", Uniqueness, NodeT, "A", nil, "", false)
	mustErr("唯一性至少一个属性", b3)

	// ---------------------------------------------- 命名唯一
	_, b4 := db.CreateConstraint("uniq_email", KeyCon, NodeT, "Person",
		[]string{"x"}, "", false)
	mustErr("重名约束报错", b4)
	db.IndexNames["idx_taken"] = true
	_, b5 := db.CreateConstraint("idx_taken", Existence, NodeT, "A",
		[]string{"x"}, "", false)
	mustErr("名字被索引占用也报错", b5)

	// ---------------------------------------------- IF NOT EXISTS
	n0 := len(db.Constraints)
	same, _ := db.CreateConstraint("uniq_email2", Uniqueness, NodeT, "Person",
		[]string{"email"}, "", true)
	eqInt("同模式不同名 → 不创建", len(db.Constraints), n0)
	eqStr("返回既有约束", same.Name, "uniq_email")
	// 官方 Example 29：与「不同类型的已有约束」同名 → 名字冲突优先
	db.CreateConstraint("ex_name", Uniqueness, NodeT, "Person",
		[]string{"other"}, "", true)
	eqInt("同名不同类型 → 仍然不创建", len(db.Constraints), n0)
	ck("产生了通知", len(db.Notifications) >= 2, fmt.Sprint(db.Notifications))
	ck("通知含 has no effect",
		len(db.Notifications) > 0 && strings.Contains(db.Notifications[0],
			"has no effect"), "")
	_, b6 := db.CreateConstraint("uniq_email9", Uniqueness, NodeT, "Person",
		[]string{"email"}, "", false)
	mustErr("默认同模式不同名 → 报错", b6)

	// ---------------------------------------------- 存在性
	d2 := NewDatabase()
	d2.CreateConstraint("ex", Existence, NodeT, "Author", []string{"name"}, "",
		false)
	mustOK("有属性通过", d2.Add(node(1, "Author", map[string]interface{}{
		"name": "x"})))
	mustErr("缺属性报错", d2.Add(node(2, "Author", map[string]interface{}{})))
	mustErr("值为 null 报错", d2.Add(node(3, "Author",
		map[string]interface{}{"name": nil})))
	mustOK("不匹配标签不受约束", d2.Add(node(4, "Book",
		map[string]interface{}{})))

	// ---------------------------------------------- 类型
	d3 := NewDatabase()
	d3.CreateConstraint("tt", TypeCon, NodeT, "Movie", []string{"title"},
		"STRING", false)
	mustOK("STRING 值通过", d3.Add(node(1, "Movie",
		map[string]interface{}{"title": "a"})))
	mustErr("INTEGER 值不通过", d3.Add(node(2, "Movie",
		map[string]interface{}{"title": 1})))
	mustOK("缺失属性通过（null 的类型谓词恒 true）", d3.Add(node(3, "Movie",
		map[string]interface{}{})))
	mustOK("显式 null 通过", d3.Add(node(4, "Movie",
		map[string]interface{}{"title": nil})))

	ck("LIST<STRING NOT NULL> 接受字符串列表",
		ValueMatchesType([]interface{}{"a", "b"}, "LIST<STRING NOT NULL>"), "")
	ck("LIST<STRING NOT NULL> 拒绝含 null",
		!ValueMatchesType([]interface{}{"a", nil}, "LIST<STRING NOT NULL>"), "")
	ck("LIST<STRING NOT NULL> 拒绝非列表",
		!ValueMatchesType("a", "LIST<STRING NOT NULL>"), "")
	ck("空列表满足 NOT NULL",
		ValueMatchesType([]interface{}{}, "LIST<STRING NOT NULL>"), "")
	ck("联合类型接受字符串",
		ValueMatchesType("a", "STRING | LIST<STRING NOT NULL>"), "")
	ck("联合类型接受列表",
		ValueMatchesType([]interface{}{"a"}, "STRING | LIST<STRING NOT NULL>"), "")
	ck("联合类型拒绝其它",
		!ValueMatchesType(1, "STRING | LIST<STRING NOT NULL>"), "")
	ck("BOOLEAN 不是 INTEGER", !ValueMatchesType(true, "INTEGER"), "")
	ck("INTEGER 值通过 INTEGER", ValueMatchesType(1, "INTEGER"), "")
	ck("VECTOR<INT32>(42) 接受序列",
		ValueMatchesType([]interface{}{1}, "VECTOR<INT32>(42)"), "")

	// ---------------------------------------------- 唯一性
	d4 := NewDatabase()
	d4.CreateConstraint("uq", Uniqueness, NodeT, "Person", []string{"email"},
		"", false)
	mustOK("首个写入通过", d4.Add(node(1, "Person",
		map[string]interface{}{"email": "a@b.c"})))
	mustErr("重复值报错", d4.Add(node(2, "Person",
		map[string]interface{}{"email": "a@b.c"})))
	mustOK("不同值通过", d4.Add(node(3, "Person",
		map[string]interface{}{"email": "x@y.z"})))
	mustOK("不同标签不算冲突", d4.Add(node(4, "Company",
		map[string]interface{}{"email": "a@b.c"})))
	mustOK("缺属性不参与唯一性（口径）", d4.Add(node(5, "Person",
		map[string]interface{}{})))
	mustOK("第二个缺属性也不冲突（口径）", d4.Add(node(6, "Person",
		map[string]interface{}{})))

	d5 := NewDatabase()
	d5.CreateConstraint("uq2", Uniqueness, NodeT, "P", []string{"a", "b"}, "",
		false)
	mustOK("复合首行通过", d5.Add(node(1, "P", map[string]interface{}{
		"a": 1, "b": 2})))
	mustOK("复合仅一项相同不冲突", d5.Add(node(2, "P",
		map[string]interface{}{"a": 1, "b": 3})))
	mustErr("复合全同才冲突", d5.Add(node(3, "P", map[string]interface{}{
		"a": 1, "b": 2})))

	// ---------------------------------------------- Key = 存在性 + 唯一性
	d6 := NewDatabase()
	d6.CreateConstraint("k", KeyCon, NodeT, "Director", []string{"imdbId"}, "",
		false)
	mustOK("Key 首个通过", d6.Add(node(1, "Director",
		map[string]interface{}{"imdbId": 1})))
	mustErr("Key 拦重复", d6.Add(node(2, "Director",
		map[string]interface{}{"imdbId": 1})))
	mustErr("Key 拦缺失", d6.Add(node(3, "Director",
		map[string]interface{}{})))
	mustOK("Key 不拦不同值", d6.Add(node(4, "Director",
		map[string]interface{}{"imdbId": 2})))
	d7 := NewDatabase()
	d7.CreateConstraint("u", Uniqueness, NodeT, "D", []string{"id"}, "", false)
	mustOK("纯唯一性不拦缺失", d7.Add(node(1, "D",
		map[string]interface{}{})))

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

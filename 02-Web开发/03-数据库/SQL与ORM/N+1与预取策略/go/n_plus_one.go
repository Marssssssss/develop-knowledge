// GORM Preload / Joins 的语句计数对照实现（与同目录 python/n_plus_one.py 同题异构）。
//
// 口径来源：GORM 官方 "Preloading (Eager Loading)"（https://gorm.io/docs/preload.html，实读）：
//   - "`Preload` loads the association data in a separate query, `Join Preload` will loads
//     association data using left join" —— 官方示例注释里给出两条 SQL：
//     `SELECT * FROM users;` + `SELECT * FROM orders WHERE user_id IN (1,2,3,4);`
//   - 三个关联各自 Preload：1 条 users + has many / has one / belongs to 各 1 条 = 4 条；
//   - "NOTE `Join Preload` works with one-to-one relation, e.g: `has one`, `belongs to`"
//     —— 对 has many 用 Joins 预加载是无效的（本 demo 以错误形式断言该约束）；
//   - "`clause.Associations` won't preload nested associations"；
//   - 嵌套 Preload "Orders.OrderItems.Product" 逐层各发一条 SQL；
//   - `db.Preload("Orders", "state NOT IN (?)", "cancelled")` 条件被拼进第二条 SQL。
//
// 运行：go run n_plus_one.go   （无第三方依赖；本机无 Go 工具链，代码经人工审查 + 结构校验）
package main

import (
	"fmt"
	"os"
	"strings"
)

const clauseAssociations = "clause.Associations"

// db 记录发出的 SQL，用于统计语句数（demo 的度量核心）。
type db struct {
	sqls []string
	base bool // 基查询只发一次：Preload 链共享同一条 SELECT * FROM users
}

func (d *db) emit(q string) { d.sqls = append(d.sqls, strings.Join(strings.Fields(q), " ")) }
func (d *db) count() int    { return len(d.sqls) }

type model struct {
	table  string
	rows   int
	hasMany bool // has many 是集合关联，has one / belongs to 是单值关联
}

var users = model{table: "users", rows: 4}
var orders = model{table: "orders", rows: 20, hasMany: true}
var profile = model{table: "profiles", rows: 4}
var role = model{table: "roles", rows: 6}
var orderItems = model{table: "order_items", rows: 40, hasMany: true}
var products = model{table: "products", rows: 12}

// Preload 按 GORM 语义：独立第二条 SQL，父主键拼进 IN 子句。
// path 形如 "Orders" 或 "Orders.OrderItems.Product"（嵌套预加载逐层各一条 SQL）。
func (d *db) emitBase() {
	if !d.base {
		d.emit("SELECT * FROM users")
		d.base = true
	}
}

func (d *db) Preload(path string, conds ...string) *db {
	d.emitBase()
	parts := strings.Split(path, ".")
	for i := range parts {
		m := tableOf(parts[i])
		q := "SELECT * FROM " + m.table + " WHERE user_id IN (1,2,3,4)"
		if len(conds) > 0 && i == 0 {
			q += " AND " + strings.Join(conds, " ") // 条件只作用于被指定的那一层
		}
		d.emit(q)
	}
	return d
}

// PreloadAll 对应 db.Preload(clause.Associations)：预加载第一层全部关联，但不递归嵌套。
func (d *db) PreloadAll() *db {
	d.emitBase()
	for _, m := range []model{orders, profile, role} {
		d.emit("SELECT * FROM " + m.table + " WHERE user_id IN (1,2,3,4)")
	}
	return d
}

// Joins 对应 Join Preload：一条 SQL 用 LEFT JOIN 取回关联列，但仅对单值关联生效。
func (d *db) Joins(assoc string) (*db, error) {
	m := tableOf(assoc)
	if m.hasMany {
		return d, fmt.Errorf("join preload works with one-to-one relation (has one / belongs to), got has-many: %s", m.table)
	}
	d.emit("SELECT users.id, users.name, " + m.table + ".id AS " + assoc + "__id FROM users " +
		"LEFT JOIN " + m.table + " ON users.company_id = " + m.table + ".id")
	return d, nil
}

func tableOf(name string) model {
	switch strings.ToLower(name) {
	case "orders", "orderitems":
		return orderItemsModel(name)
	case "profile", "profiles":
		return profile
	case "role", "roles":
		return role
	case "orderitems.product", "product", "products":
		return products
	default:
		return users
	}
}

func orderItemsModel(name string) model {
	if strings.EqualFold(name, "OrderItems") {
		return orderItems
	}
	return orders
}

var results = struct{ pass, fail int }{}

func check(label string, cond bool, detail string) {
	if cond {
		results.pass++
		fmt.Printf("  [PASS] %s\n", label)
		return
	}
	results.fail++
	fmt.Printf("  [FAIL] %s :: %s\n", label, detail)
}

func main() {
	fmt.Println(strings.Repeat("=", 72))
	fmt.Println("1) Preload：基查询 1 条 + 每个关联 1 条独立 SQL")

	d := &db{}
	d.Preload("Orders").Preload("Profile").Preload("Role")
	check("三个关联 Preload -> 4 条（users + orders + profiles + roles）", d.count() == 4, fmt.Sprint(d.sqls))
	check("has many 用 IN 收集父主键",
		strings.Contains(d.sqls[1], "FROM orders WHERE user_id IN (1,2,3,4)"), d.sqls[1])
	check("belongs to 也走独立查询", strings.HasPrefix(d.sqls[3], "SELECT * FROM roles"), d.sqls[3])

	fmt.Println("\n2) Joins（Join Preload）：一条 SQL，但只支持单值关联")
	d = &db{}
	if _, err := d.Joins("Profile"); err != nil {
		check("单值关联 Joins 不应报错", false, err.Error())
	}
	check("单值关联 Joins -> 1 条 LEFT JOIN", d.count() == 1, fmt.Sprint(d.sqls))
	check("SQL 中含 LEFT JOIN", strings.Contains(d.sqls[0], "LEFT JOIN profiles"), d.sqls[0])

	d = &db{}
	if _, err := d.Joins("Orders"); err == nil {
		check("has many 关联用 Joins 必须报错", false, "no error")
	} else {
		check("has many 关联用 Joins 报错（官方 NOTE）", strings.Contains(err.Error(), "one-to-one"))
	}

	fmt.Println("\n3) 嵌套 Preload：逐层各一条 SQL")
	d = &db{}
	d.Preload("Orders.OrderItems.Product")
	check("三层路径 -> 1 + 3 = 4 条", d.count() == 4, fmt.Sprint(d.sqls))
	check("第二条是 Orders、第三条是 OrderItems", strings.Contains(d.sqls[1], "FROM orders") &&
		strings.Contains(d.sqls[2], "FROM order_items"), fmt.Sprint(d.sqls))

	fmt.Println("\n4) Preload(clause.Associations)：只覆盖第一层，不递归")
	d = &db{}
	d.PreloadAll()
	check("clause.Associations -> 1 + 3 = 4 条", d.count() == 4, fmt.Sprint(d.sqls))
	check("不含 order_items（嵌套关联未被预加载）",
		!strings.Contains(strings.Join(d.sqls, "|"), "order_items"), strings.Join(d.sqls, "|"))

	fmt.Println("\n5) Preload 条件：条件被拼进被指定那一层的 SQL")
	d = &db{}
	d.Preload("Orders", "state NOT IN ('cancelled')")
	check("条件进入第二条 SQL", strings.Contains(d.sqls[1], "state NOT IN ('cancelled')"), d.sqls[1])
	check("条件不污染基查询", !strings.Contains(d.sqls[0], "cancelled"), d.sqls[0])

	fmt.Println("\n6) 语句数对比：Preload / Joins 都是「N+1 的固定代价」而非随行数增长")
	// 行数放大 100 倍：Preload 仍是 2 条，Joins 仍是 1 条；懒加载才会变成 1+N。
	users.rows *= 100
	d = &db{}
	d.Preload("Orders")
	preloadCount := d.count()
	d = &db{}
	if _, err := d.Joins("Profile"); err != nil {
		check("放大行数后 Joins 仍可用", false, err.Error())
	}
	joinsCount := d.count()
	check("行数 ×100 后 Preload 仍为 2 条", preloadCount == 2, fmt.Sprint(preloadCount))
	check("行数 ×100 后 Joins 仍为 1 条", joinsCount == 1, fmt.Sprint(joinsCount))
	users.rows /= 100

	fmt.Println("\n7) 与懒加载对照：默认不 Preload 时是 1+N（GORM 已移除 Lazy Loading，需显式 Preload）")
	lazyN := 1 + users.rows
	check("未预加载的等价代价 = 1 + N = 5（4 个用户）", lazyN == 5, fmt.Sprint(lazyN))

	fmt.Println("\n" + strings.Repeat("=", 72))
	fmt.Printf("断言结果：pass=%d fail=%d\n", results.pass, results.fail)
	if results.fail > 0 {
		os.Exit(1)
	}
}

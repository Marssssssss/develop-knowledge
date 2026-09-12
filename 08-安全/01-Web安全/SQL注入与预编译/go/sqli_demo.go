// SQL 注入防御 - Go database/sql 占位符演示
// 4 个子 demo:字符串拼接 / 参数化 / 允许表名白名单 / 整型 LIMIT 安全拼接
package main

import (
	"database/sql"
	"fmt"
	"strings"
	"time"

	_ "github.com/mattn/go-sqlite3" // go-sqlite3 驱动
)

// ───────────── 初始化内存数据库 + 测试数据 ─────────────

func setupDB() *sql.DB {
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		panic(err)
	}
	_, err = db.Exec(`CREATE TABLE users (
		id INTEGER PRIMARY KEY,
		name TEXT NOT NULL,
		role TEXT NOT NULL DEFAULT 'user',
		password_hash TEXT NOT NULL
	)`)
	if err != nil {
		panic(err)
	}
	stmt, _ := db.Prepare("INSERT INTO users (name, role, password_hash) VALUES (?, ?, ?)")
	defer stmt.Close()
	stmt.Exec("alice", "user", "hash_alice")
	stmt.Exec("bob", "admin", "hash_bob")
	return db
}

// ───────────── Demo 1: fmt.Sprintf 拼接 vs 参数化 ─────────────

func demoStringConcat(db *sql.DB) {
	fmt.Println(strings.Repeat("─", 65))
	fmt.Println("[Demo 1] fmt.Sprintf 拼接 vs database/sql 参数化")
	fmt.Println(strings.Repeat("─", 65))

	userInput := "' OR '1'='1"

	// ❌ 不安全:Sprintf 拼接
	naiveSQL := fmt.Sprintf("SELECT id, name, role FROM users WHERE name='%s'", userInput)
	rows, _ := db.Query(naiveSQL)
	fmt.Printf("  [UNSAFE] sql = %s\n", naiveSQL)
	for rows.Next() {
		var id int
		var name, role string
		rows.Scan(&id, &name, &role)
		fmt.Printf("           (%d, %q, %q)\n", id, name, role)
	}
	rows.Close()
	fmt.Println("           → 攻击者拿到所有用户(包括 admin)")
	fmt.Println()

	// ✅ 安全:? 占位符
	safeSQL := "SELECT id, name, role FROM users WHERE name=?"
	rows, _ = db.Query(safeSQL, userInput)
	fmt.Printf("  [SAFE]   sql = %s\n", safeSQL)
	fmt.Printf("           参数 = (%q,)\n", userInput)
	count := 0
	for rows.Next() {
		var id int
		var name, role string
		rows.Scan(&id, &name, &role)
		fmt.Printf("           (%d, %q, %q)\n", id, name, role)
		count++
	}
	rows.Close()
	fmt.Printf("           → 返回 %d 行,整段当 name 字面量\n", count)
	fmt.Println()
}

// ───────────── Demo 2: Prepared Statement 复用 ─────────────

func demoPreparedStatement(db *sql.DB) {
	fmt.Println(strings.Repeat("─", 65))
	fmt.Println("[Demo 2] Prepared Statement 复用(性能 + 安全性)")
	fmt.Println(strings.Repeat("─", 65))

	stmt, err := db.Prepare("SELECT id, name FROM users WHERE role=?")
	if err != nil {
		panic(err)
	}
	defer stmt.Close()

	// 预编译一次,多次 EXECUTE
	inputs := []string{"admin", "user", "admin' OR '1'='1"}
	for _, in := range inputs {
		rows, _ := stmt.Query(in)
		var names []string
		for rows.Next() {
			var id int
			var name string
			rows.Scan(&id, &name)
			names = append(names, name)
		}
		rows.Close()
		fmt.Printf("  role=%-30q → %v\n", in, names)
	}
	fmt.Println()
	fmt.Println("  → db.Prepare() 一次 PREPARE,后续 EXECUTE 复用执行计划")
	fmt.Println("    参数化 + 预编译 = OWASP 推荐的'金标准'")
	fmt.Println()
}

// ───────────── Demo 3: 表名白名单 ─────────────

var (
	allowedTables    = map[string]bool{"users": true, "orders": true, "products": true}
	allowedColumns   = map[string]bool{"id": true, "name": true, "role": true, "created_at": true}
	allowedDirection = map[string]bool{"ASC": true, "DESC": true}
)

func safeSelect(table, column, direction string) (string, error) {
	if !allowedTables[table] {
		return "", fmt.Errorf("invalid table: %q", table)
	}
	if !allowedColumns[column] {
		return "", fmt.Errorf("invalid column: %q", column)
	}
	if !allowedDirection[direction] {
		return "", fmt.Errorf("invalid direction: %q", direction)
	}
	// 白名单后,table/column/direction 都是合法字面量,拼接安全
	return fmt.Sprintf("SELECT %s FROM %s ORDER BY id %s LIMIT 3", column, table, direction), nil
}

func demoAllowList() {
	fmt.Println(strings.Repeat("─", 65))
	fmt.Println("[Demo 3] Allow-list 白名单(表名/列名/排序方向)")
	fmt.Println(strings.Repeat("─", 65))

	// 合法
	if sql, err := safeSelect("users", "name", "ASC"); err == nil {
		fmt.Printf("  [合法]   sql = %s\n", sql)
	}

	// 攻击:恶意表名
	if _, err := safeSelect("users; DROP TABLE users;--", "name", "ASC"); err != nil {
		fmt.Printf("  🛑 BLOCK  %v\n", err)
	}
	// 攻击:恶意列名(UNION)
	if _, err := safeSelect("users", "name UNION SELECT password_hash FROM users--", "ASC"); err != nil {
		fmt.Printf("  🛑 BLOCK  %v\n", err)
	}
	// 攻击:恶意方向(SLEEP)
	if _, err := safeSelect("users", "name", "ASC; DROP TABLE users--"); err != nil {
		fmt.Printf("  🛑 BLOCK  %v\n", err)
	}
	fmt.Println()
	fmt.Println("  → 表名/列名/direction 无法用 ? 占位符,必须白名单映射为字面量")
	fmt.Println()
}

// ───────────── Demo 4: 整型 LIMIT 安全拼接 ─────────────

func demoIntLimit(db *sql.DB) {
	fmt.Println(strings.Repeat("─", 65))
	fmt.Println("[Demo 4] 整型 LIMIT 安全拼接(strconv.Atoi 验证)")
	fmt.Println(strings.Repeat("─", 65))

	// LIMIT ? 在 SQLite 是支持的(go-sqlite3 实现了)
	// 但 MySQL 旧版本不支持,所以常见做法是 int 强转
	limitStr := "5; DROP TABLE users;--"

	// ❌ 不安全:直接拼字符串
	unsafeSQL := fmt.Sprintf("SELECT id, name FROM users LIMIT %s", limitStr)
	fmt.Printf("  [UNSAFE] sql = %s\n", unsafeSQL)
	fmt.Println("           → 多语句执行,数据库可能 DROP TABLE")
	fmt.Println()

	// ✅ 安全:strconv.Atoi 验证为整数
	var limit int
	if _, err := fmt.Sscanf(limitStr, "%d", &limit); err != nil || limit <= 0 {
		limit = 10
	}
	safeSQL := fmt.Sprintf("SELECT id, name FROM users LIMIT %d", limit)
	rows, _ := db.Query(safeSQL)
	fmt.Printf("  [SAFE]   limit 解析为 int = %d(失败回退 10)\n", limit)
	fmt.Printf("           sql = %s\n", safeSQL)
	for rows.Next() {
		var id int
		var name string
		rows.Scan(&id, &name)
		fmt.Printf("           (%d, %q)\n", id, name)
	}
	rows.Close()
	fmt.Println()

	// 参数化 LIMIT(SQLite 支持 ?)
	paramSQL := "SELECT id, name FROM users LIMIT ?"
	rows, _ = db.Query(paramSQL, limit)
	fmt.Printf("  [最佳]   sql = %s  参数 = (%d,)\n", paramSQL, limit)
	fmt.Println("           → 数据库协议层直接接收整型,无需字符串转换")
	rows.Close()
	fmt.Println()
}

func main() {
	fmt.Println(strings.Repeat("=", 65))
	fmt.Println("SQL 注入防御 — Go database/sql 参数化查询")
	fmt.Println(strings.Repeat("=", 65))
	fmt.Println()

	db := setupDB()
	demoStringConcat(db)
	demoPreparedStatement(db)
	demoAllowList()
	demoIntLimit(db)

	fmt.Println("编译运行提示:")
	fmt.Println("  cd SQL注入与预编译/go && go mod init sqli_demo")
	fmt.Println("  go get github.com/mattn/go-sqlite3")
	fmt.Println("  go run sqli_demo.go")
	_ = time.Now() // 防止 go-sqlite3 编译时检查时间
}
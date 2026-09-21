// Package rls —— PostgreSQL 行级安全（RLS）与多租户（官方文档 + rowsecurity.c 转写）。
//
// 与 Python 版的**显式语言差异**：
//   * 策略表达式是闭包，这里用 func(Row, *Role) bool 显式传入；
//   * Python 的「异常」改成 (bool, error)；
//   * Python 里给 Role 动态挂 tenant / local 字段，Go 里必须在结构体里声明，
//     这正好暴露了一个真实的坑：**上下文信息必须是显式的**，不能靠运行时打补丁。
package main

import "fmt"

// Row 是一行数据。
type Row = map[string]any

// Expr 是策略里的 SQL 表达式（这里用谓词函数代替）。
type Expr func(Row, *Role) bool

// Role 是数据库角色。
type Role struct {
	Name      string
	Superuser bool
	ByPassRLS bool
	Members   map[string]bool
	Tenant    string // 多租户场景下的租户标识
	Local     bool   // 是否本地连接（用于 restrictive 策略）
}

func (r *Role) IsMemberOf(role string) bool {
	return r.Name == role || r.Members[role]
}

// Policy 是一条行级安全策略。
type Policy struct {
	Name        string
	Cmd         string // ALL / SELECT / INSERT / UPDATE / DELETE
	Roles       []string
	Using       Expr
	WithCheck   Expr
	Restrictive bool
}

// NewPolicy 只写 USING 时，WITH CHECK 隐含与 USING 相同（官方规则）。
func NewPolicy(name, cmd string, roles []string, using Expr, withCheck Expr, restrictive bool) (*Policy, error) {
	switch cmd {
	case "ALL", "SELECT", "INSERT", "UPDATE", "DELETE":
	default:
		return nil, fmt.Errorf("bad command: %s", cmd)
	}
	if withCheck == nil {
		withCheck = using
	}
	return &Policy{Name: name, Cmd: cmd, Roles: roles, Using: using,
		WithCheck: withCheck, Restrictive: restrictive}, nil
}

// AppliesTo 判断策略对 (角色, 命令) 是否生效。
func (p *Policy) AppliesTo(r *Role, cmd string) bool {
	if p.Cmd != "ALL" && p.Cmd != cmd {
		return false
	}
	if len(p.Roles) == 0 { // PUBLIC
		return true
	}
	for _, role := range p.Roles {
		if r.IsMemberOf(role) {
			return true
		}
	}
	return false
}

// Table 是一张开启了（或没开启）RLS 的表。
type Table struct {
	Name       string
	Owner      string
	Rows       []Row
	Policies   []*Policy
	RLSEnabled bool
	RLSForced  bool
}

func NewTable(name, owner string) *Table { return &Table{Name: name, Owner: owner} }

func (t *Table) Enable(force bool) { t.RLSEnabled = true; t.RLSForced = force }

func (t *Table) AddPolicy(p *Policy) { t.Policies = append(t.Policies, p) }

// Bypasses 判断该角色是否整体绕过 RLS。
func (t *Table) Bypasses(r *Role) bool {
	if r.Superuser || r.ByPassRLS {
		return true
	}
	// 表属主默认绕过，除非 ALTER TABLE ... FORCE ROW LEVEL SECURITY
	return r.IsMemberOf(t.Owner) && !t.RLSForced
}

func (t *Table) applicable(r *Role, cmd string) []*Policy {
	if !t.RLSEnabled || t.Bypasses(r) {
		return nil
	}
	var out []*Policy
	for _, p := range t.Policies {
		if p.AppliesTo(r, cmd) {
			out = append(out, p)
		}
	}
	return out
}

func usingTrue(p *Policy, row Row, r *Role) bool {
	if p.Using == nil {
		return true
	}
	return p.Using(row, r)
}

func withCheckTrue(p *Policy, row Row, r *Role) bool {
	if p.WithCheck == nil {
		return true
	}
	return p.WithCheck(row, r)
}

// VisibleRows 返回该角色在给定命令下可见的行。
//
// 官方 rowsecurity.c/add_security_quals：permissive 用 OR_EXPR 串起来，
// restrictive 再 AND 上去；一张策略都没有时用隐含的 default-deny。
func (t *Table) VisibleRows(r *Role, cmd string) []Row {
	if !t.RLSEnabled || t.Bypasses(r) {
		return t.Rows
	}
	policies := t.applicable(r, cmd)
	if len(policies) == 0 {
		return nil // default-deny
	}
	var permissive, restrictive []*Policy
	for _, p := range policies {
		if p.Restrictive {
			restrictive = append(restrictive, p)
		} else {
			permissive = append(permissive, p)
		}
	}
	var out []Row
	for _, row := range t.Rows {
		visible := true
		if len(permissive) > 0 {
			visible = false
			for _, p := range permissive {
				if usingTrue(p, row, r) {
					visible = true
					break
				}
			}
		}
		if visible {
			for _, p := range restrictive {
				if !usingTrue(p, row, r) {
					visible = false
					break
				}
			}
		}
		if visible {
			out = append(out, row)
		}
	}
	return out
}

// CheckInsert 只看 WITH CHECK 一侧（INSERT 没有 USING）。
func (t *Table) CheckInsert(r *Role, row Row) error {
	policies := t.applicable(r, "INSERT")
	if !t.RLSEnabled || t.Bypasses(r) {
		return nil
	}
	if len(policies) == 0 {
		return fmt.Errorf("new row violates WITH CHECK OPTION (default-deny)")
	}
	for _, p := range policies {
		if p.Restrictive && !withCheckTrue(p, row, r) {
			return fmt.Errorf("new row violates WITH CHECK OPTION for %q", t.Name)
		}
	}
	for _, p := range policies {
		if !p.Restrictive && withCheckTrue(p, row, r) {
			return nil
		}
	}
	return fmt.Errorf("new row violates WITH CHECK OPTION for %q", t.Name)
}

// Insert 校验通过后写入。
func (t *Table) Insert(r *Role, row Row) error {
	if err := t.CheckInsert(r, row); err != nil {
		return err
	}
	t.Rows = append(t.Rows, row)
	return nil
}

// Update 先用 USING 选行，再用 WITH CHECK 校验新行；任一行不合规则整句报错。
func (t *Table) Update(r *Role, match func(Row) bool, changes Row) (int, error) {
	n := 0
	for _, row := range t.VisibleRows(r, "UPDATE") {
		if !match(row) {
			continue
		}
		newRow := Row{}
		for k, v := range row {
			newRow[k] = v
		}
		for k, v := range changes {
			newRow[k] = v
		}
		if err := t.checkWrite(r, newRow); err != nil {
			return n, err
		}
		for k, v := range changes {
			row[k] = v
		}
		n++
	}
	return n, nil
}

func (t *Table) checkWrite(r *Role, row Row) error {
	policies := t.applicable(r, "UPDATE")
	if !t.RLSEnabled || t.Bypasses(r) {
		return nil
	}
	if len(policies) == 0 {
		return fmt.Errorf("new row violates WITH CHECK OPTION (default-deny)")
	}
	for _, p := range policies {
		if p.Restrictive && !withCheckTrue(p, row, r) {
			return fmt.Errorf("new row violates WITH CHECK OPTION for %q", t.Name)
		}
	}
	for _, p := range policies {
		if !p.Restrictive && withCheckTrue(p, row, r) {
			return nil
		}
	}
	return fmt.Errorf("new row violates WITH CHECK OPTION for %q", t.Name)
}

// Delete 只能删看得见的行；看不见不报错，只是 0 行。
func (t *Table) Delete(r *Role, match func(Row) bool) int {
	n := 0
	visible := t.VisibleRows(r, "DELETE")
	for _, row := range visible {
		if match(row) {
			for i, trow := range t.Rows {
				if sameRow(trow, row) {
					t.Rows = append(t.Rows[:i], t.Rows[i+1:]...)
					break
				}
			}
			n++
		}
	}
	return n
}

func sameRow(a, b Row) bool {
	if len(a) != len(b) {
		return false
	}
	for k, v := range a {
		if b[k] != v {
			return false
		}
	}
	return true
}

// ReferentialCheck 参照完整性检查（唯一/主键/外键）**总是绕过** RLS。
func (t *Table) ReferentialCheck(row Row, col string) bool {
	for _, r := range t.Rows {
		if r[col] == row[col] {
			return true
		}
	}
	return false
}

// TenantIsolation 是多租户最常见的策略形态。
func TenantIsolation(col string) Expr {
	return func(row Row, r *Role) bool { return row[col] == r.Tenant }
}

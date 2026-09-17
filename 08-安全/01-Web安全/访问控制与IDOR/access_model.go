// 访问控制引擎(Go 侧):与 access_model.py 同构,用来对照"同一套策略在两种语言里的表达"。
// 依据:OWASP Authorization Cheat Sheet —— 默认拒绝、每请求校验、对象级授权(CWE-639)、
// ABAC/ReBAC 优于纯 RBAC、失败安全退出、审计留痕。
//
// 本机无 go 工具链,全部结论来自人工审查 + bracket/syntax 结构自检(见 README「性能边界」)。
package main

import (
	"fmt"
	"sort"
	"strings"
)

type Subject struct {
	UID      string
	Roles    map[string]bool
	Attrs    map[string]string
	Untrusted map[string]string // 客户端自称的属性:决策时一律不看
}

type Resource struct {
	Kind      string
	RID       string
	Owner     string
	Tenant    string
	Public    bool
	Sensitive bool
}

type Decision struct {
	Allow  bool
	Reason string
	Rule   string
}

type Rule struct {
	Name    string
	Effect  string // "allow" / "deny"
	Actions []string
	Pred    func(s Subject, r Resource) bool
}

type AuditRow struct {
	UID, Action, Object, Rule, Reason string
	Allow                             bool
}

type Engine struct {
	rules     []Rule
	rbac      map[string]map[string]bool
	relations map[string]bool // "uid|relation|object"
	handles   map[string]map[string]string
	Audit     []AuditRow
	seq       int
}

func NewEngine() *Engine {
	return &Engine{
		rbac:      map[string]map[string]bool{},
		relations: map[string]bool{},
		handles:   map[string]map[string]string{},
	}
}

func (e *Engine) AddRole(role string, perms ...string) {
	set := e.rbac[role]
	if set == nil {
		set = map[string]bool{}
		e.rbac[role] = set
	}
	for _, p := range perms {
		set[p] = true
	}
}

func (e *Engine) AddRule(name, effect string, actions []string, pred func(Subject, Resource) bool) {
	e.rules = append(e.rules, Rule{Name: name, Effect: effect, Actions: actions, Pred: pred})
}

func (e *Engine) Relate(uid, relation, object string) {
	e.relations[uid+"|"+relation+"|"+object] = true
}

func (e *Engine) Unrelated(object string) {
	for k := range e.relations {
		if strings.HasSuffix(k, "|"+object) {
			delete(e.relations, k)
		}
	}
	for session, table := range e.handles {
		for h, real := range table {
			if real == object {
				delete(table, h)
			}
		}
		if len(table) == 0 {
			delete(e.handles, session)
		}
	}
}

// ---- 会话级间接引用:句柄只在签发它的会话里可解析 ----
func (e *Engine) HandleFor(session, realID string) string {
	table := e.handles[session]
	if table == nil {
		table = map[string]string{}
		e.handles[session] = table
	}
	for h, real := range table {
		if real == realID {
			return h
		}
	}
	e.seq++
	h := fmt.Sprintf("h%d", e.seq)
	table[h] = realID
	return h
}

func (e *Engine) Resolve(session, handle string) (string, bool) {
	v, ok := e.handles[session][handle]
	return v, ok
}

// ---- RBAC:只认"角色有没有这个权限",不看对象 ----
func (e *Engine) RBACDecide(s Subject, action string, r Resource) Decision {
	names := []string{}
	for role := range s.Roles {
		names = append(names, role)
	}
	sort.Strings(names) // 保证结果与遍历顺序无关
	for _, role := range names {
		if e.rbac[role][action] {
			return Decision{true, "role " + role + " has " + action, "rbac:" + role}
		}
	}
	return Decision{false, "no role grants " + action, "rbac:none"}
}

// ---- 决策:deny-overrides + 默认拒绝,异常一律拒绝 ----
func (e *Engine) Decide(s Subject, action string, r Resource) (dec Decision) {
	defer func() {
		if rec := recover(); rec != nil {
			dec = Decision{false, "policy error -> deny", "policy-error"}
		}
	}()
	for _, effect := range []string{"deny", "allow"} {
		for _, rule := range e.rules {
			if rule.Effect != effect || !contains(rule.Actions, action) {
				continue
			}
			if rule.Pred(s, r) {
				ok := effect == "allow"
				return Decision{ok, rule.Name + " matched", rule.Name}
			}
		}
	}
	return Decision{false, "deny by default", "default-deny"}
}

func contains(list []string, v string) bool {
	for _, x := range list {
		if x == v {
			return true
		}
	}
	return false
}

// ---- 对象级校验:每个对象都要查 ----
func (e *Engine) CanTouchObject(s Subject, action string, r Resource) Decision {
	if r.Public && strings.HasSuffix(action, ":read") {
		return Decision{true, "public resource", "public:read"}
	}
	if r.Tenant != "" && s.Attrs["tenant"] != r.Tenant {
		return Decision{false, "cross-tenant access", "tenant-isolation"}
	}
	if e.relations[s.UID+"|auditor|tenant:"+r.Tenant] {
		return Decision{true, "tenant auditor", "rebac:auditor"}
	}
	if r.Owner != "" && r.Owner == s.UID {
		return Decision{true, "owner", "rebac:owner"}
	}
	if e.relations[s.UID+"|collaborator|"+r.Kind+":"+r.RID] {
		return Decision{true, "collaborator", "rebac:collaborator"}
	}
	return Decision{false, "no relation to this object", "object-level"}
}

func (e *Engine) Authorize(s Subject, action string, r Resource) Decision {
	first := e.Decide(s, action, r)
	e.logRow(s, action, r, first)
	if !first.Allow {
		return first
	}
	second := e.CanTouchObject(s, action, r)
	e.logRow(s, action, r, second)
	return second
}

func (e *Engine) logRow(s Subject, action string, r Resource, d Decision) {
	e.Audit = append(e.Audit, AuditRow{s.UID, action, r.Kind + ":" + r.RID, d.Rule, d.Reason, d.Allow})
}

func ReferenceEngine() *Engine {
	e := NewEngine()
	e.AddRole("accountant", "invoice:read", "invoice:write", "report:read")
	e.AddRole("admin", "invoice:read", "invoice:write", "invoice:delete", "user:manage")
	e.AddRole("viewer", "report:read")
	e.AddRule("on-shift-invoice", "allow", []string{"invoice:read", "invoice:write"},
		func(s Subject, r Resource) bool { return s.Roles["accountant"] && s.Attrs["on_shift"] == "true" })
	e.AddRule("admin-everything", "allow",
		[]string{"invoice:read", "invoice:write", "invoice:delete", "user:manage"},
		func(s Subject, r Resource) bool { return s.Roles["admin"] })
	e.AddRule("sensitive-needs-admin", "deny", []string{"invoice:read", "invoice:write"},
		func(s Subject, r Resource) bool { return r.Sensitive && !s.Roles["admin"] })
	return e
}

func main() {
	e := ReferenceEngine()
	alice := Subject{UID: "alice", Roles: map[string]bool{"accountant": true},
		Attrs: map[string]string{"tenant": "t1", "on_shift": "true"}, Untrusted: map[string]string{}}
	own := Resource{Kind: "invoice", RID: "inv-1", Owner: "alice", Tenant: "t1"}
	other := Resource{Kind: "invoice", RID: "inv-2", Owner: "bob", Tenant: "t1"}
	fmt.Println("RBAC 类型级:", e.RBACDecide(alice, "invoice:read", other).Allow)
	fmt.Println("对象级(自己的):", e.CanTouchObject(alice, "invoice:read", own).Allow)
	fmt.Println("对象级(别人的):", e.CanTouchObject(alice, "invoice:read", other).Allow, e.CanTouchObject(alice, "invoice:read", other).Rule)
	fmt.Println("完整链路:", e.Authorize(alice, "invoice:read", own).Allow, "/", e.Authorize(alice, "invoice:read", other).Allow)
	fmt.Println("句柄:", e.HandleFor("sess-a", "inv-1"))
	fmt.Println("审计行数:", len(e.Audit))
}

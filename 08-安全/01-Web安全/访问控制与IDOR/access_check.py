"""访问控制 / IDOR 自检。

断言来源:OWASP Authorization Cheat Sheet ——
最小权限(横向+纵向)、默认拒绝、每请求校验、全局配置优于逐方法检查、
ABAC/ReBAC 优于纯 RBAC、对象级授权(CWE-639 IDOR)、服务端校验、失败安全退出、
审计日志、间接引用、权限蔓延审查。
"""
from access_model import (AccessDenied, Engine, Resource, Subject,
                          build_reference_engine)

PASS = 0


def check(label, cond, detail=""):
    global PASS
    assert cond, f"FAIL {label} {detail}"
    PASS += 1
    print(f"  ok  {label}")


def session(uid, roles=(), tenant="t1", on_shift=True, **claim):
    """服务端从会话/JWT 里取出的可信主体;claim 是客户端自称的属性(不可信)。"""
    return Subject(uid=uid, roles=frozenset(roles), attrs={"tenant": tenant, "on_shift": on_shift},
                   untrusted=dict(claim))


alice_inv = Resource("invoice", "inv-1", owner="alice", tenant="t1")
bob_inv = Resource("invoice", "inv-2", owner="bob", tenant="t1")
pub_report = Resource("report", "rep-1", public=True, tenant="t1")
other_tenant = Resource("invoice", "inv-9", owner="carol", tenant="t2")
secret_inv = Resource("invoice", "inv-7", owner="alice", tenant="t1", sensitive=True)

print("默认拒绝与最小权限")
e = build_reference_engine()
nobody = session("dave", roles=())
check("无角色 → 类型级拒绝", not e.decide(nobody, "invoice:read", alice_inv).allow)
check("拒绝原因是 deny by default", e.decide(nobody, "invoice:read", alice_inv).reason == "deny by default")
check("未知动作 → 拒绝", not e.decide(session("dave", ["admin"]), "invoice:teleport", alice_inv).allow)
viewer = session("erin", roles=["viewer"])
check("viewer 可读报表", e.decide(viewer, "report:read", pub_report).allow)
check("viewer 不能写报表(未授予的动作)", not e.decide(viewer, "report:write", pub_report).allow)
check("viewer 不能借报表权限读发票(横纵都要限)", not e.decide(viewer, "invoice:read", alice_inv).allow)

print("ABAC 环境条件(职位 + 是否在班)")
acct = session("alice", roles=["accountant"], on_shift=True)
off = session("alice", roles=["accountant"], on_shift=False)
check("在班的会计拿到类型级许可", e.decide(acct, "invoice:read", alice_inv).allow)
check("下班的会计被拒(同一角色,不同环境)", not e.decide(off, "invoice:read", alice_inv).allow)

print("纯 RBAC 的失守点:类型级权限被当成对象级权限")
check("RBAC 也认为 alice 能读 bob 的发票(横向越权!)",
      e.rbac_decide(acct, "invoice:read", bob_inv).allow)
check("对象级校验拦住横向越权",
      not e.can_touch_object(acct, "invoice:read", bob_inv).allow)
check("对象级校验放行自己的对象",
      e.can_touch_object(acct, "invoice:read", alice_inv).allow)
check("完整链路:自己的 → 允许",
      e.authorize(acct, "invoice:read", alice_inv).allow)
check("完整链路:别人的 → 拒绝(规则过了,对象级没过)",
      not e.authorize(acct, "invoice:read", bob_inv).allow
      and e.authorize(acct, "invoice:read", bob_inv).rule == "object-level")
check("RBAC 也认为 admin 能删任何发票 —— 仅当策略真的这么配时",
      e.rbac_decide(session("root", ["admin"]), "invoice:delete", alice_inv).allow)

print("ReBAC:协作者关系 + 级联撤销")
e.relate("frank", "collaborator", "invoice:inv-1")
frank = session("frank", roles=["accountant"])
check("协作者可读被共享对象", e.authorize(frank, "invoice:read", alice_inv).allow)
check("协作者仍不可读未共享对象",
      not e.authorize(frank, "invoice:read", bob_inv).allow)
e.unrelated("invoice:inv-1")
check("对象删除后关系与句柄一并失效(不留孤儿授权)",
      not e.authorize(frank, "invoice:read", alice_inv).allow)

print("多租户隔离与显式拒绝优先")
check("跨租户 → 拒绝且原因是 tenant-isolation",
      e.can_touch_object(session("alice", ["admin"], tenant="t1"), "invoice:read", other_tenant).rule
      == "tenant-isolation")
admin_t1 = session("root", ["admin"], tenant="t1")
check("admin 角色本身不等于能碰任何对象(宽范围访问也要显式授予)",
      not e.authorize(admin_t1, "invoice:read", secret_inv).allow)
e.relate("root", "auditor", "tenant:t1")
check("显式授予租户级审计员后才放行",
      e.authorize(admin_t1, "invoice:read", secret_inv).allow)
check("审计员也跨不出租户", not e.authorize(admin_t1, "invoice:read", other_tenant).allow)
check("会计读敏感资源被显式 deny 覆盖(deny-overrides)",
      not e.authorize(acct, "invoice:read", secret_inv).allow
      and e.authorize(acct, "invoice:read", secret_inv).rule == "sensitive-needs-admin")

print("公开资源只读")
check("public 只读放行", e.can_touch_object(nobody, "report:read", pub_report).allow)
check("public 写不因公开而放行",
      not e.can_touch_object(nobody, "report:write", pub_report).allow)

print("客户端自称属性不参与决策")
liar = session("mallory", roles=[], on_shift=True, roles_claim="admin", is_admin=True)
check("自称 is_admin 无效", not e.authorize(liar, "user:manage", alice_inv).allow)
check("不可信属性与可信属性分仓存放",
      "is_admin" in liar.untrusted and "is_admin" not in liar.attrs)

print("IDOR / 对象引用")
e2 = build_reference_engine()
h_alice = e2.handle_for("sess-alice", "inv-1")
check("句柄不是真实 ID", h_alice != "inv-1")
check("句柄在本会话内可解析", e2.resolve("sess-alice", h_alice) == "inv-1")
check("同一对象的句柄稳定", e2.handle_for("sess-alice", "inv-1") == h_alice)
check("换会话拿到不同句柄", e2.handle_for("sess-bob", "inv-1") != h_alice)
check("别人的句柄解析不到(顺手改 URL 无效)",
      e2.resolve("sess-bob", h_alice) is None)
check("直接篡改 ID 也过不了对象级校验",
      not e2.authorize(acct, "invoice:read", Resource("invoice", h_alice, owner="bob",
                                                      tenant="t1")).allow)

print("失败安全退出与审计")
e3 = build_reference_engine()
e3.add_rule("boom", "allow", {"report:read"},
            lambda s, r: (_ for _ in ()).throw(RuntimeError("policy bug")))
check("策略抛异常 → 拒绝,不放行", not e3.decide(acct, "report:read", pub_report).allow)
check("异常原因被记录而不是被吞掉",
      "policy error" in e3.decide(acct, "report:read", pub_report).reason)
try:
    e3.guard(nobody, "invoice:read", alice_inv)
    check("guard 应抛 AccessDenied", False)
except AccessDenied as exc:
    check("guard 对外只给 403,不带策略细节", str(exc) == "403 forbidden")
    check("细节留在 decision 里供审计", exc.decision.rule == "default-deny")
check("审计记录含主体/动作/对象/结果/规则",
      e3.audit.rows and len(e3.audit.rows[0]) == 6, e3.audit.rows[:1])
check("每次 decide 都留痕", len(e3.audit.rows) == 3, len(e3.audit.rows))
check("审计顺序与调用顺序一致",
      [r[1] for r in e3.audit.rows] == ["report:read", "report:read", "invoice:read"])

print("权限蔓延:角色变化必须走显式重配")
e4 = build_reference_engine()
before = e4.rbac_decide(session("gina", ["accountant"], on_shift=True), "invoice:read", alice_inv)
e4.add_role("accountant", {"report:read"})           # 显式重新配置(撤掉 invoice 权限)
after = e4.rbac_decide(session("gina", ["accountant"], on_shift=True), "invoice:read", alice_inv)
check("重配后旧权限不残留", before.allow and not after.allow)
check("重配后新权限立即生效",
      e4.rbac_decide(session("gina", ["accountant"]), "report:read", pub_report).allow)

print(f"\n{PASS} 项断言全部通过 (授权 / 访问控制 / IDOR)")

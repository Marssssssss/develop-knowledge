"""行级安全（RLS）自检 —— 断言全部来自 PG 18 官方文档与 rowsecurity.c。

运行： python selfcheck_rls.py
"""

import sys

from main import CheckViolation, Policy, Role, Table, tenant_isolation

PASS = FAIL = 0


def ok(c, m):
    global PASS, FAIL
    if c:
        PASS += 1
    else:
        FAIL += 1
        print("FAIL:", m)


def eq(g, w, m):
    ok(g == w, "%s (got=%r want=%r)" % (m, g, w))


def raises(fn, exc, m):
    try:
        fn()
    except exc:
        ok(True, m)
        return
    except Exception as e:
        ok(False, "%s (raised %r)" % (m, e))
        return
    ok(False, "%s (no exception)" % m)


def accounts(force=False):
    """PG 文档里的 accounts 例子，属主是 alice。"""
    t = Table("accounts", owner="alice")
    t.rows.append({"id": 1, "manager": "alice", "tenant": "a"})
    t.rows.append({"id": 2, "manager": "bob", "tenant": "b"})
    t.enable(force=force)
    return t


def mgr_policy():
    # USING (manager = current_user) —— 只写 USING 时 WITH CHECK 隐含相同
    return Policy("p_mgr", roles=None, using=lambda r, u: r["manager"] == u.name)


# ------------------------------------------------------ A default-deny
def t_default_deny():
    t = accounts()
    bob = Role("bob")
    eq(t.visible_rows(bob), [], "A1 启用 RLS 但没有策略 = 默认拒绝（看不到任何行）")
    raises(lambda: t.insert(bob, {"id": 3, "manager": "bob"}), CheckViolation,
           "A2 同理，一行也插不进去")

    t2 = Table("t", owner="alice")     # 未启用 RLS
    t2.rows.append({"id": 1})
    eq(len(t2.visible_rows(Role("bob"))), 1, "A3 未启用 RLS 时不受影响")
    t2.add_policy(Policy("deny_all", using=lambda r, u: False))
    eq(len(t2.visible_rows(Role("bob"))), 1,
       "A3b 未启用 RLS 时策略完全不起作用（忘记 ENABLE 是最常见的漏洞）")


# -------------------------------------------------- B owner / BYPASSRLS
def t_bypass():
    t = accounts()
    alice = Role("alice")
    eq(len(t.visible_rows(alice)), 2, "B1 表属主默认绕过 RLS")

    t2 = accounts(force=True)
    eq(len(t2.visible_rows(Role("alice"))), 0, "B2 FORCE ROW LEVEL SECURITY 后属主也受限")

    # 有策略之后，属主仍然绕过（未 FORCE）
    t3 = accounts()
    t3.add_policy(mgr_policy())
    eq(len(t3.visible_rows(Role("alice"))), 2, "B3 未 FORCE 时属主始终看得到全部")

    su = Role("root", superuser=True)
    eq(len(t3.visible_rows(su)), 2, "B4 superuser 一律绕过")
    br = Role("svc", bypassrls=True)
    eq(len(t3.visible_rows(br)), 2, "B5 BYPASSRLS 角色一律绕过")


# ------------------------------------------------ C USING / WITH CHECK
def t_using_check():
    t = accounts()
    t.add_policy(mgr_policy())
    bob = Role("bob")
    vis = t.visible_rows(bob)
    eq([r["id"] for r in vis], [2], "C1 USING 只放行自己的行")

    # C2 INSERT 只校验 WITH CHECK；只写 USING 时 WITH CHECK 隐含相同
    t.insert(bob, {"id": 3, "manager": "bob"})
    eq(len(t.rows), 3, "C2a 插自己租户的行成功")
    raises(lambda: t.insert(bob, {"id": 4, "manager": "carol"}), CheckViolation,
           "C2b 插别人的行报 WITH CHECK OPTION")

    # C3 UPDATE：先用 USING 选行，再用 WITH CHECK 校验新行。
    #    注意是**整句报错**而不是「静默少改一行」（官方示例 ERROR: new row violates
    #    WITH CHECK OPTION for "passwd"）。
    raises(lambda: t.update(bob, lambda r: r["id"] == 3, {"manager": "dave"}),
           CheckViolation, "C3a 把行改成别人的 manager 会让整句报错")
    eq(t.rows[2]["manager"], "bob", "C3b 报错后原行未被改动")

    # C4 DELETE 只能删看得见的行，删不到不报错
    eq(t.delete(bob, lambda r: r["id"] == 1), 0, "C4a 看不见的行删不掉（不报错，0 行）")
    eq(t.delete(bob, lambda r: r["id"] == 3), 1, "C4b 自己的行能删")


# ------------------------------------------------- D 命令与角色的适用性
def t_scope():
    t = accounts()
    t.add_policy(Policy("p_sel", cmd="SELECT", using=lambda r, u: r["manager"] == u.name))
    bob = Role("bob")
    eq([r["id"] for r in t.visible_rows(bob, "SELECT")], [2], "D1 FOR SELECT 管 SELECT")
    eq(len(t.visible_rows(bob, "DELETE")), 0, "D2 同一条策略对 DELETE 不生效 → default-deny")

    # ALL 覆盖所有命令
    t2 = accounts()
    t2.add_policy(Policy("p_all", cmd="ALL", using=lambda r, u: r["manager"] == u.name))
    eq(len(t2.visible_rows(bob, "DELETE")), 1, "D3 ALL 覆盖所有命令")

    # TO 指定角色
    t3 = accounts()
    t3.add_policy(Policy("p_admin", cmd="ALL", roles=["admin"],
                         using=lambda r, u: True))
    eq(len(t3.visible_rows(bob)), 0, "D4 角色不匹配时策略不生效 → default-deny")
    eq(len(t3.visible_rows(Role("admin"))), 2, "D5 TO admin 的角色能看到全部")
    eq(len(t3.visible_rows(Role("op", members=["admin"]))), 2,
       "D6 角色继承算数（成员关系生效）")


# ------------------------------------------ E permissive OR / restrictive AND
def t_combine():
    # E1 多条 permissive 之间取 OR
    t = accounts()
    t.add_policy(Policy("p1", using=lambda r, u: r["id"] == 1))
    t.add_policy(Policy("p2", using=lambda r, u: r["id"] == 2))
    eq(len(t.visible_rows(Role("bob"))), 2, "E1 permissive 之间取 OR（两条各自放行一行）")

    # E2 restrictive 与 permissive 取 AND：管理员还必须走本地 socket
    t2 = Table("passwd", owner="postgres")
    for name, local in (("root", True), ("daemon", False)):
        t2.rows.append({"user_name": name, "local": local})
    t2.enable()
    t2.add_policy(Policy("admin_all", roles=["admin"], using=lambda r, u: True))
    # 官方例子：restrictive 策略判的是「连接是否来自本地」，而不是行上的某个列
    t2.add_policy(Policy("admin_local_only", roles=["admin"], restrictive=True,
                         using=lambda r, u: u.local))
    admin_local = Role("admin")
    admin_local.local = True
    admin_remote = Role("admin")
    admin_remote.local = False
    eq([r["user_name"] for r in t2.visible_rows(admin_local)], ["root", "daemon"],
       "E2a 本地管理员看到全部（permissive 放行 + restrictive 通过）")
    eq(len(t2.visible_rows(admin_remote)), 0, "E2b 网络管理员一行都看不到")

    # E3 只有 restrictive 时，基础是「全部可见」再被收紧
    t3 = accounts()
    t3.add_policy(Policy("only_a", restrictive=True, using=lambda r, u: r["tenant"] == "a"))
    eq([r["id"] for r in t3.visible_rows(Role("bob"))], [1], "E3 只有 restrictive 时先全放行再收紧")


# ------------------------------------- F 参照完整性与整表操作绕过 RLS
def t_integrity():
    t = accounts()
    t.add_policy(mgr_policy())
    bob = Role("bob")
    # F1 引用完整性检查（唯一/主键/外键）总是绕过 RLS
    ok(t.referential_check({"id": 1}, "id") is True,
       "F1 参照完整性检查绕过 RLS（否则会形成隐蔽信道之外的完整性问题）")
    # F2 正因为绕过，唯一约束会泄露「某值已存在」—— 官方点名的 covert channel
    ok(t.referential_check({"id": 1}, "id") and len(t.visible_rows(bob)) == 1,
       "F2 这也意味着唯一冲突会泄露存在性（官方提醒的 covert channel）")


# ---------------------------------------------------------- G 多租户模板
def t_tenant():
    t = Table("orders", owner="app")
    for i, tenant in enumerate(["t1", "t1", "t2"]):
        t.rows.append({"id": i + 1, "tenant_id": tenant})
    t.enable(force=True)                       # 应用账号即属主，必须 FORCE
    t.add_policy(Policy("tenant_iso", cmd="ALL", roles=["app_user"],
                        using=tenant_isolation()))
    u1 = Role("app_user")
    u1.tenant = "t1"
    u2 = Role("app_user")
    u2.tenant = "t2"
    eq([r["id"] for r in t.visible_rows(u1)], [1, 2], "G1 租户隔离：t1 只看到自己的两行")
    eq([r["id"] for r in t.visible_rows(u2)], [3], "G2 t2 只看到自己的一行")
    raises(lambda: t.insert(u1, {"id": 4, "tenant_id": "t2"}), CheckViolation,
           "G3 跨租户写入被 WITH CHECK 拦下")
    t.insert(u1, {"id": 5, "tenant_id": "t1"})
    eq(len(t.rows), 4, "G4 同租户写入正常")

    # G5 应用账号是属主且未 FORCE 时会完全绕过 —— 多租户最常见的漏洞
    t5 = Table("orders", owner="app")
    t5.rows.append({"id": 1, "tenant_id": "t1"})
    t5.enable(force=False)
    t5.add_policy(Policy("iso", using=tenant_isolation()))
    app = Role("app")
    app.tenant = "t9"
    eq(len(t5.visible_rows(app)), 1, "G5 属主未 FORCE 时绕过 RLS（必须显式 FORCE）")


def main():
    t_default_deny(); t_bypass(); t_using_check(); t_scope()
    t_combine(); t_integrity(); t_tenant()
    print("PASS=%d FAIL=%d" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

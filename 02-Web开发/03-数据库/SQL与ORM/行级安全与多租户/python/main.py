"""行级安全（RLS）与多租户 —— PostgreSQL 18 官方文档 + 官方源码的可执行模型。

转写对象：
  * PostgreSQL 18《5.9. Row Security Policies》—— USING / WITH CHECK、
    permissive 与 restrictive 的组合方式、default-deny、owner 与 BYPASSRLS 的豁免
  * PostgreSQL 源码 `src/backend/rewrite/rowsecurity.c` —— add_security_quals 里
    「没有策略就等于禁止一切访问」、permissive 用 OR_EXPR、restrictive 用 AND

口径说明：策略表达式用 Python 谓词表示（真实环境下是 SQL 表达式，由
`add_security_quals` 注入到查询计划里），组合方式完全照官方。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence


class CheckViolation(Exception):
    """new row violates WITH CHECK OPTION。"""


Expr = Callable[[Dict[str, Any], "Role"], bool]


class Role:
    def __init__(self, name: str, superuser: bool = False,
                 bypassrls: bool = False, members: Sequence[str] = ()) -> None:
        self.name = name
        self.superuser = superuser
        self.bypassrls = bypassrls
        self.members = set(members)      # 该角色所属的角色（含继承）

    def is_member_of(self, role: str) -> bool:
        return self.name == role or role in self.members


class Policy:
    """一条行级安全策略。

    using      : 作用于「已存在的行」（SELECT / UPDATE / DELETE 的可见性）
    with_check : 作用于「被写出的新行」（INSERT / UPDATE）
    官方：只写 USING 时，WITH CHECK 隐含与 USING 相同。
    """

    def __init__(self, name: str, cmd: str = "ALL", roles: Optional[Sequence[str]] = None,
                 using: Optional[Expr] = None, with_check: Optional[Expr] = None,
                 restrictive: bool = False) -> None:
        if cmd not in ("ALL", "SELECT", "INSERT", "UPDATE", "DELETE"):
            raise ValueError("bad command: %s" % cmd)
        self.name = name
        self.cmd = cmd
        self.roles = list(roles) if roles else None     # None = PUBLIC
        self.using = using
        self.with_check = with_check or using           # 隐含规则
        self.restrictive = restrictive

    def applies_to(self, role: Role, cmd: str) -> bool:
        if self.cmd != "ALL" and self.cmd != cmd:
            return False
        if self.roles is None:
            return True
        return any(role.is_member_of(r) for r in self.roles)


class Table:
    def __init__(self, name: str, owner: str) -> None:
        self.name = name
        self.owner = owner
        self.rows: List[Dict[str, Any]] = []
        self.policies: List[Policy] = []
        self.rls_enabled = False
        self.rls_forced = False          # ALTER TABLE ... FORCE ROW LEVEL SECURITY

    def enable(self, force: bool = False) -> None:
        self.rls_enabled = True
        self.rls_forced = force

    def add_policy(self, p: Policy) -> None:
        self.policies.append(p)

    # ------------------------------------------------------ 豁免判定
    def bypasses(self, role: Role) -> bool:
        """官方：超级用户与 BYPASSRLS 角色永远绕过；表属主默认也绕过，
        除非表被 ALTER TABLE ... FORCE ROW LEVEL SECURITY。"""
        if role.superuser or role.bypassrls:
            return True
        if role.is_member_of(self.owner) and not self.rls_forced:
            return True
        return False

    def _applicable(self, role: Role, cmd: str) -> List[Policy]:
        if not self.rls_enabled or self.bypasses(role):
            return []
        return [p for p in self.policies if p.applies_to(role, cmd)]

    # --------------------------------------------- 可见性（USING，OR/AND）
    def visible_rows(self, role: Role, cmd: str = "SELECT") -> List[Dict[str, Any]]:
        """返回该角色在给定命令下能看到的行。

        官方 add_security_quals：permissive 用 OR_EXPR 串起来，restrictive 再 AND 上去；
        **一张策略都没有时使用隐含的 default-deny**（所有行都看不见）。
        """
        policies = self._applicable(role, cmd)
        if not self.rls_enabled or self.bypasses(role):
            return list(self.rows)
        if not policies:
            return []                       # default-deny

        permissive = [p for p in policies if not p.restrictive]
        restrictive = [p for p in policies if p.restrictive]

        out = []
        for row in self.rows:
            if permissive:
                # OR：任意一条 permissive 通过即可
                visible = any(_using(p, row, role) for p in permissive)
            else:
                # 只有 restrictive 时，基础是「全部可见」，再被 AND 收紧
                visible = True
            if visible:
                visible = all(_using(p, row, role) for p in restrictive)
            if visible:
                out.append(row)
        return out

    # --------------------------------------------- 写入校验（WITH CHECK）
    def check_insert(self, role: Role, row: Dict[str, Any]) -> bool:
        """INSERT 只看 WITH CHECK（官方：没有 USING 这一侧）。

        无可用策略时同样是 default-deny —— 一行都插不进去。
        """
        policies = self._applicable(role, "INSERT")
        if not self.rls_enabled or self.bypasses(role):
            return True
        if not policies:
            raise CheckViolation("new row violates WITH CHECK OPTION (default-deny)")
        if not all(_with_check(p, row, role) for p in policies if p.restrictive):
            raise CheckViolation("new row violates WITH CHECK OPTION for %s" % self.name)
        if not any(_with_check(p, row, role) for p in policies if not p.restrictive):
            raise CheckViolation("new row violates WITH CHECK OPTION for %s" % self.name)
        return True

    def insert(self, role: Role, row: Dict[str, Any]) -> None:
        self.check_insert(role, row)
        self.rows.append(dict(row))

    def update(self, role: Role, match: Callable[[Dict[str, Any]], bool],
               changes: Dict[str, Any]) -> int:
        """UPDATE = 先用 USING 选行，再用 WITH CHECK 校验新行。"""
        n = 0
        for row in self.visible_rows(role, "UPDATE"):
            if not match(row):
                continue
            new_row = dict(row)
            new_row.update(changes)
            if not self._check_write(role, new_row):
                raise CheckViolation("new row violates WITH CHECK OPTION for %s" % self.name)
            row.update(changes)
            n += 1
        return n

    def delete(self, role: Role, match: Callable[[Dict[str, Any]], bool]) -> int:
        n = 0
        for row in list(self.visible_rows(role, "DELETE")):
            if match(row):
                self.rows.remove(row)
                n += 1
        return n

    def _check_write(self, role: Role, row: Dict[str, Any]) -> bool:
        policies = self._applicable(role, "UPDATE")
        if not self.rls_enabled or self.bypasses(role):
            return True
        if not policies:
            return False
        if not all(_with_check(p, row, role) for p in policies if p.restrictive):
            return False
        return any(_with_check(p, row, role) for p in policies if not p.restrictive)

    # 官方：参照完整性检查（唯一/主键/外键）**总是绕过** RLS
    def referential_check(self, row: Dict[str, Any], col: str) -> bool:
        return any(r.get(col) == row.get(col) for r in self.rows)


def _using(p: Policy, row: Dict[str, Any], role: Role) -> bool:
    if p.using is None:
        return True
    return p.using(row, role)


def _with_check(p: Policy, row: Dict[str, Any], role: Role) -> bool:
    if p.with_check is None:
        return True
    return p.with_check(row, role)


# ------------------------------------------------------- 便捷策略构造器
def tenant_isolation(col: str = "tenant_id") -> Expr:
    """多租户最常见的形态：只能看自己租户的行。"""
    def _f(row: Dict[str, Any], role: Role) -> bool:
        return row.get(col) == getattr(role, "tenant", role.name)
    return _f

"""授权/访问控制引擎(OWASP Authorization Cheat Sheet 的可执行版)。

三条基线:
  ① 默认拒绝 —— 没有显式匹配的规则时给出"拒绝"这个明确决定,而不是"中立";
  ② 每个请求都在服务端校验 —— 客户端传来的字段只作提示,一律不可信;
  ③ 对象级校验 —— "对某类资源有权限"≠"对该类每个对象都有权限"(CWE-639 / IDOR)。

同时实现 RBAC、ABAC、ReBAC 三种模型并让它们对同一批用例投票,以便看清 RBAC 在哪一步失守。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Subject:
    uid: str
    roles: frozenset = frozenset()
    attrs: dict = field(default_factory=dict)          # 仅容纳**服务端可信**属性
    untrusted: dict = field(default_factory=dict)      # 客户端送来的"自称属性",决策时忽略


@dataclass(frozen=True)
class Resource:
    kind: str                # invoice / document / report ...
    rid: str
    owner: str | None = None
    tenant: str | None = None
    public: bool = False
    sensitive: bool = False


@dataclass(frozen=True)
class Decision:
    allow: bool
    reason: str
    rule: str = "-"
    status: int = 200

    def __bool__(self):
        return self.allow


class Audit:
    def __init__(self):
        self.rows: list[tuple] = []

    def log(self, subj, action, res, dec):
        self.rows.append((subj.uid, action, f"{res.kind}:{res.rid}", dec.allow, dec.rule, dec.reason))


class AccessDenied(Exception):
    """对外只暴露通用信息,不泄露策略细节(CWE-209 的对手面)。"""

    def __init__(self, decision: Decision):
        super().__init__("403 forbidden")
        self.decision = decision


class Engine:
    def __init__(self):
        self.audit = Audit()
        self.rbac: dict[str, set[str]] = {}             # 角色 → 权限(类型级,无对象概念)
        self.rules: list[tuple] = []                    # ABAC 规则
        self.relations: set[tuple] = set()              # ReBAC 关系元组
        self.handles: dict[str, dict[str, str]] = {}    # 会话级间接引用
        self._n = 0

    # ---------------- 配置 ----------------
    def add_role(self, role: str, perms):
        self.rbac[role] = set(perms)

    def add_rule(self, name, effect, actions, predicate):
        """effect: 'allow'/'deny';predicate(subject, resource) -> bool"""
        self.rules.append((name, effect, frozenset(actions), predicate))

    def relate(self, subject_uid, relation, obj):
        self.relations.add((subject_uid, relation, obj))

    def unrelated(self, obj):                            # 级联撤销:对象消失时清理关系
        self.relations = {r for r in self.relations if r[2] != obj}
        self.handles = {k: v for k, v in self.handles.items() if obj not in v.values()}

    # ---------------- 间接引用(OWASP 建议的 user/session 级 handle)----------------
    def handle_for(self, sess_uid: str, real_id: str) -> str:
        table = self.handles.setdefault(sess_uid, {})
        for h, real in table.items():
            if real == real_id:
                return h
        self._n += 1
        h = f"h{self._n}"
        table[h] = real_id
        return h

    def resolve(self, sess_uid: str, handle: str) -> str | None:
        return self.handles.get(sess_uid, {}).get(handle)     # 别人的句柄解析不到

    # ---------------- RBAC:只认"角色有没有这个权限" ----------------
    def rbac_decide(self, subj: Subject, action: str, res: Resource) -> Decision:
        for role in sorted(subj.roles):
            if action in self.rbac.get(role, ()):
                return Decision(True, f"role {role} has {action}", f"rbac:{role}")
        return Decision(False, f"no role grants {action}")

    # ---------------- ABAC + ReBAC:先过规则,再做对象级校验 ----------------
    def decide(self, subj: Subject, action: str, res: Resource) -> Decision:
        try:
            dec = self._decide(subj, action, res)
        except Exception as exc:                       # 失败必须"安全退出",不能放行
            dec = Decision(False, f"policy error → deny ({type(exc).__name__})")
        self.audit.log(subj, action, res, dec)
        return dec

    def _decide(self, subj: Subject, action: str, res: Resource) -> Decision:
        for effect in ("deny", "allow"):        # 显式拒绝优先(deny-overrides)
            for name, eff, actions, pred in self.rules:
                if eff != effect or action not in actions:
                    continue
                if pred(subj, res):
                    if effect == "deny":
                        return Decision(False, f"{name} matched", name)
                    return Decision(True, f"{name} matched", name)
        # 默认拒绝:没有任何规则命中时的显式决定
        return Decision(False, "deny by default", "default-deny")

    # ---------------- 对象级校验(每个对象都要查)----------------
    def can_touch_object(self, subj: Subject, action: str, res: Resource) -> Decision:
        if res.public and action.endswith(":read"):
            return Decision(True, "public resource", "public:read")
        if res.tenant is not None and subj.attrs.get("tenant") != res.tenant:
            return Decision(False, "cross-tenant access", "tenant-isolation")
        # 宽范围访问也必须**显式**授予(如租户级审计员),不能靠角色名隐含
        if (subj.uid, "auditor", f"tenant:{res.tenant}") in self.relations:
            return Decision(True, "tenant auditor", "rebac:auditor")
        if res.owner == subj.uid:
            return Decision(True, "owner", "rebac:owner")
        if (subj.uid, "collaborator", f"{res.kind}:{res.rid}") in self.relations:
            return Decision(True, "collaborator", "rebac:collaborator")
        return Decision(False, "no relation to this object", "object-level")

    def authorize(self, subj: Subject, action: str, res: Resource) -> Decision:
        """完整链路:类型级规则 → 对象级关系 → 审计。"""
        first = self.decide(subj, action, res)
        if not first.allow:
            return first
        second = self.can_touch_object(subj, action, res)
        self.audit.log(subj, action, res, second)
        return second

    # ---------------- 失败处理集中化 ----------------
    def guard(self, subj: Subject, action: str, res: Resource) -> str:
        dec = self.authorize(subj, action, res)
        if not dec.allow:
            raise AccessDenied(dec)          # 调用方只看到 403,细节留在审计里
        return "200 ok"


def build_reference_engine() -> Engine:
    """一个"看起来正确"的配置:RBAC 给类型级权限,ABAC 规则表达环境条件,ReBAC 表达所有权。"""
    e = Engine()
    e.add_role("accountant", {"invoice:read", "invoice:write", "report:read"})
    e.add_role("admin", {"invoice:read", "invoice:write", "invoice:delete",
                         "report:read", "user:manage", "static:read"})
    e.add_role("viewer", {"report:read"})
    # ABAC:凭"角色 + 环境属性"给类型级许可(官方示例:销售代表不能午夜从家里访问客户库)
    e.add_rule("on-shift-invoice", "allow", {"invoice:read", "invoice:write"},
               lambda s, r: "accountant" in s.roles and s.attrs.get("on_shift") is True)
    e.add_rule("admin-everything", "allow",
               {"invoice:read", "invoice:write", "invoice:delete", "user:manage",
                "static:read", "report:read"},
               lambda s, r: "admin" in s.roles)
    e.add_rule("viewer-report", "allow", {"report:read"}, lambda s, r: "viewer" in s.roles)
    # 显式拒绝优先:敏感资源除 admin 外一律拒绝(即使上面已 allow)
    e.add_rule("sensitive-needs-admin", "deny", {"invoice:read", "invoice:write"},
               lambda s, r: r.sensitive and "admin" not in s.roles)
    return e

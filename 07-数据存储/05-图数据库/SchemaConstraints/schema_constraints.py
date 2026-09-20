"""Neo4j 约束（constraints）最小模型。

依据 Neo4j Cypher Manual（实读）：
  * 四类约束：
      - **Property uniqueness**：`REQUIRE n.prop IS [NODE] UNIQUE`
      - **Property existence**（Enterprise Edition）：`REQUIRE n.prop IS NOT NULL`
      - **Property type**（Enterprise Edition）：`REQUIRE n.prop IS :: <TYPE>`
      - **Key**（Enterprise Edition）：`REQUIRE n.prop IS [NODE] KEY`
    节点用 `FOR (n:Label)`，关系用 `FOR ()-[r:REL_TYPE]-()`；均支持复合
    （`(n.p1, …, n.pn)`）。
  * **Key 约束 = 存在性 + 唯一性**：官方原文 "Key constraints ensure that the
    property exist and the property value is unique … For composite key
    constraints on multiple properties, all properties must exists and the
    combination of property values must be unique."
  * 属性类型约束支持 `LIST<STRING NOT NULL>` 这样的**带非空标记的元素类型**，
    以及 `STRING | LIST<STRING NOT NULL>` 这样的**联合类型**，还有
    `VECTOR<INT32>(42)` 这种带维度的向量类型。
  * 约束名**建议显式给出**，且在**索引与约束之间**必须唯一；可用参数化名字。
  * `IF NOT EXISTS`：若**同名**约束、或**同类型同模式**的约束已存在，则不报错、
    不创建，只回一条 informational 通知。
  * 用旧 `CREATE CONSTRAINT` 语法创建的约束会被**自动加入**数据库的 graph type。
"""

UNIQUENESS = "UNIQUENESS"
EXISTENCE = "EXISTENCE"
TYPE = "TYPE"
KEY = "KEY"

NODE = "NODE"
REL = "RELATIONSHIP"


class ConstraintError(Exception):
    """建约束 / 写数据时的约束冲突。"""


class Constraint:
    def __init__(self, name, kind, target, entity, props, type_spec=None):
        self.name = name
        self.kind = kind
        self.target = target
        self.entity = entity
        self.props = tuple(props)
        self.type_spec = type_spec      # 仅 TYPE 约束使用

    def schema_key(self):
        return (self.kind, self.target, self.entity, self.props)

    def __repr__(self):
        return "Constraint(%s, %s:%s %s)" % (self.kind, self.target,
                                             self.entity, self.props)


class Entity:
    """一个节点或关系：标签/类型 + 属性字典。"""

    def __init__(self, eid, target, entity, **props):
        self.eid = eid
        self.target = target
        self.entity = entity
        self.props = props

    def matches(self, target, entity):
        return self.target == target and self.entity == entity

    def value(self, prop):
        return self.props.get(prop, None)


class Database:
    def __init__(self):
        self.constraints = []
        self.index_names = set()
        self.notifications = []
        self.entities = []

    # ---------------------------------------------------------- 建约束
    def create_constraint(self, name, kind, target, entity, props,
                          type_spec=None, if_not_exists=False):
        if kind == TYPE and type_spec is None:
            raise ConstraintError("type constraint 必须给出类型")
        if kind in (EXISTENCE,) and len(props) != 1:
            raise ConstraintError("existence constraint 只支持单属性")
        if kind in (UNIQUENESS, KEY) and not props:
            raise ConstraintError("至少需要指定一个属性")
        if name in self.index_names:
            raise ConstraintError("there is an index called %s" % name)
        if if_not_exists:
            for c in self.constraints:
                if c.name == name or c.schema_key() == (kind, target, entity,
                                                        tuple(props)):
                    self.notifications.append(
                        "`CREATE CONSTRAINT %s` has no effect. `%s` "
                        "already exists." % (name, c.name))
                    return c
        if any(c.name == name for c in self.constraints):
            raise ConstraintError("there is a constraint called %s" % name)
        for c in self.constraints:
            if c.schema_key() == (kind, target, entity, tuple(props)):
                raise ConstraintError("constraint already exists: %s" % c.name)
        con = Constraint(name, kind, target, entity, tuple(props), type_spec)
        self.constraints.append(con)
        return con

    def applicable(self, ent):
        return [c for c in self.constraints if ent.matches(c.target, c.entity)]

    # ---------------------------------------------------------- 校验
    def _check_exists(self, con, ent):
        for p in con.props:
            if p not in ent.props or ent.props[p] is None:
                raise ConstraintError(
                    "%s: 缺少属性 %s（或值为 null）" % (con.name, p))

    def _check_type(self, con, ent):
        """属性类型约束。

        口径：值缺失/null 时**通过**校验 —— 依据 Working with null 页
        「类型谓词表达式对 null 一律返回 true」。
        """
        for p in con.props:
            v = ent.value(p)
            if v is None:
                continue
            if not _value_matches_type(v, con.type_spec):
                raise ConstraintError(
                    "%s: %s 的值 %r 不满足类型 %s" % (con.name, p, v,
                                                       con.type_spec))

    def _check_unique(self, con, ent):
        """唯一性校验。

        口径：官方文档本页**未规定** null/缺失属性是否参与唯一性判定。
        本模型按「缺少任一指定属性的实体不参与唯一性校验」实现。
        """
        if any(p not in ent.props or ent.props[p] is None
               for p in con.props):
            return
        key = tuple(ent.props[p] for p in con.props)
        for other in self.entities:
            if other is ent or not other.matches(con.target, con.entity):
                continue
            if any(p not in other.props or other.props[p] is None
                   for p in con.props):
                continue
            if tuple(other.props[p] for p in con.props) == key:
                raise ConstraintError(
                    "%s: %s 与已有实体 %s 冲突" % (con.name, key, other.eid))

    def add(self, ent):
        for con in self.applicable(ent):
            if con.kind == EXISTENCE:
                self._check_exists(con, ent)
            elif con.kind == TYPE:
                self._check_type(con, ent)
            elif con.kind == UNIQUENESS:
                self._check_unique(con, ent)
            elif con.kind == KEY:
                # Key = 存在性 + 唯一性
                self._check_exists(con, ent)
                self._check_unique(con, ent)
        self.entities.append(ent)
        return ent


_PRIMITIVES = {
    "STRING": str, "INTEGER": int, "FLOAT": float, "BOOLEAN": bool,
    "MAP": dict, "DATE": str, "DURATION": str, "POINT": tuple,
}


def _value_matches_type(v, spec):
    """类型判定：支持 `T`、`LIST<T NOT NULL>`、`A | B` 三种形式。"""
    for alt in spec.split("|"):
        if _one_matches(v, alt.strip()):
            return True
    return False


def _one_matches(v, spec):
    if spec.startswith("LIST<") and spec.endswith(">"):
        inner = spec[5:-1].strip()
        not_null = inner.endswith(" NOT NULL")
        if not_null:
            inner = inner[: -len(" NOT NULL")].strip()
        if not isinstance(v, list):
            return False
        if not v and not_null:
            return True          # 空列表里没有 null，满足 NOT NULL
        return all(_one_matches(x, inner) for x in v)
    if spec.startswith("VECTOR<"):
        return isinstance(v, (list, tuple))
    py = _PRIMITIVES.get(spec)
    if py is None:
        return False
    if py is int and isinstance(v, bool):
        return False             # BOOLEAN 不是 INTEGER
    return isinstance(v, py)

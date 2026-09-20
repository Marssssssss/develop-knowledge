"""Neo4j 搜索性能索引（search-performance indexes）最小模型。

依据 Neo4j Cypher Manual（实读）：
  * 共有**四种**搜索性能索引：Range（默认）、Text、Point、Token lookup。
  * Token lookup 索引**只解决节点标签与关系类型谓词**，不能解决任何属性谓词；
    数据库创建时就**自带两个**（一个管节点标签、一个管关系类型）。
  * Range 支持的谓词：等值、列表成员（IN）、存在性（IS NOT NULL）、范围查找、
    前缀（STARTS WITH）。Range **没有**可配置项。
  * Text 支持的谓词：等值、列表成员、STARTS WITH、ENDS WITH、CONTAINS。
    采用 **trigram 索引**：字符串被切成重叠的三字符组（每个三字符组含 3 个
    Unicode 码点），例如 "developer" → ["dev","eve","vel","elo","lop","ope","per"]。
    Text 只做**精确**匹配；近似匹配与相似度打分要用全文索引。
  * Point 只解决 POINT 值谓词：等值 `point({x,y})`、`point.withinBBox(...)`、
    `point.distance(...) <= d`。Point **有**可配置项（spatial.cartesian.min/max
    默认 ±1000000.0 等）。
  * 建索引时**不指定类型则得到 Range 索引**。
  * 索引名在**索引与约束之间**必须唯一；不显式命名则自动生成。
  * `CREATE INDEX` 默认**非幂等**（重复创建同名同模式同类型会报错）；加上
    `IF NOT EXISTS` 则不报错、不创建，只返回一条 informational 通知
    （若存在冲突的约束仍可能报错）。
  * 复合索引：只有**带指定标签且同时含有全部指定属性**的节点才会被收录。
  * 多个索引可用时，**Cypher planner 会挑最能高效解决该谓词的那个**；也可以
    用 `USING` 关键字显式指定。
"""

RANGE = "RANGE"
TEXT = "TEXT"
POINT = "POINT"
TOKEN = "TOKEN_LOOKUP"

NODE = "NODE"
REL = "RELATIONSHIP"

# 每类索引可解决的谓词（官方文档的 Supported predicates 表）
RANGE_PREDS = {"eq", "in", "exists", "range", "starts_with"}
TEXT_PREDS = {"eq", "in", "starts_with", "ends_with", "contains"}
POINT_PREDS = {"point_eq", "within_bbox", "distance"}
TOKEN_PREDS = {"label", "rel_type"}

PREDS_BY_TYPE = {RANGE: RANGE_PREDS, TEXT: TEXT_PREDS,
                 POINT: POINT_PREDS, TOKEN: TOKEN_PREDS}

# planner 的同分裁决口径：官方只说"挑最能高效解决该谓词的索引"，
# 未公布优先级表。这里按「谓词只能被某类索引解决时无歧义；多类都能解决时
# 优先 Range（默认且最通用）」实现，并在 README 标注为口径。
PREFERENCE = {
    "eq": [RANGE, TEXT, POINT],
    "in": [RANGE, TEXT],
    "exists": [RANGE],
    "range": [RANGE],
    "starts_with": [RANGE, TEXT],
    "ends_with": [TEXT],
    "contains": [TEXT],
    "point_eq": [POINT],
    "within_bbox": [POINT],
    "distance": [POINT],
    "label": [TOKEN],
    "rel_type": [TOKEN],
}


class SchemaError(Exception):
    """建索引 / 建约束失败（重名、重复创建等）。"""


class Index:
    def __init__(self, name, target, entity, props=(), itype=RANGE,
                 generated_name=False):
        self.name = name
        self.target = target          # NODE / RELATIONSHIP
        self.entity = entity          # 标签名 / 关系类型名
        self.props = tuple(props)
        self.itype = itype
        self.generated_name = generated_name

    def key(self):
        return (self.target, self.entity, self.props, self.itype)

    def __repr__(self):
        return "Index(%s, %s:%s ON %s)" % (self.itype, self.target,
                                           self.entity, ",".join(self.props))


class Schema:
    """索引 + 约束共享的命名空间。"""

    def __init__(self):
        self.indexes = []
        self.constraints = []          # 只存名字，用于验证名字唯一性
        self.notifications = []

    def name_taken(self, name):
        return (any(i.name == name for i in self.indexes)
                or name in self.constraints)

    def default_token_indexes(self):
        """数据库创建时自带的两个 token lookup 索引。"""
        self.indexes.append(Index("__token_labels__", NODE, "*", (), TOKEN,
                                  generated_name=True))
        self.indexes.append(Index("__token_reltypes__", REL, "*", (), TOKEN,
                                  generated_name=True))

    def create_index(self, name, target, entity, props=(), itype=None,
                     if_not_exists=False):
        # 不指定类型 → Range
        if itype is None:
            itype = RANGE
        if itype == TOKEN and props:
            raise SchemaError("token lookup 索引不能带属性")
        if target == NODE and itype != TOKEN and not props:
            raise SchemaError("属性索引至少要指定一个属性")
        # 与**约束**的冲突即使加了 IF NOT EXISTS 也会报错（官方：
        # "It may still throw an error if conflicting constraints exist,
        #  such as constraints with the same name or schema and backing
        #  index type."）
        if name in self.constraints:
            raise SchemaError("there is a constraint called %s" % name)
        key = (target, entity, tuple(props), itype)
        if if_not_exists:
            for i in self.indexes:
                if i.name == name or i.key() == key:
                    self.notifications.append(
                        "`CREATE INDEX %s` has no effect. `%s` already exists."
                        % (name, i.name))
                    return i
        if any(i.name == name for i in self.indexes):
            raise SchemaError("there already exists an index called %s" % name)
        for i in self.indexes:
            if i.key() == key:
                # 同名同模式同类型：默认行为是报错（CREATE INDEX 非幂等）
                raise SchemaError("index already exists with same schema and "
                                  "index type: %s" % i.name)
        idx = Index(name, target, entity, tuple(props), itype)
        self.indexes.append(idx)
        return idx


def can_solve(index, pred, prop=None):
    """该索引能否解决这个谓词。"""
    if index.itype == TOKEN:
        return pred in TOKEN_PREDS
    if pred not in PREDS_BY_TYPE[index.itype]:
        return False
    if pred in ("label", "rel_type"):
        return False
    # 属性索引必须覆盖该属性
    return prop is None or prop in index.props


def candidates(indexes, pred, prop=None, target=None, entity=None):
    out = []
    for i in indexes:
        if target is not None and i.target != target and i.itype != TOKEN:
            continue
        if entity is not None and i.entity != entity and i.itype != TOKEN:
            continue
        if target == REL and i.itype == TOKEN:
            continue
        if can_solve(i, pred, prop):
            out.append(i)
    return out


def planner(indexes, pred, prop=None, target=None, entity=None):
    """planner 选索引：按谓词类型偏好挑最合适的一个。"""
    cands = candidates(indexes, pred, prop, target, entity)
    if not cands:
        return None
    for t in PREFERENCE.get(pred, []):
        for i in cands:
            if i.itype == t:
                return i
    return cands[0]


def plan_with_hint(indexes, hint_name, pred, prop=None):
    """USING INDEX 提示：强制使用指定索引（不再由 planner 裁决）。"""
    for i in indexes:
        if i.name == hint_name:
            return i
    raise SchemaError("no such index: %s" % hint_name)


def trigrams(s):
    """Text 索引的 trigram 切分：连续 3 个 Unicode 码点为一个组。

    官方例子："developer" → ["dev","eve","vel","elo","lop","ope","per"]
    """
    return [s[i:i + 3] for i in range(len(s) - 2)]


def text_matches(value, pattern):
    """模拟用 trigram 索引回答 CONTAINS：查询串的所有 trigram 都要能命中。

    口径：长度不足 3 的查询串切不出 trigram，官方未规定其走法，本模型按
    「退化为普通子串判定」实现并在 README 标注。
    """
    tg = trigrams(pattern)
    if not tg:
        return pattern in value
    return all(t in trigrams(value) for t in tg)


def indexed_members(node_props, required_props, label, wanted_label):
    """复合索引的收录条件：标签匹配 **且** 全部指定属性都存在。

    官方原文：only nodes with the specified label and that contain all the
    specified properties are added to the index.
    """
    if label != wanted_label:
        return False
    return all(p in node_props for p in required_props)

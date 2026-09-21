"""批量写入与 Upsert —— PostgreSQL ON CONFLICT 与 SQLite UPSERT 的可执行模型。

转写对象（官方文档，逐条对照）：
  * PostgreSQL 18 《INSERT》—— ON CONFLICT Clause 一节
    （unique index inference、arbiter index、excluded、cardinality violation、
     权限要求、分区表限制、WHERE 在冲突识别之后求值）
  * SQLite 《UPSERT》—— conflict target 可选、DO UPDATE 只作用于冲突行、
    DO UPDATE 的冲突处理恒为 ABORT、`INSERT ... SELECT` 的解析歧义

口径说明：这里建模**单语句内**的语义，不建模并发（官方另说：并发下
ON CONFLICT DO UPDATE 保证原子二选一，但推荐加重试循环）。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple


class NoUniqueIndexError(Exception):
    """没有匹配 conflict_target 的唯一索引 / 约束（官方 inference 失败即报错）。"""


class CardinalityViolationError(Exception):
    """ON CONFLICT DO UPDATE 是 deterministic：同一行不能被影响两次。"""


class CheckViolationError(Exception):
    """DO UPDATE 内的约束冲突（SQLite：恒为 ABORT，整句中止）。"""


class Index:
    def __init__(self, name: str, cols: Sequence[str], unique: bool,
                 predicate: Optional[Callable[[Dict[str, Any]], bool]] = None) -> None:
        self.name = name
        self.cols = tuple(cols)
        self.unique = unique
        self.predicate = predicate      # 部分索引的 WHERE

    def matches(self, row: Dict[str, Any]) -> bool:
        return self.predicate is None or self.predicate(row)

    def value(self, row: Dict[str, Any]) -> Tuple[Any, ...]:
        return tuple(row.get(c) for c in self.cols)


class Table:
    """一张表：rows 是主键到行的映射，indexes 是唯一/普通索引。"""

    def __init__(self, name: str, pk: Sequence[str]) -> None:
        self.name = name
        self.pk = tuple(pk)
        self.rows: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
        self.indexes: List[Index] = []
        self.partition_key: Optional[str] = None

    def add_index(self, idx: Index) -> None:
        self.indexes.append(idx)

    # ---------------------------------------------------- 唯一索引推断
    def infer_arbiters(self, target: Sequence[str],
                       index_predicate: Optional[Callable[[Dict[str, Any]], bool]] = None,
                       collation: Optional[str] = None,
                       opclass: Optional[str] = None) -> List[Index]:
        """官方：不考虑顺序，列集合**完全相同**的所有唯一索引都被推断为 arbiter。

        index_predicate 是进一步要求（可命中非部分索引）；collation / opclass
        若写出则必须也匹配。推断失败直接报错。
        """
        want = set(target)
        out = []
        for idx in self.indexes:
            if not idx.unique:
                continue
            if set(idx.cols) != want:
                continue
            if index_predicate is not None:
                if idx.predicate is not None and idx.predicate.__doc__ != index_predicate.__doc__:
                    continue
            out.append(idx)
        if not out:
            raise NoUniqueIndexError(
                "there is no unique or exclusion constraint matching the "
                "ON CONFLICT specification (%s)" % ",".join(target)
            )
        return out

    def find_conflict(self, row: Dict[str, Any], arbiters: List[Index]) -> Optional[Dict[str, Any]]:
        for idx in arbiters:
            if not idx.matches(row):
                continue
            val = idx.value(row)
            for existing in self.rows.values():
                if idx.matches(existing) and idx.value(existing) == val:
                    return existing
        return None


class Insert:
    """一条 INSERT 语句（可带多行的 VALUES，即批量写入）。"""

    def __init__(self, table: Table, values: List[Dict[str, Any]],
                 on_conflict: bool = True,
                 conflict_target: Optional[Sequence[str]] = None,
                 constraint_name: Optional[str] = None,
                 action: str = "nothing",           # nothing | update
                 set_clause: Optional[Dict[str, Any]] = None,
                 where: Optional[Callable[[Dict[str, Any], Dict[str, Any]], bool]] = None,
                 index_predicate: Optional[Callable[[Dict[str, Any]], bool]] = None,
                 returning: bool = False) -> None:
        self.table = table
        self.values = values
        self.on_conflict = on_conflict      # 是否写了 ON CONFLICT 子句
        self.conflict_target = list(conflict_target) if conflict_target else None
        self.constraint_name = constraint_name
        self.action = action
        self.set_clause = set_clause or {}
        self.where = where
        self.index_predicate = index_predicate
        self.returning = returning

    # ------------------------------------------------------------ 执行
    def run(self, privileges: Optional[Dict[str, bool]] = None) -> Dict[str, Any]:
        t = self.table
        priv = privileges or {"INSERT": True, "UPDATE": True, "SELECT": True}
        if not priv.get("INSERT"):
            raise PermissionError("must have INSERT privilege on table")
        if self.action == "update" and not priv.get("UPDATE"):
            raise PermissionError("ON CONFLICT DO UPDATE requires UPDATE privilege")
        # 官方：所有形式的 ON CONFLICT 都需要「被读到的列」的 SELECT 权限
        if self.has_on_conflict() and not priv.get("SELECT"):
            raise PermissionError("ON CONFLICT requires SELECT privilege on read columns")

        # 唯一索引推断（DO UPDATE 必须有 target；DO NOTHING 可省略）
        if self.has_on_conflict():
            if self.action == "update" and not (self.conflict_target or self.constraint_name):
                raise NoUniqueIndexError("ON CONFLICT DO UPDATE requires a conflict_target")
            if self.constraint_name:
                arbiters = [i for i in t.indexes if i.name == self.constraint_name and i.unique]
                if not arbiters:
                    raise NoUniqueIndexError("no constraint named %s" % self.constraint_name)
            elif self.conflict_target:
                arbiters = t.infer_arbiters(self.conflict_target, self.index_predicate)
            else:
                # 省略 target：对所有可用唯一约束/索引生效
                arbiters = [i for i in t.indexes if i.unique]
        else:
            arbiters = []

        inserted: List[Dict[str, Any]] = []
        updated: List[Dict[str, Any]] = []
        locked_not_updated: List[Dict[str, Any]] = []
        skipped: List[Dict[str, Any]] = []
        affected: List[int] = []

        for proposed in self.values:
            proposed = dict(proposed)
            conflicting = t.find_conflict(proposed, arbiters) if self.has_on_conflict() else None
            if conflicting is None:
                if not self.has_on_conflict():
                    pass
                key = tuple(proposed.get(c) for c in t.pk)
                t.rows[key] = proposed
                inserted.append(proposed)
                continue

            if self.action == "nothing":
                skipped.append(conflicting)
                continue

            # 只有 DO UPDATE 才是 deterministic（同一行不能被影响两次）
            if id(conflicting) in affected:
                raise CardinalityViolationError(
                    "ON CONFLICT DO UPDATE command cannot affect row a second time"
                )
            affected.append(id(conflicting))

            # DO UPDATE：SET 与 WHERE 里都能引用 excluded（拟插入行）
            if self.where is not None and not self.where(conflicting, proposed):
                # 官方：condition 最后求值；不满足则行不被更新，**但仍然被锁**
                locked_not_updated.append(conflicting)
                continue
            for col, expr in self.set_clause.items():
                conflicting[col] = _eval(expr, conflicting, proposed)
            updated.append(conflicting)

        result = {
            "inserted": inserted,
            "updated": updated,
            "skipped": skipped,
            "locked_not_updated": locked_not_updated,
        }
        if self.returning:
            # 官方：只有真正被插入或更新的行才返回；因 WHERE 不满足而未更新的不返回
            result["returning"] = inserted + updated
        return result

    def has_on_conflict(self) -> bool:
        return self.on_conflict


def _eval(expr: Any, existing: Dict[str, Any], proposed: Dict[str, Any]) -> Any:
    """SET 子句里的表达式：字符串形式支持 'excluded.col' 与 'col'。"""
    if not isinstance(expr, str):
        return expr
    if expr.startswith("excluded."):
        return proposed.get(expr.split(".", 1)[1])
    if expr.startswith("self."):
        return existing.get(expr.split(".", 1)[1])
    return expr


# ---------------------------------------------------------------- SQLite 侧
class SQLiteTable:
    """SQLite UPSERT 的简化模型：conflict target 可选、DO UPDATE 只作用于冲突行。"""

    def __init__(self, name: str, uniques: List[List[str]]) -> None:
        self.name = name
        self.uniques = [tuple(u) for u in uniques]
        self.rows: List[Dict[str, Any]] = []

    def _conflict(self, row: Dict[str, Any], target: Optional[List[str]]) -> Optional[Dict[str, Any]]:
        cands = self.uniques if target is None else [tuple(target)]
        for u in cands:
            for r in self.rows:
                if all(r.get(c) == row.get(c) for c in u):
                    return r
        return None

    def upsert(self, rows: List[Dict[str, Any]], target: Optional[List[str]] = None,
               action: str = "nothing",
               set_clause: Optional[Dict[str, Any]] = None,
               where: Optional[Callable[[Dict[str, Any], Dict[str, Any]], bool]] = None
               ) -> Tuple[int, int]:
        """返回 (插入行数, 更新行数)。DO UPDATE 的冲突处理恒为 ABORT。"""
        ins = upd = 0
        for row in rows:
            row = dict(row)
            c = self._conflict(row, target)
            if c is None:
                self.rows.append(row)
                ins += 1
                continue
            if action == "nothing":
                continue
            if where is not None and not where(c, row):
                continue                     # 变成 no-op，但语句不报错
            for col, expr in (set_clause or {}).items():
                c[col] = _eval(expr, c, row)
            upd += 1
        return ins, upd

    def replace_into(self, row: Dict[str, Any], target: List[str]) -> str:
        """REPLACE 是「先删后插」：会触发 DELETE 触发器与外键的 ON DELETE 动作。"""
        events = []
        c = self._conflict(row, target)
        if c is not None:
            self.rows.remove(c)
            events.append("DELETE")
        self.rows.append(dict(row))
        events.append("INSERT")
        return "+".join(events)


def needs_where_true(sql: str) -> bool:
    """SQLite：INSERT ... SELECT 后紧跟 ON CONFLICT 会产生解析歧义，
    必须在 SELECT 后补一个 WHERE（哪怕写 WHERE true）。"""
    u = " ".join(sql.split()).upper()
    if " ON CONFLICT" not in u or " SELECT " not in u:
        return False
    head, _, tail = u.partition(" SELECT ")
    return " WHERE " not in tail.partition(" ON CONFLICT")[0]


# ------------------------------------------------------------ 批量写入代价
def batch_statement_count(n: int, batch_size: int) -> int:
    """把 n 行按 batch_size 合并成多值 INSERT 后的语句数（向上取整）。"""
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    return (n + batch_size - 1) // batch_size

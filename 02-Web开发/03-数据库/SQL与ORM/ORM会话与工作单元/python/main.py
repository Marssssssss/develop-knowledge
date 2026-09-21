"""ORM 会话与工作单元 —— 身份映射 + 脏检查 + flush 顺序的可执行模型。

转写对象（官方源码，逐行对照）：
  * sqlalchemy/orm/identity.py        —— _WeakInstanceDict 的 add / get / replace / __contains__
  * sqlalchemy/orm/unitofwork.py      —— _per_mapper_flush_actions 的 (saves, deletes) 依赖
  * sqlalchemy/orm/persistence.py     —— _save_obj 的「每表先 UPDATE 后 INSERT」与
                                         _delete_obj 的 reversed(_sorted_tables)

口径说明：
  * 这里只建模 **单表继承链 + 外键拓扑** 下的 flush 顺序，不涉及 post_update、
    version_id row_switch、many-to-many 的 secondary 表单独排序等分支。
  * 「identity map 不是查询缓存」按官方 FAQ 原文实现：查询永不查身份映射。
"""

from __future__ import annotations

import weakref
from typing import Any, Dict, Iterable, List, Optional, Tuple

from identity import (IdentityMap, InstanceState, InvalidRequestError, _Sentinel,
                      _state)


from identity import (IdentityMap, InstanceState, InvalidRequestError, _Sentinel,
                      _state)





class Entity:
    """被映射的实体基类：__tablename__ 与主键列由子类声明。"""

    __tablename__: str = ""
    __pk__: Tuple[str, ...] = ("id",)
    __fks__: Dict[str, str] = {}  # 列名 -> 父表名

    def identity_key(self) -> Tuple[Any, ...]:
        return (type(self).__name__,) + tuple(getattr(self, c) for c in self.__pk__)


class Table:
    def __init__(self, name: str, parent: Optional[str] = None) -> None:
        self.name = name
        self.parent = parent


class Session:
    """工作单元。

    _new      : pending（官方 Session._new，强引用，add 后尚未 flush）
    identity  : persistent 对象的弱引用身份映射
    dirty_states / deleted_states : 待 UPDATE / 待 DELETE
    """

    def __init__(self, tables: Iterable[Table], autoflush: bool = True) -> None:
        self.tables: List[Table] = list(tables)
        self.identity = IdentityMap()
        self._new: List[InstanceState] = []
        self._deleted: List[InstanceState] = []
        self.autoflush = autoflush
        self.sql: List[str] = []          # 发出的语句序列（flush 计划的可观测产物）
        self._rows: Dict[str, Dict[Any, Any]] = {t.name: {} for t in self.tables}
        self.in_flush = False

    # ---------- 拓扑序：官方 mapper._sorted_tables（按外键依赖排序） ----------
    def sorted_tables(self) -> List[Table]:
        by_name = {t.name: t for t in self.tables}
        out: List[Table] = []
        for t in self.tables:
            chain = []
            cur: Optional[Table] = t
            while cur is not None:
                chain.append(cur)
                cur = by_name.get(cur.parent) if cur.parent else None
            out.extend(chain)
        seen, uniq = set(), []
        for t in out:
            if t.name not in seen:
                seen.add(t.name)
                uniq.append(t)
        # 父表在前：按「链长」升序即为拓扑序
        def depth(t: Table) -> int:
            d, cur = 0, t
            while cur is not None and cur.parent:
                d += 1
                cur = by_name.get(cur.parent)
            return d

        return sorted(uniq, key=depth)

    # ---------------------------- 对象生命周期 ----------------------------
    def add(self, obj: Entity) -> None:
        st = _state(obj)
        if st.session is self:
            return
        st.session = self
        self._new.append(st)

    def delete(self, obj: Entity) -> None:
        st = _state(obj)
        if not st.has_identity:
            return  # 官方：尚未持久化的对象被 delete 时不产生 DELETE
        st.deleted = True
        if st not in self._deleted:
            self._deleted.append(st)

    def get(self, cls: type, pk: Any) -> Optional[Any]:
        key = (cls.__name__, pk)
        st = self.identity.get(key)
        return st.obj if st is not None else None

    def query_all(self, table_name: str) -> List[Any]:
        """模拟 SQL 查询：官方 FAQ 明确——查询**不查**身份映射，直接打数据库。"""
        self._autoflush()
        return list(self._rows[table_name].values())

    def _autoflush(self) -> None:
        if self.autoflush and not self.in_flush and self._needs_flush():
            self.flush()

    def _needs_flush(self) -> bool:
        return bool(self._new) or bool(self._deleted) or any(
            s.is_dirty() for s in self.identity.live_states()
        )

    # ------------------------------- flush -------------------------------
    def flush(self) -> List[str]:
        """一次 flush 的语句序列。

        顺序完全照官方 persistence.py：
          1) 先保存（saves）—— 对每个表（拓扑序）**先 UPDATE 再 INSERT**；
          2) 再删除（deletes）—— 按 _sorted_tables 的**逆序**（先子表后父表）。
        依赖来自 unitofwork.py：`self.dependencies.add((saves, deletes))`。
        """
        self.in_flush = True
        try:
            self._emit_saves()
            self._emit_deletes()
            self._new = []
            self._deleted = []
        finally:
            self.in_flush = False
        return list(self.sql)

    def _emit_saves(self) -> None:
        ordered = self.sorted_tables()
        for table in ordered:
            # UPDATE：持久对象里脏的（含被 update 的）
            for st in self.identity.live_states():
                if st.obj.__class__.__tablename__ != table.name:
                    continue
                if st.deleted or not st.is_dirty():
                    continue
                changes = st.dirty_attrs()
                self.sql.append(
                    "UPDATE %s SET %s WHERE id=%s"
                    % (table.name, ",".join(sorted(changes)), st.key[1])
                )
                st.committed.update(st.attrs())
            # INSERT：pending 对象
            for st in list(self._new):
                if st.obj.__class__.__tablename__ != table.name:
                    continue
                st.key = st.obj.identity_key()
                self.identity.add(st)
                st.has_identity = True
                st.committed.update(st.attrs())
                self.sql.append(
                    "INSERT INTO %s (%s)" % (table.name, ",".join(sorted(st.attrs())))
                )
                self._rows[table.name][st.key[1]] = st.obj

    def _emit_deletes(self) -> None:
        for table in reversed(self.sorted_tables()):
            for st in self.identity.live_states():
                if not st.deleted:
                    continue
                if st.obj.__class__.__tablename__ != table.name:
                    continue
                self.sql.append("DELETE FROM %s WHERE id=%s" % (table.name, st.key[1]))

    def commit(self) -> List[str]:
        """官方：commit 内部无条件 flush。"""
        self.flush()
        for st in list(self.identity.live_states()):
            if st.deleted:
                st.deleted = False
                st.has_identity = False
                st.session = None
                self._dict_pop(st)
        return list(self.sql)

    def _dict_pop(self, st: InstanceState) -> None:
        if st.key is not None and st.key in self.identity._dict:
            del self.identity._dict[st.key]

    def rollback(self) -> None:
        for st in list(self.identity.live_states()):
            if st.deleted:
                st.deleted = False
            # 恢复到已提交快照
            for k, v in st.committed.items():
                setattr(st.obj, k, v)
        self._new = []
        self._deleted = []

    def expire_all(self) -> None:
        for st in self.identity.live_states():
            st.committed = {k: _Sentinel() for k in st.committed}



"""身份映射与对象状态 —— 从 main.py 拆出的独立模块。

口径与主 README 一致：转写 SQLAlchemy 2.0 官方 `orm/identity.py`。
"""

from __future__ import annotations

import weakref
from typing import Any, Dict, List, Optional, Tuple





class InvalidRequestError(Exception):
    """对应 sqlalchemy.exc.InvalidRequestError（身份键冲突）。"""


class InstanceState:
    """一个被 ORM 接管的对象的状态槽。

    committed：上一次 flush / load 时的**已提交快照**，脏检查的唯一依据。
    key：身份键，等于 (实体名, 主键元组)；None 表示还没有身份（pending）。
    """

    __slots__ = ("_ref", "key", "committed", "deleted", "session", "has_identity")

    def __init__(self, obj: Any) -> None:
        # 官方：state 对对象是**弱**引用（state.obj() 可能返回 None）
        self._ref: "weakref.ref[Any]" = weakref.ref(obj)
        self.key: Optional[Tuple[Any, ...]] = None
        self.committed: Dict[str, Any] = {}
        self.deleted: bool = False
        self.session: Optional["Session"] = None
        self.has_identity: bool = False

    @property
    def obj(self) -> Any:
        return self._ref()

    # ---- 脏检查：与官方 attributes.get_history 同口径：只比已提交快照 ----
    def attrs(self) -> Dict[str, Any]:
        return {k: v for k, v in vars(self.obj).items() if not k.startswith("_")}

    def dirty_attrs(self) -> Dict[str, Any]:
        cur = self.attrs()
        return {
            k: v
            for k, v in cur.items()
            if k not in self.committed or self.committed[k] != v
        }

    def is_dirty(self) -> bool:
        return bool(self.dirty_attrs())


class IdentityMap:
    """弱引用身份映射（官方 _WeakInstanceDict 的可执行版）。

    要点：对象是**弱**持有的，被 GC 掉后 __contains__ 返回 False 而键可能仍在字典里
    （官方源码把这种「GC 把键带走」的情形用 try/except KeyError 兜住）。
    """

    def __init__(self) -> None:
        self._dict: Dict[Tuple[Any, ...], InstanceState] = {}

    # 官方 add()：已存在**另一个活着**的同键对象 → 抛 InvalidRequestError；
    # 已存在同一个 state → 返回 False（不重复登记）。
    def add(self, state: InstanceState) -> bool:
        key = state.key
        assert key is not None
        if key in self._dict:
            existing_state = self._dict[key]
            if existing_state is not state:
                if existing_state.obj is not None:
                    raise InvalidRequestError(
                        "Can't attach instance %s; another instance with key %s "
                        "is already present in this session." % (type(state.obj).__name__, key)
                    )
            else:
                return False
        self._dict[key] = state
        return True

    # 官方 replace()：不抛异常，直接换掉并把旧 state 摘出去。
    def replace(self, state: InstanceState) -> Optional[InstanceState]:
        assert state.key is not None
        existing = self._dict.get(state.key)
        if existing is not None and existing is not state:
            pass  # _manage_removed_state(existing)
        self._dict[state.key] = state
        return existing

    def get(self, key: Tuple[Any, ...]) -> Optional[InstanceState]:
        if key not in self._dict:
            return None
        state = self._dict[key]
        return state if state.obj is not None else None

    def __contains__(self, key: Tuple[Any, ...]) -> bool:
        return self.get(key) is not None

    def __len__(self) -> int:
        return sum(1 for k in self._dict if self.get(k) is not None)

    def live_states(self) -> List[InstanceState]:
        return [s for s in self._dict.values() if s.obj is not None]



class _Sentinel:
    def __eq__(self, other: Any) -> bool:
        return False

    def __hash__(self) -> int:
        return 0


_STATES: "weakref.WeakKeyDictionary[Any, InstanceState]" = weakref.WeakKeyDictionary()


def _state(obj: Any) -> InstanceState:
    st = _STATES.get(obj)
    if st is None:
        st = InstanceState(obj)
        _STATES[obj] = st
    return st

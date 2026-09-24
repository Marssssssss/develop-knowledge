"""list_array_tt 与 ro 上的 list / relative_list_list_t 二选一存储。

对照 objc4 `runtime/objc-runtime-new.h` 2024-2135 行（list_array_tt::attachLists、
attachListList、copyListList）与 `runtime/PointerUnion.h`。
"""

from rwbits_bits import Violation


class Item:
    """一个 method_list_t / property_list_t / protocol_list_t 占位对象。"""

    def __init__(self, tag):
        self.tag = tag
        self.is_dup = False

    def duplicate(self):
        c = Item(self.tag)
        c.is_dup = True
        return c

    def __repr__(self):
        return "Item(%s%s)" % (self.tag, "'" if self.is_dup else "")


class ListOrListList:
    """`ro->baseMethods` 之类：单个 list 与 relative_list_list_t 二选一。

    真实类型是 `objc::PointerUnion<method_list_t, relative_list_list_t<...>>`，
    同样用**最低位**做标签（见 PointerUnion.h）。
    """

    def __init__(self, kind, value):
        if kind not in ("list", "rel"):
            raise ValueError(kind)
        self.kind = kind
        self.value = value

    def dyn_list(self):
        return self.value if self.kind == "list" else None

    def dyn_rel(self):
        return self.value if self.kind == "rel" else None


class ListArray:
    """`list_array_tt`：0 / 1 / array_t / relative_list_list_t 四种存储。"""

    def __init__(self, kind="null", value=None):
        self.kind = kind
        self.value = value

    @classmethod
    def from_list(cls, x):
        return cls("list", x)

    @classmethod
    def from_rel(cls, xs):
        return cls("rel", list(xs))

    def lists(self):
        if self.kind == "null":
            return ()
        if self.kind == "list":
            return (self.value,)
        return tuple(self.value)

    def attachLists(self, added, preoptimized=False):
        if not added:
            return                                   # addedCount == 0：直接 return
        if preoptimized and self.kind == "rel":
            return                                   # 已在相对列表里，不必再挂
        for a in added:
            if a in self.lists():
                raise Violation("list attached twice")
        if self.kind == "null" and len(added) == 1:  # 0 -> 1
            self.kind, self.value = "list", added[0]
        elif self.kind == "null" or self.kind == "list":   # 0/1 -> many
            old = self.value if self.kind == "list" else None
            arr = [None] * (len(added) + (1 if old else 0))
            if old is not None:
                arr[len(added)] = old                # 老的挪到末尾
            for i, a in enumerate(added):
                arr[i] = a                           # 新的排在最前
            self.kind, self.value = "array", arr
        elif self.kind == "array":                   # many -> many
            old = self.value
            arr = [None] * (len(old) + len(added))
            for i in range(len(old) - 1, -1, -1):
                arr[i + len(added)] = old[i]
            for i, a in enumerate(added):
                arr[i] = a
            self.kind, self.value = "array", arr
        else:                                        # rel -> many
            arr = list(added) + list(self.value)
            self.kind, self.value = "array", arr

    def attachListList(self, rel):
        if self.kind != "null":
            raise Violation("attachListList onto non-empty storage")
        self.kind, self.value = "rel", list(rel)

    def copyListList(self, num_loaded):
        if self.kind != "rel" or num_loaded == 0:
            raise Violation("copyListList needs a non-empty relative list")
        xs = list(self.value)[:num_loaded]
        if len(xs) == 1:
            self.kind, self.value = "list", xs[0]
        else:
            self.kind, self.value = "array", xs

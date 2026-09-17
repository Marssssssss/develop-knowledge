"""模型里的"容器"：类型描述、值描述、内存 Store。

与 `value_model.py`（reflect.Value 的 flag 语义）分开，避免单文件超 300 行。
"""

from kinds import STRUCT


class Field:
    __slots__ = ("name", "kind", "typ", "embedded", "struct_type")

    def __init__(self, name, kind, typ, struct_type=None, embedded=False):
        self.name = name
        self.kind = kind
        self.typ = typ
        self.struct_type = struct_type
        self.embedded = embedded

    @property
    def exported(self):
        """源码用 field.Name.IsExported()：首字符为大写字母。"""
        return bool(self.name) and self.name[0].isupper()


class StructType:
    """只实现字段查找（嵌入提升 + 歧义判定），不做字段对齐。"""

    __slots__ = ("name", "fields")

    def __init__(self, name, fields):
        self.name = name
        self.fields = fields

    def field_by_name(self, name):
        """返回 (index_path, Field)；找不到或存在歧义时返回 (None, None)。

        规则对齐 reflect：先看当前深度的全部字段，命中恰好一个才采用；命中多个
        即歧义（ok == false）；否则下降到嵌入结构体继续。最多下钻 8 层。
        """
        level = [((i,), f) for i, f in enumerate(self.fields)]
        for _ in range(8):
            hits = [(idx, f) for idx, f in level if f.name == name]
            if len(hits) == 1:
                return hits[0]
            if len(hits) > 1:
                return None, None
            nxt = []
            for idx, f in level:
                if f.embedded and f.struct_type is not None:
                    for j, g in enumerate(f.struct_type.fields):
                        nxt.append((idx + (j,), g))
            if not nxt:
                break
            level = nxt
        return None, None


class Goval:
    """一个 Go 值的三要素：Kind、类型名、数据。"""

    __slots__ = ("kind", "typ", "value", "struct_type")

    def __init__(self, kind, typ, value, struct_type=None):
        self.kind = kind
        self.typ = typ
        self.value = value
        self.struct_type = struct_type

    def copy(self):
        v = self.value
        if isinstance(v, (list, dict, set)):
            v = type(v)(v)
        return Goval(self.kind, self.typ, v, self.struct_type)


class Ptr:
    """模型里的指针：指向某个 slot，并记住被指对象的 Kind/类型。"""

    __slots__ = ("target", "pointee")

    def __init__(self, target, pointee):
        self.target = target
        self.pointee = pointee


class StructObj:
    """模型里的结构体实例：一份字段 slot 列表。"""

    __slots__ = ("slots",)

    def __init__(self, slots):
        self.slots = slots


class Store:
    """模拟可取地址的存储：slot id → 数据。"""

    def __init__(self):
        self.slots = {}
        self._next = 1

    def alloc(self, value):
        s = self._next
        self._next += 1
        self.slots[s] = value
        return s

    def load(self, slot):
        return self.slots[slot]

    def put(self, slot, value):
        self.slots[slot] = value


def new_struct(store, stype, values):
    """建一个结构体变量，返回 (它的 Goval, 它的 slot)。"""
    obj = StructObj([store.alloc(v) for v in values])
    slot = store.alloc(obj)
    return Goval(STRUCT, stype.name, obj, stype), slot

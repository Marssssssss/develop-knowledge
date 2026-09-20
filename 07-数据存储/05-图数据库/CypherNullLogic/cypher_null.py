"""Cypher ``null`` 与三值逻辑最小模型。

依据 Neo4j Cypher Manual → Values and types → Working with null（实读）：
  * ``null`` 表示**缺失或未知值**；Cypher 中所有数据类型都可为空，因此
    **类型谓词表达式对 null 一律返回 true**。
  * 读取节点上不存在的属性得到 ``null``；大多数以 null 为输入的表达式产出 null。
  * 在 ``WHERE`` 里，**任何不是 true 的结果都被解释为 false**。
  * **null 不等于 null**：``null = null`` 得到 ``null`` 而不是 ``true``
    —— 不知道两个值并不代表它们是同一个值。
  * 布尔运算符（AND / OR / XOR / NOT）把 null 当作三值逻辑的**未知值**。
  * ``IN``：能确定存在则 true；列表含 null 且没有匹配元素则 null；否则 false。
  * ``[]``（列表/映射取值、区间）任一端为 null 则结果为 null。
  * 排序（ORDER BY）：**升序时 null 排在最后，降序时排在最前**。

本模型用 Python 的 ``None`` 表示 Cypher 的 ``null``。
"""

import math

NULL = None

# 官方真值表原始 9 行：(a, b, a AND b, a OR b, a XOR b, NOT a)
TRUTH_TABLE = [
    (False, False, False, False, False, True),
    (False, None, False, None, None, True),
    (False, True, False, True, True, True),
    (True, False, False, True, True, False),
    (True, None, None, True, None, False),
    (True, True, True, True, False, False),
    (None, False, False, None, None, None),
    (None, None, None, None, None, None),
    (None, True, None, True, None, None),
]


def cy_and(a, b):
    """三值 AND：false 吸收，其余含 null 则为 null。"""
    if a is False or b is False:
        return False
    if a is None or b is None:
        return None
    return True


def cy_or(a, b):
    """三值 OR：true 吸收，其余含 null 则为 null。"""
    if a is True or b is True:
        return True
    if a is None or b is None:
        return None
    return False


def cy_not(a):
    if a is None:
        return None
    return not a


def cy_xor(a, b):
    """三值 XOR：任一侧为 null 则结果为 null（XOR 无吸收律）。"""
    if a is None or b is None:
        return None
    return a != b


def cy_eq(a, b):
    """``a = b``：任一侧为 null 得到 null（所以 null = null 是 null）。"""
    if a is None or b is None:
        return None
    return a == b


def cy_ne(a, b):
    """``a <> b``：同样任一侧为 null 得到 null。"""
    if a is None or b is None:
        return None
    return a != b


def is_null(x):
    return x is None


def is_not_null(x):
    return x is not None


def where(pred):
    """``WHERE`` 的判定：只有 true 通过，false 与 null 都当 false。"""
    return pred is True


def cy_in(x, lst):
    """Cypher ``IN``：能确定存在→true；含 null 且无匹配→null；否则 false。"""
    hit = False
    for v in lst:
        r = cy_eq(x, v)
        if r is True:
            return True
        if r is None:
            hit = True
    return None if hit else False


def cy_get(container, key):
    """列表下标 / 映射键取值：键为 null 或容器无该键时得到 null。"""
    if key is None:
        return None
    try:
        return container[key]
    except (IndexError, KeyError, TypeError):
        return None


def cy_slice(seq, lo, hi):
    """区间切片：任一端为 null 则整个结果为 null。"""
    if lo is None or hi is None:
        return None
    return seq[lo:hi]


def cy_all(lst):
    """all()：AND 折叠 —— 有 false 即 false，否则有 null 即 null。"""
    acc = True
    for v in lst:
        acc = cy_and(acc, v)
    return acc


def cy_any(lst):
    """any()：OR 折叠 —— 有 true 即 true，否则有 null 即 null。"""
    acc = False
    for v in lst:
        acc = cy_or(acc, v)
    return acc


def cy_none(lst):
    return cy_not(cy_any(lst))


def cy_single(lst):
    """single()：能确定「恰好一个 true」才返回 true/false，否则 null。

    口径：官方文档只给出「all/any/none/single 遵循同样的规则——能确定就给出
    true 或 false，否则产出 null」这一总纲，没有逐函数的表格。这里按该总纲
    实现：true 的个数 ≥2 必定 false；恰 1 个 true 且存在 null 时无法确定
    （null 取 true 会变成 2 个）故为 null。
    """
    n_true = sum(1 for v in lst if v is True)
    has_null = any(v is None for v in lst)
    if n_true >= 2:
        return False
    if n_true == 1:
        return None if has_null else True
    return None if has_null else False


def coalesce(*args):
    for a in args:
        if a is not None:
            return a
    return None


INT = "INTEGER"
STRING = "STRING"


def type_predicate(x, typename):
    """类型谓词 ``x IS :: T``：对 null **一律返回 true**（所有类型都可为空），
    对非 null 才真正做类型判定。

    官方原话："All data types in Cypher are nullable. This means that type
    predicate expressions always return true for null values."
    """
    if x is None:
        return True
    table = {INT: int, STRING: str}
    return isinstance(x, table[typename]) and not isinstance(x, bool)


def cy_arith(a, b):
    """算术表达式 ``a + b``：任一侧为 null 得 null。"""
    if a is None or b is None:
        return None
    return a + b


def cy_lt(a, b):
    """比较 ``a < b``：任一侧为 null 得 null。"""
    if a is None or b is None:
        return None
    return a < b


def cy_sin(x):
    """``sin(x)``：参数为 null 得 null。"""
    if x is None:
        return None
    return math.sin(x)


def order_by(rows, desc=False):
    """ORDER BY：升序 null 最后，降序 null 最前。"""
    nulls = [r for r in rows if r is None]
    rest = sorted([r for r in rows if r is not None], reverse=desc)
    return nulls + rest if desc else rest + nulls


class Node:
    """最简属性容器：读取不存在的属性得到 null。"""

    def __init__(self, **props):
        self.props = props

    def prop(self, name):
        return self.props.get(name, None)


def head(lst):
    """head([]) 得到 null。"""
    return lst[0] if lst else None

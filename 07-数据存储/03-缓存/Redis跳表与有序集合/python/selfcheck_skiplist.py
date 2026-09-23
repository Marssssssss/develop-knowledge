"""自检：Redis ZSET skiplist（skiplist.py）。随机层高用确定性源钉死，避免「通过只是运气」。"""

import math

from skiplist import (
    RAND_MAX, RANDOM_THRESHOLD, ZSKIPLIST_MAXLEVEL, Node, ZSkipList,
    compare_with_node, get_span, set_span, zsl_random_level,
)
from zset_encoding import (
    LISTPACK, SKIPLIST, ZSET_MAX_LISTPACK_ENTRIES, ZSET_MAX_LISTPACK_VALUE,
    ZSetObject, zadd, zset_convert_to_listpack_if_needed, zset_type_create,
    zset_type_maybe_convert,
)

PASS = 0
FAIL = []


def check(label, got, expect):
    global PASS
    if got == expect:
        PASS += 1
    else:
        FAIL.append(f"{label}: got {got!r}, expect {expect!r}")


def raises(label, fn, *a, **kw):
    global PASS
    try:
        fn(*a, **kw)
    except Exception:
        PASS += 1
    else:
        FAIL.append(f"{label}: expected an exception")


class Fixed:
    """恒定随机源。"""

    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


class Seq:
    """按序返回预设值（确定性钉死层高）。"""

    def __init__(self, values):
        self.values = list(values)
        self.i = 0

    def __call__(self):
        value = self.values[self.i]
        self.i += 1
        return value


# ---------------------------------------------------------------- 层高生成
check("threshold = P*RAND_MAX", RANDOM_THRESHOLD, 0.25 * 2147483647)
check("全不中奖 → level 1", zsl_random_level(Seq([RAND_MAX])), 1)
check("两次中奖 → level 3", zsl_random_level(Seq([0, 0, RAND_MAX])), 3)
check("31 次中奖 → level 32", zsl_random_level(Seq([0] * 31 + [RAND_MAX])), 32)
check("超过 32 被夹到 MAXLEVEL", zsl_random_level(Seq([0] * 40 + [RAND_MAX])), ZSKIPLIST_MAXLEVEL)
check("等于 threshold 上取整 → 不中奖",
      zsl_random_level(Seq([int(RANDOM_THRESHOLD) + 1])), 1)
check("MAXLEVEL 常量", ZSKIPLIST_MAXLEVEL, 32)

# ---------------------------------------------------------------- 比较语义
check("NULL 视为 +infinity", compare_with_node(1.0, "a", None), -1)
node_a = Node(1, 1.0, "a")
check("score 小", compare_with_node(0.5, "z", node_a), -1)
check("score 大", compare_with_node(1.5, "z", node_a), 1)
check("score 同 ele 小", compare_with_node(1.0, "A", node_a), -1)
check("score 同 ele 大", compare_with_node(1.0, "b", node_a), 1)
check("完全相同", compare_with_node(1.0, "a", node_a), 0)

# ------------------------------------------------------- 全 level-1 的基线
zsl = ZSkipList()
for score, ele in [(1.0, "a"), (2.0, "b"), (3.0, "c")]:
    zsl.insert(score, ele, Fixed(RAND_MAX))
check("长度", zsl.length, 3)
check("zsl.level 仍为 1", zsl.level, 1)
check("顺序", zsl.in_order(), [(1.0, "a"), (2.0, "b"), (3.0, "c")])
check("rank 1-based", [zsl.get_rank(s, e) for s, e in zsl.in_order()], [1, 2, 3])
check("找不到返回 0", zsl.get_rank(9.0, "z"), 0)
check("按 rank 取第 1 个", zsl.get_element_by_rank(1).ele, "a")
check("按 rank 取第 3 个", zsl.get_element_by_rank(3).ele, "c")
check("rank 越界(0)", zsl.get_element_by_rank(0), None)
check("rank 越界(4)", zsl.get_element_by_rank(4), None)
check("backward 只在 level0", zsl.tail.backward.ele, "b")
check("首节点 backward 为 None", zsl.header.level[0].forward.backward, None)
check("rank_via_span 与 get_rank 一致",
      [zsl.rank_via_span(n) for n in
       (zsl.header.level[0].forward, zsl.tail.backward, zsl.tail)], [1, 2, 3])

# --------------------------------------------------- level[0].span 是 NodeInfo
check("level0 span 读取：非尾为 1", get_span(zsl.header.level[0].forward, 0), 1)
check("level0 span 读取：尾为 0", get_span(zsl.tail, 0), 0)
_span_before = zsl.tail.level[0].span
set_span(zsl.tail, 0, 99)
check("level0 写 span 是空操作", zsl.tail.level[0].span, _span_before)

# ------------------------------------------------------------ 多层 span 一致
multi = ZSkipList()
for levels, score, ele in [(1, 1.0, "a"), (2, 2.0, "b"), (4, 3.0, "c"), (1, 4.0, "d")]:
    multi.insert_node(Node(levels, score, ele))
check("多层 zsl.level 跟到 4", multi.level, 4)
check("多层顺序", multi.in_order(), [(1.0, "a"), (2.0, "b"), (3.0, "c"), (4.0, "d")])
check("多层 rank", [multi.get_rank(s, e) for s, e in multi.in_order()], [1, 2, 3, 4])
check("多层按 rank 取值", [multi.get_element_by_rank(r).ele for r in (1, 2, 3, 4)],
      ["a", "b", "c", "d"])
_nodes = []
_x = multi.header.level[0].forward
while _x:
    _nodes.append(_x)
    _x = _x.level[0].forward
check("多层 rank_via_span 一致", [multi.rank_via_span(n) for n in _nodes], [1, 2, 3, 4])

# -------------------------------------------------------------------- 删除
check("删除命中", multi.delete(3.0, "c").ele, "c")
check("删除后长度", multi.length, 3)
check("删除后顺序", multi.in_order(), [(1.0, "a"), (2.0, "b"), (4.0, "d")])
check("删除后 rank 重排", [multi.get_rank(s, e) for s, e in multi.in_order()], [1, 2, 3])
check("删除后 rank_via_span 仍一致",
      [multi.rank_via_span(n) for n in
       (multi.header.level[0].forward, multi.tail.backward, multi.tail)], [1, 2, 3])
check("删掉唯一的 4 层节点 → 顶层回收到 2（b 是 2 层）", multi.level, 2)
multi.delete(2.0, "b")
check("再删掉 2 层节点 → 顶层回收到 1", multi.level, 1)
check("删除不存在的元素返回 None", multi.delete(99.0, "zz"), None)

# ------------------------------------------------------------ 区间提前判空
check("区间 [1,3] 非空", multi.is_in_range(1.0, 3.0), True)
check("tail 够不到下界 → 空", multi.is_in_range(5.0, 9.0), False)
check("首节点超过上界 → 空", multi.is_in_range(0.0, 0.5), False)
check("min==max 且开区间 → 空", multi.is_in_range(2.0, 2.0, maxex=True), False)
check("min > max → 空", multi.is_in_range(3.0, 1.0), False)
check("min==max 闭区间且命中", multi.is_in_range(2.0, 2.0), True)

# ------------------------------------------------------------ 初始编码选择
check("hint(0,0) → listpack", zset_type_create(0, 0).encoding, LISTPACK)
check("hint(128,64) → listpack", zset_type_create(128, 64).encoding, LISTPACK)
check("hint(129,64) → skiplist", zset_type_create(129, 64).encoding, SKIPLIST)
check("hint(128,65) → skiplist", zset_type_create(128, 65).encoding, SKIPLIST)

# ------------------------------- maybe_convert 只看 size_hint（成对构造）
o1 = ZSetObject(LISTPACK)
zset_type_maybe_convert(o1, 200)
check("size_hint 200 → 转 skiplist", o1.encoding, SKIPLIST)
o2 = ZSetObject(LISTPACK)
zset_type_maybe_convert(o2, 100)
check("size_hint 100 → 保持 listpack（不看 val_len）", o2.encoding, LISTPACK)
o3 = ZSetObject(SKIPLIST)
zset_type_maybe_convert(o3, 200)
check("已是 skiplist → 不动", o3.encoding, SKIPLIST)

# -------------------------------------------------------------- ZADD 转换
o4 = zset_type_create(0, 0)
for i in range(ZSET_MAX_LISTPACK_ENTRIES):
    zadd(o4, f"m{i}", float(i))
check(f"插入 {ZSET_MAX_LISTPACK_ENTRIES} 个后仍是 listpack", o4.encoding, LISTPACK)
check("listpack 长度", o4.length(), ZSET_MAX_LISTPACK_ENTRIES)
zadd(o4, "overflow", 999.0)
check("第 129 个触发转换", o4.encoding, SKIPLIST)
check("转换后长度", o4.length(), ZSET_MAX_LISTPACK_ENTRIES + 1)

o5 = zset_type_create(0, 0)
zadd(o5, "x" * (ZSET_MAX_LISTPACK_VALUE + 1), 1.0)
check("单个超长 ele 立即转 skiplist", o5.encoding, SKIPLIST)
o6 = zset_type_create(0, 0)
zadd(o6, "x" * ZSET_MAX_LISTPACK_VALUE, 1.0)
check("刚好 64 字节的 ele 不触发", o6.encoding, LISTPACK)
o7 = zset_type_create(0, 0)
zadd(o7, "y", 1.0, safe_to_add=False)
check("lpSafeToAdd 失败也会触发转换", o7.encoding, SKIPLIST)

# ------------------------------------------------------- 反向转回 listpack
o8 = ZSetObject(SKIPLIST)
for i in range(ZSET_MAX_LISTPACK_ENTRIES + 1):
    o8.zsl.insert_node(Node(1, float(i), f"m{i}"))
zset_convert_to_listpack_if_needed(o8, 10, 100)
check("129 个成员不满足 <= 128 → 保持 skiplist", o8.encoding, SKIPLIST)
o9 = ZSetObject(SKIPLIST)
for i in range(ZSET_MAX_LISTPACK_ENTRIES):
    o9.zsl.insert_node(Node(1, float(i), f"m{i}"))
zset_convert_to_listpack_if_needed(o9, ZSET_MAX_LISTPACK_VALUE, 100)
check("128 个 + 元素长 64 → 转回 listpack", o9.encoding, LISTPACK)
o10 = ZSetObject(SKIPLIST)
for i in range(64):
    o10.zsl.insert_node(Node(1, float(i), f"m{i}"))
zset_convert_to_listpack_if_needed(o10, ZSET_MAX_LISTPACK_VALUE + 1, 100)
check("元素长 65 → 不转回", o10.encoding, SKIPLIST)
o11 = ZSetObject(LISTPACK)
zset_convert_to_listpack_if_needed(o11, 1, 1)
check("已是 listpack → 不动", o11.encoding, LISTPACK)
check("bpe 常数自洽（ln2^2 ≈ 0.48045）", round(math.log(2) ** 2, 15), 0.480453013918201)

if FAIL:
    print(f"FAILED {len(FAIL)} / {PASS + len(FAIL)}")
    for f in FAIL:
        print("  -", f)
    raise SystemExit(1)
print(f"skiplist selfcheck: {PASS} assertions passed")

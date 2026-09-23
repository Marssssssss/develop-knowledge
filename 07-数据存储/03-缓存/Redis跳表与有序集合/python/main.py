"""demo 631 主程序：插入一批 member，展示层高、span、rank 与编码切换。"""

import random

from skiplist import SKIPLIST, ZSKIPLIST_MAXLEVEL, ZSKIPLIST_P, ZSkipList
from zset_encoding import (
    LISTPACK, zadd, zset_convert_to_listpack_if_needed, zset_type_create,
)


def show_levels(tag, zsl):
    xs = []
    x = zsl.header.level[0].forward
    while x:
        xs.append(f"{x.ele}(L{len(x.level)})")
        x = x.level[0].forward
    print(f"{tag}: level={zsl.level} length={zsl.length} " + " ".join(xs))


def main():
    print(f"ZSKIPLIST_MAXLEVEL={ZSKIPLIST_MAXLEVEL}  ZSKIPLIST_P={ZSKIPLIST_P}")
    print(f"随机器阈值 threshold = P*RAND_MAX = {ZSKIPLIST_P * 2147483647}")

    # 1) 真实随机源下的层高分布（只做展示，自检里用确定性源钉死）
    rnd = random.Random(20260923)
    zsl = ZSkipList()
    members = [(1.0, "alpha"), (2.0, "beta"), (3.0, "gamma"),
               (3.0, "delta"), (4.0, "epsilon"), (5.0, "zeta")]
    for score, ele in members:
        zsl.insert(score, ele, lambda: rnd.randint(0, 2147483647))
    show_levels("插入 6 个成员", zsl)
    print("按分数+字典序排好:", zsl.in_order())
    for score, ele in zsl.in_order():
        print(f"  ZRANK {ele:<8} -> {zsl.get_rank(score, ele)}")
    print("ZRANGE 2..4:", [zsl.get_element_by_rank(r).ele for r in (2, 3, 4)])

    # 2) 删除中间节点后 rank 重排
    zsl.delete(3.0, "delta")
    show_levels("删除 delta", zsl)
    print("删除后:", [(s, e, zsl.get_rank(s, e)) for s, e in zsl.in_order()])

    # 3) 区间判空
    print("ZRANGEBYSCORE 2 4 是否有成员:", zsl.is_in_range(2.0, 4.0))
    print("ZRANGEBYSCORE 10 20 是否有成员:", zsl.is_in_range(10.0, 20.0))

    # 4) 编码切换：小 zset 是 listpack，越过 128 个元素转 skiplist
    obj = zset_type_create(0, 0)
    print(f"\n新建 zset 编码 = {obj.encoding}")
    for i in range(130):
        zadd(obj, f"key{i:03d}", float(i))
        if obj.encoding == SKIPLIST and i < 130:
            print(f"插入第 {i + 1} 个成员后编码切换为 {SKIPLIST}")
            break
    print(f"最终编码 = {obj.encoding}, 长度 = {obj.length()}")

    # 5) 反向：成员数降到阈值以内才可能转回 listpack
    back = obj
    while back.encoding == SKIPLIST and back.length() > 128:
        back.zsl.delete(float(back.length() - 1), f"key{back.length() - 1:03d}")
    zset_convert_to_listpack_if_needed(back, 8, 128 * 8)
    print(f"删到 128 个并请求反向转换后编码 = {back.encoding}")
    print(f"listpack 常量 = {LISTPACK}")


if __name__ == "__main__":
    main()

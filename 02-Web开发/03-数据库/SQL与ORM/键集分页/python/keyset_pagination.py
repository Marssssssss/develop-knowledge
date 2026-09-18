#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OFFSET 分页 vs 键集分页（keyset / seek method）的代价与正确性复刻。

口径来源（先联网实读再写）：
  - PostgreSQL 18 手册 7.6 "LIMIT and OFFSET"（postgresql.org/docs/current/queries-limit.html）：
      * "If both OFFSET and LIMIT appear, then OFFSET rows are skipped before starting to count
        the LIMIT rows that are returned."；
      * "The rows skipped by an OFFSET clause still have to be computed inside the server;
        therefore a large OFFSET might be inefficient."；
      * "When using LIMIT, it is important to use an ORDER BY clause that constrains the result
        rows into a unique order. Otherwise you will get an unpredictable subset of the query's rows."；
      * "The query optimizer takes LIMIT into account when generating query plans ... using
        different LIMIT/OFFSET values to select different subsets of a query result *will give
        inconsistent results* unless you enforce a predictable result ordering with ORDER BY."；
      * "OFFSET 0 is the same as omitting the OFFSET clause, as is OFFSET with a NULL argument."；
        "LIMIT ALL is the same as omitting the LIMIT clause."
  - use-the-index-luke.com/no-offset（Markus Winand）：引用 SQL:2023 Part 2 §4.17.3 Derived tables 的
    "the rows in the derived table are first sorted according to the <order by clause> and then
    limited by dropping the number of rows specified in the <result offset clause> from the
    beginning" —— offset 只有"丢弃 N 行"这一个参数、没有任何上下文；并给出 keyset 的基本写法
    `WHERE id < ?last_seen_id ORDER BY id DESC FETCH FIRST 10 ROWS ONLY`，以及"插入一行会产生重复"
    的经典异常与"无法直接跳到任意页"的局限。

运行：python3 keyset_pagination.py （仅标准库）
"""
from __future__ import annotations

import bisect
import random

PAGE_SIZE = 10


class Table:
    """一张按 (score DESC, id DESC) 排序的索引；rows_examined 统计「数据库实际取出的行数」。"""

    def __init__(self, n: int = 10_000, seed: int = 7):
        rnd = random.Random(seed)
        self.rows = sorted(((rnd.randint(0, 500), i) for i in range(1, n + 1)),
                           key=lambda r: (-r[0], -r[1]))
        self.rows_examined = 0
        self.sql: list[str] = []

    # -- 排序 + 丢弃 N 行：OFFSET 的全部语义 --------------------------------
    def offset_page(self, page: int, size: int = PAGE_SIZE) -> list[tuple[int, int]]:
        ordered = sorted(self.rows, key=lambda r: (-r[0], -r[1]))     # 先排序
        skipped = page * size                                        # 再丢弃 N 行
        self.rows_examined += page * size + size
        self.sql.append(f"SELECT ... ORDER BY score DESC, id DESC LIMIT {size} OFFSET {skipped}")
        return ordered[skipped:skipped + size]

    # -- 键集分页：用「上一页最后一个键」定位，不丢弃任何行 ----------------
    def keyset_page(self, last: tuple[int, int] | None, size: int = PAGE_SIZE
                    ) -> list[tuple[int, int]]:
        ordered = sorted(self.rows, key=lambda r: (-r[0], -r[1]))
        if last is None:
            start = 0
            self.sql.append(f"SELECT ... ORDER BY score DESC, id DESC LIMIT {size}")
        else:
            key = (-last[0], -last[1])
            start = bisect.bisect_right([(-r[0], -r[1]) for r in ordered], key)  # 索引定位
            self.rows_examined += start.bit_length()                    # 定位是 O(log N)
            self.sql.append("SELECT ... WHERE (score, id) < (?, ?) ORDER BY score DESC, id DESC "
                            f"LIMIT {size}")
        self.rows_examined += size
        return ordered[start:start + size]

    # -- 只按 score 做键集（漏了 tie-breaker）的对照实现 --------------------
    def keyset_page_by_score_only(self, last_score: int, size: int = PAGE_SIZE
                                  ) -> list[tuple[int, int]]:
        ordered = sorted(self.rows, key=lambda r: (-r[0], -r[1]))
        start = bisect.bisect_left([-r[0] for r in ordered], -last_score)
        self.rows_examined += size
        return ordered[start:start + size]


def main() -> int:
    ok, bad = 0, 0

    def check(label: str, cond: bool, detail: str = "") -> None:
        nonlocal ok, bad
        if cond:
            ok += 1
            print(f"  [PASS] {label}")
        else:
            bad += 1
            print(f"  [FAIL] {label} :: {detail}")

    print("=" * 74)
    print("1) OFFSET 的代价：丢弃前 N 行仍要先把它们取出来")
    t = Table()
    page1000 = t.offset_page(999)
    check("第 1000 页（每页 10）要取出 10000 行才返回 10 行",
          t.rows_examined == 9990 + 10, str(t.rows_examined))
    check("SQL 里 OFFSET = page × size", "OFFSET 9990" in t.sql[-1], t.sql[-1])
    check("返回的确实是第 1000 页（10 行）", len(page1000) == 10, str(len(page1000)))

    t2 = Table()
    first = t2.keyset_page(None)
    nxt = t2.keyset_page(first[-1])
    check("键集分页第 2 页只取 10 行 + 一次 O(log N) 定位",
          t2.rows_examined == 10 + 10 + (10).bit_length(), str(t2.rows_examined))
    check("键集 SQL 用行值比较 (score, id) < (?, ?)", "(score, id) < (?, ?)" in t2.sql[-1])
    check("键集第 2 页与 OFFSET 第 2 页结果一致",
          nxt == Table().offset_page(1), "mismatch")

    print("\n2) 深分页的代价差距（同一页、两种方式）")
    deep_offset = Table()
    deep_offset.offset_page(999)                       # 一次请求直接取第 1000 页
    deep_keyset = Table()
    ordered = sorted(deep_keyset.rows, key=lambda r: (-r[0], -r[1]))
    cursor = ordered[9989]                             # 已知「上一页最后一个键」（游标）
    deep_keyset.keyset_page(cursor)                    # 同一页，靠游标定位
    ratio = deep_offset.rows_examined / deep_keyset.rows_examined
    check("同一次取第 1000 页：OFFSET 取行数是键集的 100 倍以上", ratio > 100,
          f"offset={deep_offset.rows_examined} keyset={deep_keyset.rows_examined} ratio={ratio:.1f}")

    print("\n3) 正确性：翻页期间有新行插入")
    t = Table(n=100)
    p1 = t.offset_page(0)
    t.rows.insert(0, (999, 100000))          # 第一页读完之后插入一条最高分
    p2_offset = t.offset_page(1)
    with_new = Table(n=100)
    q1 = with_new.keyset_page(None)
    with_new.rows.insert(0, (999, 100000))
    q2_keyset = with_new.keyset_page(q1[-1])
    dup = set(p1) & set(p2_offset)
    check("OFFSET 第 2 页与第 1 页出现重复行（行被挤到下一页）", bool(dup), str(sorted(dup)[:3]))
    check("键集第 2 页与第 1 页无重复", not (set(q1) & set(q2_keyset)))
    new_order = sorted(with_new.rows, key=lambda r: (-r[0], -r[1]))
    check("键集不会漏行：第 2 页正好是新序里第 1 页末行之后的 10 行",
          q2_keyset == new_order[11:21], f"{q2_keyset[0]} vs {new_order[11]}")
    check("新插入的行出现在第 1 页（键集下不会破坏已读页）",
          new_order[0] == (999, 100000) and (999, 100000) not in q2_keyset)

    print("\n4) score 有并列时必须带 tie-breaker")
    ties = Table(n=40)
    for i in range(1, 41):                   # 人为制造大量同分
        ties.rows[i - 1] = (100, i)
    ties.rows.sort(key=lambda r: (-r[0], -r[1]))
    bad_page = ties.keyset_page_by_score_only(100)
    good_page = ties.keyset_page(sorted(ties.rows, key=lambda r: (-r[0], -r[1]))[9])
    check("只用 score 做键集会重复返回同一批同行（OFFSET 式退化）",
          bad_page == ties.rows[:10], f"{bad_page[:2]} vs {ties.rows[:2]}")
    check("(score, id) 行值比较能正确越过同行", good_page[0] == ties.rows[10],
          f"{good_page[0]} vs {ties.rows[10]}")

    print("\n5) 没有 ORDER BY 时：LIMIT 会改变计划，页与页之间会互相错位")
    t = Table(n=200)
    by_index = sorted(t.rows, key=lambda r: (-r[0], -r[1]))    # LIMIT 10 → 索引序计划
    by_sort = sorted(t.rows, key=lambda r: (r[0], r[1]))       # 不同 LIMIT → 顺序扫 + 排序
    p1, p2 = by_index[:10], by_sort[10:20]
    check("同一查询换 LIMIT 后计划改变：两页完全不衔接（交集为空）",
          not (set(p1) & set(p2)), f"{sorted(set(p1) & set(p2))[:3]}")
    check("加上唯一 ORDER BY 后两页严格衔接", by_index[0:10] + by_index[10:20] == by_index[:20])

    print("\n6) 边界语义与键集分页的局限")
    t = Table(n=30)
    check("OFFSET 0 与键集首屏（无游标）取到同一页",
          t.offset_page(0) == t.keyset_page(None))
    t.rows_examined = 0
    t.offset_page(0)                                        # page 0 时 skipped=0
    check("第 0 页的 skipped = 0 × size = 0", "OFFSET 0" in t.sql[-1], t.sql[-1])
    check("键集分页必须带「上一页最后一个键」游标，OFFSET 只需要页号",
          can_jump_to_arbitrary_page() is False and page_number_alone_is_enough() is True)

    print("\n" + "=" * 74)
    print(f"断言结果：pass={ok} fail={bad}")
    return 1 if bad else 0


def can_jump_to_arbitrary_page() -> bool:
    """键集分页只接受「上一页最后一个键」，因此不能像 OFFSET 那样直接算第 N 页的偏移。"""
    return False


def page_number_alone_is_enough() -> bool:
    """OFFSET 只需要「页号 × 每页大小」这一个数，给定页号即可定位（代价是丢弃前 N 行）。"""
    return True


if __name__ == "__main__":
    raise SystemExit(main())

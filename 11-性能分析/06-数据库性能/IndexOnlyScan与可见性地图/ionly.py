# -*- coding: utf-8 -*-
"""Index-Only Scan 可行性与可见性地图(VM)模型。

口径(实读源):PostgreSQL 官方 11.9 Index-Only Scans and Covering Indexes
与 24.1.4 Updating the Visibility Map。
"""


PAGE_SIZE = 8192


class Heap:
    """每页一个 all-visible 位;VACUUM 置位,页被修改即清零。"""

    def __init__(self, pages):
        self.vm = [False] * pages

    def vacuum(self, page_ids):
        for p in page_ids:
            self.vm[p] = True

    def modify(self, page_id):
        self.vm[page_id] = False          # 页上有任何修改,位即失效

    def all_visible_fraction(self):
        return sum(self.vm) / len(self.vm)


def index_only_eligible(index_type, index_cols, query_cols):
    """两个前置条件:索引类型支持 IOS + 查询只引用索引内列。"""
    supported = {"btree": True, "gist": "部分 opclass", "spgist": "部分 opclass",
                 "gin": False, "brin": False}
    if supported.get(index_type) is not True:
        return False, f"索引类型不支持({index_type})"
    if not set(query_cols) <= set(index_cols):
        return False, "查询引用了索引外列"
    return True, "ok"


def execute_ios(entries, heap):
    """逐索引项:VM 位已置→免堆访问;未置→必须回堆查可见性。"""
    heap_fetches = 0
    for page_id, _value in entries:
        if not heap.vm[page_id]:
            heap_fetches += 1                         # 位未置=退化为普通索引扫描
    return heap_fetches


def covering_index(key_cols, include_cols):
    """INCLUDE 语义:payload 不参与搜索键;唯一性只约束键列。"""

    class Index:
        cols = tuple(key_cols) + tuple(include_cols)
        key = tuple(key_cols)
        unique_on = tuple(key_cols)

        @classmethod
        def eligible(cls, query_cols):
            return set(query_cols) <= set(cls.cols)

    return Index


def partial_index_ios(partial_predicate_cols, query_where_cols, query_select_cols,
                      index_cols):
    """部分索引:WHERE 里的谓词列不在索引里,但索引本身保证其为真→免 recheck(9.6+)。"""
    implied = set(partial_predicate_cols) & set(query_where_cols)
    recheck_needed = bool(implied) is False
    eligible = set(query_select_cols) <= set(index_cols) and not recheck_needed
    return eligible, recheck_needed


def expression_index_gap(query_needs, index_expr_cols):
    """表达式索引 f(x):规划器要求『查询需要的列全部可从索引取得』,
    f(x) 不算取得 x——需把 x 加进 INCLUDE 才可能 IOS(官方承认的规划器局限)。"""
    missing = set(query_needs) - set(index_expr_cols)
    return sorted(missing)

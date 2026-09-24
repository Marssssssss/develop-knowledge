"""索引排序与提前终止 —— 自检。

期望值来自 SortField.java / Sort.java / IndexWriterConfig.java 源码实读 + 手算，已实跑校准。

运行：python selfcheck_sort.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sortmodel import (SortField, Sort, LeafReader, IndexWriterConfig,
                       SCORE, DOC, STRING, INT, LONG, DOUBLE, FLOAT,
                       CUSTOM, REWRITEABLE, STRING_VAL,
                       INDEX_SORTER_TYPES, FIELD_SCORE, FIELD_DOC,
                       STRING_FIRST, STRING_LAST)

PASS = 0
FAIL = []


def check(name, cond):
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(name)


def eq(name, got, want):
    check("%s (got=%r want=%r)" % (name, got, want), got == want)


# ================================================ 1. SortField 类型与校验
eq("FIELD_SCORE 的 field 为 None", FIELD_SCORE.get_field(), None)
eq("FIELD_DOC 的 field 为 None", FIELD_DOC.get_field(), None)
eq("FIELD_SCORE 类型", FIELD_SCORE.get_type(), SCORE)
eq("FIELD_DOC 类型", FIELD_DOC.get_type(), DOC)
eq("SCORE 需要算分", FIELD_SCORE.needs_scores(), True)
eq("DOC 不需要算分", FIELD_DOC.needs_scores(), False)

for t in (STRING, INT, LONG, DOUBLE, FLOAT):
    eq("%s 有 IndexSorter" % t, SortField("f", t).get_index_sorter(), True)
for t in (CUSTOM, DOC, REWRITEABLE, STRING_VAL, SCORE):
    eq("%s 没有 IndexSorter" % t, SortField(None, t).get_index_sorter()
       if t in (SCORE, DOC) else SortField("f", t).get_index_sorter(), False)
eq("可索引排序的类型共 5 种", sorted(INDEX_SORTER_TYPES),
   sorted([DOUBLE, FLOAT, INT, LONG, STRING]))

# field 为 None 时只允许 SCORE / DOC
try:
    SortField(None, STRING)
    check("field=None + STRING 应抛异常", False)
except ValueError as e:
    check("field=None + STRING 抛异常", "field can only be null" in str(e))
eq("field=None + SCORE 合法", SortField(None, SCORE).get_type(), SCORE)
eq("field=None + DOC 合法", SortField(None, DOC).get_type(), DOC)

# STRING 的 missingValue 白名单
for mv in (None, STRING_FIRST, STRING_LAST):
    eq("STRING missingValue=%r 合法" % mv, SortField("f", STRING, False, mv).get_type(), STRING)
try:
    SortField("f", STRING, False, "nope")
    check("STRING 非法 missingValue 应抛异常", False)
except ValueError as e:
    check("STRING 非法 missingValue 抛异常", "STRING_FIRST or STRING_LAST" in str(e))

# ================================================================ 2. Sort
eq("Sort() 默认按 SCORE", Sort().get_sort(), [FIELD_SCORE])
eq("RELEVANCE 就是空参 Sort", Sort.RELEVANCE.get_sort(), [FIELD_SCORE])
eq("INDEXORDER 是 [FIELD_DOC]", Sort.INDEXORDER.get_sort(), [FIELD_DOC])
eq("RELEVANCE 需要算分", Sort.RELEVANCE.needs_scores(), True)
eq("INDEXORDER 不需要算分", Sort.INDEXORDER.needs_scores(), False)
eq("只要有一个 SCORE 就要算分",
   Sort(SortField("a", INT), FIELD_SCORE).needs_scores(), True)
eq("混入自定义字段不算分", Sort(SortField("a", INT)).needs_scores(), False)

# Java 里 `new Sort()`（无参）与 `new Sort(new SortField[0])`（显式空数组）是两条路径：
# 前者走 [FIELD_SCORE]，后者抛异常。Python 的 *fields 无法区分，故用 from_array 表达后者。
try:
    Sort.from_array([])
    check("显式空字段数组应抛异常", False)
except ValueError as e:
    check("显式空字段数组抛异常", "There must be at least 1 sort field" in str(e))
eq("Sort() 无参不抛异常（默认 FIELD_SCORE）", len(Sort().get_sort()), 1)
eq("from_array 单字段等价", Sort.from_array([SortField("a", INT)]),
   Sort(SortField("a", INT)))

s = Sort(SortField("a", INT), SortField("b", LONG, reverse=True))
eq("getSort 保持顺序", [f.get_field() for f in s.get_sort()], ["a", "b"])
eq("toString 用逗号连接", str(s), str(s.fields[0]) + "," + str(s.fields[1]))
eq("hashCode 常量偏移", Sort(FIELD_SCORE).__hash__(),
   (0x45aaf665 + Sort._arrays_hash([FIELD_SCORE])) & 0xFFFFFFFF)
eq("相等 Sort 哈希相同",
   Sort(SortField("a", INT)).__hash__(), Sort(SortField("a", INT)).__hash__())
check("不同字段哈希不同",
      Sort(SortField("a", INT)).__hash__() != Sort(SortField("b", INT)).__hash__())
eq("equals 按字段数组", Sort(SortField("a", INT)), Sort(SortField("a", INT)))
check("reverse 不同则不相等",
      Sort(SortField("a", INT)) != Sort(SortField("a", INT, reverse=True)))

# rewrite：没变就返回 self，变了返回新对象
eq("rewrite 无变化返回自身", s.rewrite() is s, True)


class _Rewritten(SortField):
    def rewrite(self, searcher=None):
        return SortField(self.field, LONG)


s2 = Sort(_Rewritten("a", INT))
r2 = s2.rewrite()
check("rewrite 有变化返回新对象", r2 is not s2)
eq("改写后类型变了", r2.get_sort()[0].get_type(), LONG)

# ============================================ 3. setIndexSort 的校验
cfg = IndexWriterConfig()
try:
    cfg.set_index_sort(Sort.INDEXORDER)          # DOC 没有 IndexSorter
    check("INDEXORDER 不能做索引排序", False)
except ValueError as e:
    check("INDEXORDER 做索引排序抛异常", "Cannot sort index with sort field" in str(e))
try:
    cfg.set_index_sort(Sort.RELEVANCE)           # SCORE 同样不行
    check("RELEVANCE 不能做索引排序", False)
except ValueError:
    check("RELEVANCE 做索引排序抛异常", True)
# 部分字段不可用 → 整体拒绝，不会部分生效
try:
    cfg.set_index_sort(Sort(SortField("a", INT), FIELD_SCORE))
    check("含不可用字段应整体拒绝", False)
except ValueError:
    check("含不可用字段整体拒绝", True)
eq("被拒后 indexSort 未被修改", cfg.index_sort, None)

ok = cfg.set_index_sort(Sort(SortField("a", INT), SortField("b", STRING)))
eq("合法索引排序写入成功", ok.index_sort.get_sort()[1].get_type(), STRING)
eq("indexSortFields 是字段名集合", sorted(cfg.index_sort_fields), ["a", "b"])

# ======================================== 4. getPrimarySortField 的跳过规则
r = LeafReader(max_doc=100, field_infos=["a", "b"], meta={"sort": None})
eq("段没有索引排序 → None", Sort.get_primary_sort_field(r), None)

sf_a = SortField("a", INT)
sf_b = SortField("b", STRING)
sort_ab = Sort(sf_a, sf_b)

# 4a. 正常：第一个字段就生效
r1 = LeafReader(100, ["a", "b"], {"sort": sort_ab})
eq("首个字段生效", Sort.get_primary_sort_field(r1), sf_a)

# 4b. 第一个字段在本段没有值 → 跳过，用第二个
r2 = LeafReader(100, ["b"], {"sort": sort_ab})
eq("字段缺值则跳到下一个", Sort.get_primary_sort_field(r2), sf_b)

# 4c. 两个字段都没值 → None
r3 = LeafReader(100, [], {"sort": sort_ab})
eq("全都没值 → None", Sort.get_primary_sort_field(r3), None)

# 4d. 全段同值且 skipper 覆盖全部文档 → 跳过
r4 = LeafReader(100, ["a", "b"], {"sort": sort_ab},
                {"a": {"doc_count": 100, "min": 7, "max": 7}})
eq("全段同值则跳过", Sort.get_primary_sort_field(r4), sf_b)

# 4e. min != max → 不跳过
r5 = LeafReader(100, ["a", "b"], {"sort": sort_ab},
                {"a": {"doc_count": 100, "min": 1, "max": 9}})
eq("值有差异则不跳过", Sort.get_primary_sort_field(r5), sf_a)

# 4f. skipper 只覆盖部分文档（docCount != maxDoc）→ 不敢跳过
r6 = LeafReader(100, ["a", "b"], {"sort": sort_ab},
                {"a": {"doc_count": 50, "min": 7, "max": 7}})
eq("skipper 未覆盖全段则不跳过", Sort.get_primary_sort_field(r6), sf_a)

# 4g. field == null（自定义字段）→ 直接返回，不查 fieldInfo
r7 = LeafReader(100, [], {"sort": Sort(FIELD_SCORE)})
eq("field 为 None 直接返回", Sort.get_primary_sort_field(r7), FIELD_SCORE)

# 4h. 全部字段都被跳过 → None（段上没有有效主排序字段）
r8 = LeafReader(100, ["a", "b"], {"sort": sort_ab},
                {"a": {"doc_count": 100, "min": 0, "max": 0},
                 "b": {"doc_count": 100, "min": 3, "max": 3}})
eq("全部跳过 → None", Sort.get_primary_sort_field(r8), None)

# ============================================ 5. IndexWriterConfig 默认值
eq("DEFAULT_MAX_FULL_FLUSH_MERGE_WAIT_MILLIS",
   IndexWriterConfig.DEFAULT_MAX_FULL_FLUSH_MERGE_WAIT_MILLIS, 500)
eq("DEFAULT_RAM_BUFFER_SIZE_MB", IndexWriterConfig.DEFAULT_RAM_BUFFER_SIZE_MB, 16.0)
eq("DEFAULT_MAX_BUFFERED_DOCS 是关闭自动 flush",
   IndexWriterConfig.DEFAULT_MAX_BUFFERED_DOCS, -1)
c = IndexWriterConfig()
eq("默认等待 500ms", c.max_full_flush_merge_wait_millis, 500)
eq("默认 500 表示开启 full flush 合并", c.full_flush_merge_enabled, True)
c.set_max_full_flush_merge_wait_millis(0)
eq("设为 0 关闭 full flush 合并", c.full_flush_merge_enabled, False)
eq("默认没有索引排序", c.index_sort, None)

print("PASS=%d FAIL=%d" % (PASS, len(FAIL)))
for f in FAIL:
    print("  FAILED:", f)
sys.exit(1 if FAIL else 0)

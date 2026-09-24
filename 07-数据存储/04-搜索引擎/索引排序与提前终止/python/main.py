"""索引排序与提前终止 —— 演示入口。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sortmodel import (SortField, Sort, LeafReader, IndexWriterConfig,
                       SCORE, DOC, STRING, INT, LONG, DOUBLE, FLOAT,
                       CUSTOM, REWRITEABLE, STRING_VAL, INDEX_SORTER_TYPES,
                       FIELD_SCORE, FIELD_DOC)


def main():
    print("=" * 72)
    print("Lucene Sort / SortField 与索引排序")
    print("=" * 72)

    print("\n[1] 哪些 SortField 能做索引排序？")
    for t in (STRING, INT, LONG, DOUBLE, FLOAT, SCORE, DOC, CUSTOM,
              REWRITEABLE, STRING_VAL):
        f = SortField(None, t) if t in (SCORE, DOC) else SortField("f", t)
        print("    %-12s getIndexSorter() = %-5s  needsScores() = %s"
              % (t, f.get_index_sorter(), f.needs_scores()))
    print("    可索引排序的类型共 %d 种：%s"
          % (len(INDEX_SORTER_TYPES), sorted(INDEX_SORTER_TYPES)))

    print("\n[2] Sort 的两个常量")
    print("    Sort.RELEVANCE  = %s  needsScores=%s"
          % (Sort.RELEVANCE, Sort.RELEVANCE.needs_scores()))
    print("    Sort.INDEXORDER = %s  needsScores=%s"
          % (Sort.INDEXORDER, Sort.INDEXORDER.needs_scores()))

    print("\n[3] setIndexSort：有一个字段不可用就整体拒绝")
    cfg = IndexWriterConfig()
    for name, sort in (("INDEXORDER(DOC)", Sort.INDEXORDER),
                       ("RELEVANCE(SCORE)", Sort.RELEVANCE),
                       ("[INT, SCORE]", Sort(SortField("a", INT), FIELD_SCORE)),
                       ("[INT, STRING]", Sort(SortField("a", INT), SortField("b", STRING)))):
        try:
            cfg.set_index_sort(sort)
            print("    %-18s → 接受，indexSortFields=%s" % (name, sorted(cfg.index_sort_fields)))
        except ValueError as e:
            print("    %-18s → 拒绝：%s" % (name, e))

    print("\n[4] getPrimarySortField：段上真正生效的主排序字段")
    sf_a, sf_b = SortField("a", INT), SortField("b", STRING)
    sort_ab = Sort(sf_a, sf_b)
    cases = [
        ("正常", LeafReader(100, ["a", "b"], {"sort": sort_ab})),
        ("a 在本段无值", LeafReader(100, ["b"], {"sort": sort_ab})),
        ("a、b 都无值", LeafReader(100, [], {"sort": sort_ab})),
        ("a 全段同值", LeafReader(100, ["a", "b"], {"sort": sort_ab},
                                {"a": {"doc_count": 100, "min": 7, "max": 7}})),
        ("a 值有差异", LeafReader(100, ["a", "b"], {"sort": sort_ab},
                                {"a": {"doc_count": 100, "min": 1, "max": 9}})),
        ("skipper 只覆盖 50/100", LeafReader(100, ["a", "b"], {"sort": sort_ab},
                                        {"a": {"doc_count": 50, "min": 7, "max": 7}})),
        ("两个字段都被跳过", LeafReader(100, ["a", "b"], {"sort": sort_ab},
                               {"a": {"doc_count": 100, "min": 0, "max": 0},
                                "b": {"doc_count": 100, "min": 3, "max": 3}})),
        ("段无索引排序", LeafReader(100, ["a", "b"], {"sort": None})),
    ]
    for label, reader in cases:
        p = Sort.get_primary_sort_field(reader)
        print("    %-22s → %s" % (label, p.get_field() if p else "None"))

    print("\n[5] IndexWriterConfig 默认值")
    print("    DEFAULT_MAX_FULL_FLUSH_MERGE_WAIT_MILLIS = %d"
          % IndexWriterConfig.DEFAULT_MAX_FULL_FLUSH_MERGE_WAIT_MILLIS)
    print("    DEFAULT_RAM_BUFFER_SIZE_MB = %.1f" % IndexWriterConfig.DEFAULT_RAM_BUFFER_SIZE_MB)
    print("    DEFAULT_MAX_BUFFERED_DOCS = %d（DISABLE_AUTO_FLUSH）"
          % IndexWriterConfig.DEFAULT_MAX_BUFFERED_DOCS)
    c = IndexWriterConfig()
    print("    默认 full-flush 合并开启 = %s；设为 0 后 = %s"
          % (c.full_flush_merge_enabled,
             c.set_max_full_flush_merge_wait_millis(0).full_flush_merge_enabled))

    print("\n" + "=" * 72)


if __name__ == "__main__":
    main()

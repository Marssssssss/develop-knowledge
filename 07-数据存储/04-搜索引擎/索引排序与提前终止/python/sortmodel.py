"""Sort / SortField 语义与索引排序（IndexWriterConfig.setIndexSort + 主排序字段判定）。

忠实转写自 apache/lucene@main：
  lucene/core/src/java/org/apache/lucene/search/SortField.java        （Type 枚举、validateField、
                                                                        needsScores、getIndexSorter）
  lucene/core/src/java/org/apache/lucene/search/Sort.java             （构造、rewrite、needsScores、
                                                                        hashCode、getPrimarySortField）
  lucene/core/src/java/org/apache/lucene/index/IndexWriterConfig.java （setIndexSort 校验与默认值）

**建模口径**：`IndexSorter` 的**实际排序行为**不还原，本 demo 只关心
「哪些 SortField 允许做索引排序」这个**可判定的**开关，以及 `getPrimarySortField`
的跳过规则 —— 后者正是「查询排序与索引排序一致时能否提前终止」的判定入口。
"""

# ---------------------------------------------------------------------- Type

SCORE = "SCORE"
DOC = "DOC"
STRING = "STRING"
INT = "INT"
LONG = "LONG"
DOUBLE = "DOUBLE"
FLOAT = "FLOAT"
CUSTOM = "CUSTOM"
REWRITEABLE = "REWRITEABLE"
STRING_VAL = "STRING_VAL"

# SortField#getIndexSorter 的 switch：这五类返回实现，其余（含 default）返回 null
INDEX_SORTER_TYPES = frozenset([STRING, INT, LONG, DOUBLE, FLOAT])

STRING_FIRST = "<STRING_FIRST>"
STRING_LAST = "<STRING_LAST>"


class SortField(object):
    """SortField：字段名 + 类型 + 是否倒序 + 缺失值。"""

    def __init__(self, field, type_, reverse=False, missing_value=None):
        self._validate(field, type_, missing_value)
        self.field = field
        self.type = type_
        self.reverse = reverse
        self.missing_value = missing_value

    @staticmethod
    def _validate(field, type_, missing_value):
        if field is None and type_ not in (SCORE, DOC):
            raise ValueError("field can only be null when type is SCORE or DOC")
        if type_ == STRING and missing_value not in (None, STRING_FIRST, STRING_LAST):
            raise ValueError(
                "For Type.STRING, missing value must be either STRING_FIRST or STRING_LAST")

    def get_field(self):
        return self.field

    def get_type(self):
        return self.type

    def needs_scores(self):
        """只有 SCORE 需要算分。"""
        return self.type == SCORE

    def get_index_sorter(self):
        """能否用于索引排序。SCORE / DOC / CUSTOM / REWRITEABLE / STRING_VAL 一律 None。"""
        return self.type in INDEX_SORTER_TYPES

    def rewrite(self, searcher=None):
        return self

    def __eq__(self, other):
        if not isinstance(other, SortField):
            return False
        return (self.field == other.field and self.type == other.type
                and self.reverse == other.reverse)

    def __hash__(self):
        return hash((self.field, self.type, self.reverse))

    def __str__(self):
        # 照 Lucene 的写法：<int: "a"> / <doc> / <score> / <long!>；reverse 加感叹号
        s = "<" + self.type.lower()
        if self.reverse:
            s += "!"
        if self.field is not None:
            s += ': "%s"' % self.field
        return s + ">"


FIELD_SCORE = SortField(None, SCORE)
FIELD_DOC = SortField(None, DOC)


# ---------------------------------------------------------------------- Sort

class Sort(object):
    """排序条件：第一个字段为主序，同分依次用后续字段，最后用 docid 兜底。"""

    RELEVANCE = None      # 下面紧接着赋值
    INDEXORDER = None

    def __init__(self, *fields):
        if len(fields) == 0:
            # Sort() 无参 → [FIELD_SCORE]
            fields = (FIELD_SCORE,)
        self.fields = list(fields)

    @classmethod
    def from_array(cls, fields):
        """对应 Java 的 `new Sort(SortField[] fields)`：**显式传空数组会抛异常**。

        Python 的 `Sort(*[])` 会退化成"无参调用"从而命中 [FIELD_SCORE] 默认值，
        没法表达 Java 里 `new Sort(new SortField[0])` 这条路径，所以单独给一个入口。
        """
        fields = list(fields)
        if len(fields) == 0:
            raise ValueError("There must be at least 1 sort field")
        obj = cls.__new__(cls)
        obj.fields = fields
        return obj

    def get_sort(self):
        return list(self.fields)

    def needs_scores(self):
        for f in self.fields:
            if f.needs_scores():
                return True
        return False

    def rewrite(self, searcher=None):
        """任一字段被改写就返回**新的** Sort，否则返回 self。"""
        rewritten = [f.rewrite(searcher) for f in self.fields]
        changed = any(a is not b for a, b in zip(self.fields, rewritten))
        return Sort(*rewritten) if changed else self

    def __eq__(self, other):
        if not isinstance(other, Sort):
            return False
        return self.fields == other.fields

    def __hash__(self):
        # 0x45aaf665 + Arrays.hashCode(fields)
        return (0x45aaf665 + self._arrays_hash(self.fields)) & 0xFFFFFFFF

    @staticmethod
    def _arrays_hash(fields):
        h = 1
        for f in fields:
            h = (31 * h + (hash(f) & 0xFFFFFFFF)) & 0xFFFFFFFF
        # Java 的 int 溢出要还原成有符号
        return h - 0x100000000 if h >= 0x80000000 else h

    def __str__(self):
        return ",".join(str(f) for f in self.fields)

    # ---- 主排序字段判定 ----
    @staticmethod
    def get_primary_sort_field(reader):
        """该段上真正生效的第一个排序字段；全都被跳过则返回 None。

        跳过规则（源码注释逐条对应）：
          1. sort == null → None
          2. field == null → **直接返回**（自定义字段，无从判断，视为有效）
          3. 该字段在本段没有值（fieldInfo == null）→ 跳过
          4. 有 DocValuesSkipper 且覆盖全部文档（docCount == maxDoc）且 min == max
             → 全段同值，排序对该段是 no-op → 跳过
        """
        sort = reader.meta.get("sort")
        if sort is None:
            return None
        for sf in sort.get_sort():
            field = sf.get_field()
            if field is None:
                return sf
            if field not in reader.field_infos:
                continue
            skipper = reader.doc_values_skipper(field)
            if (skipper is not None
                    and skipper["doc_count"] == reader.max_doc
                    and skipper["min"] == skipper["max"]):
                continue
            return sf
        return None


Sort.RELEVANCE = Sort()
Sort.INDEXORDER = Sort(FIELD_DOC)


# ------------------------------------------------------- IndexWriterConfig 片段

class IndexWriterConfig(object):
    DEFAULT_MAX_FULL_FLUSH_MERGE_WAIT_MILLIS = 500
    DEFAULT_RAM_BUFFER_SIZE_MB = 16.0
    DEFAULT_MAX_BUFFERED_DOCS = -1        # DISABLE_AUTO_FLUSH
    DEFAULT_MAX_BUFFERED_DELETE_TERMS = -1

    def __init__(self):
        self.index_sort = None
        self.index_sort_fields = set()
        self.max_full_flush_merge_wait_millis = \
            self.DEFAULT_MAX_FULL_FLUSH_MERGE_WAIT_MILLIS
        self.ram_buffer_size_mb = self.DEFAULT_RAM_BUFFER_SIZE_MB

    def set_index_sort(self, sort):
        """每个字段都必须有 IndexSorter，否则抛异常（不会部分生效）。"""
        for sf in sort.get_sort():
            if not sf.get_index_sorter():
                raise ValueError("Cannot sort index with sort field %s" % sf)
        self.index_sort = sort
        self.index_sort_fields = set(sf.get_field() for sf in sort.get_sort())
        return self

    def set_max_full_flush_merge_wait_millis(self, millis):
        self.max_full_flush_merge_wait_millis = millis
        return self

    @property
    def full_flush_merge_enabled(self):
        """文档：Set to 0 to disable merging on full flush."""
        return self.max_full_flush_merge_wait_millis != 0


# ------------------------------------------------------------------ LeafReader

class LeafReader(object):
    """够用的 LeafReader 替身：段元信息 + 字段信息 + DocValuesSkipper。"""

    def __init__(self, max_doc, field_infos, meta=None, skippers=None):
        self.max_doc = max_doc
        self.field_infos = set(field_infos)
        self.meta = meta or {}
        self._skippers = skippers or {}

    def doc_values_skipper(self, field):
        return self._skippers.get(field)

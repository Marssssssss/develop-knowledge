"""分区边界与裁剪判定。

依据 PostgreSQL 18 官方文档 5.12《Table Partitioning》与源码
`src/backend/partitioning/partprune.c`。

边界语义(文档 5.12.1):

- RANGE: 每个分区的上下界**含下界、不含上界** —— "if one partition's range is from 1 to
  10, and the next one's range is from 10 to 20, then value 10 belongs to the second
  partition not the first"。
- LIST: 显式列出键值。
- HASH: 指定 modulus 与 remainder, `hash(partition key) % modulus == remainder`。

裁剪阶段(文档 5.12.4):

1. **计划期裁剪**: 由分区键隐式定义的约束驱动, **与索引无关**
   ("driven only by the constraints defined implicitly by the partition keys, not by the
   presence of indexes")。被裁掉的分区不出现在 EXPLAIN 里。
2. **执行期初始化裁剪**: 用 PREPARE 参数 / 子查询值等"初始化时已知"的值裁剪;
   不出现在 EXPLAIN 里, 只能通过 **`Subplans Removed: N`** 看出来。
   注意: **这个阶段被裁掉的分区在开始执行时仍然会被加锁**。
3. **执行期逐次裁剪**: 参数在嵌套循环里每次变化时重算; 要靠 EXPLAIN ANALYZE 的
   **loops** 差异判断, 完全没被执行的子计划显示 **(never executed)**。

`partprune.c` 里的匹配状态 (`PartClauseMatchStatus`) 与阶段目标 (`PartClauseTarget`):

```c
PARTCLAUSE_NOMATCH / MATCH_CLAUSE / MATCH_NULLNESS / MATCH_STEPS / MATCH_CONTRADICT / UNSUPPORTED
PARTTARGET_PLANNER / PARTTARGET_INITIAL / PARTTARGET_EXEC
```

本模块实现: 三种分区方式的"某值/某区间属于哪些分区", 加上 AND/OR 组合的矛盾检测。
"""


class RangePartition(object):
    """RANGE: [lower, upper)。None 表示无界。"""

    strategy = "range"

    def __init__(self, name, lower, upper):
        self.name = name
        self.lower = lower
        self.upper = upper

    def match_value(self, v):
        if self.lower is not None and v < self.lower:
            return False
        if self.upper is not None and v >= self.upper:
            return False
        return True

    def match_interval(self, lo, hi):
        """查询区间 [lo, hi) 与本分区是否有交集。"""
        a = self.lower if self.lower is not None else float("-inf")
        b = self.upper if self.upper is not None else float("inf")
        return max(lo, a) < min(hi, b)


class ListPartition(object):
    """LIST: 显式键值集合; 另有 DEFAULT 分区兜住其余值。"""

    strategy = "list"

    def __init__(self, name, values, is_default=False):
        self.name = name
        self.values = set(values)
        self.is_default = is_default

    def match_value(self, v):
        return v in self.values

    def match_interval(self, lo, hi):
        return any(lo <= v < hi for v in self.values)


class HashPartition(object):
    """HASH: hash(key) % modulus == remainder。"""

    strategy = "hash"

    def __init__(self, name, modulus, remainder, hashfn=None):
        self.name = name
        self.modulus = modulus
        self.remainder = remainder
        self.hashfn = hashfn or _default_hash

    def match_value(self, v):
        return (self.hashfn(v) % self.modulus) == self.remainder

    def match_interval(self, lo, hi):
        """HASH 无法按区间裁剪: 连续区间必然横跨所有余数。"""
        return True


def _default_hash(v):
    """稳定的整数散列(仅用于演示; 官方用的是各类型的 hash function)。"""
    h = 1469598103934665603
    for b in str(v).encode():
        h = ((h ^ b) * 1099511628211) & 0xFFFFFFFFFFFFFFFF
    return h


def prune(partitions, op, value):
    """按比较算子裁剪: 返回 (命中分区, 匹配状态)。"""
    hits = []
    status = "MATCH_CLAUSE"
    if op == "IS NULL":
        status = "MATCH_NULLNESS"
        for p in partitions:
            if isinstance(p, ListPartition):
                # list 分区只有显式含 NULL 或 DEFAULT 才可能装 NULL
                if p.is_default or None in p.values:
                    hits.append(p.name)
            elif isinstance(p, RangePartition):
                if p.lower is None:      # 无下界的范围分区可以容纳 NULL
                    hits.append(p.name)
        return hits, status
    for p in partitions:
        if isinstance(p, ListPartition) and p.is_default:
            # DEFAULT 分区无法被"值属于某集合"的推理排除
            hits.append(p.name)
            continue
        if op == "=":
            okv = p.match_value(value)
        elif op == "<":
            okv = p.match_interval(float("-inf"), value)
        elif op == "<=":
            okv = p.match_interval(float("-inf"), _next(value))
        elif op == ">":
            okv = p.match_interval(_next(value), float("inf"))
        elif op == ">=":
            okv = p.match_interval(value, float("inf"))
        elif op == "BETWEEN":
            lo, hi = value
            okv = p.match_interval(lo, _next(hi))
        else:
            return [], "UNSUPPORTED"
        if okv:
            hits.append(p.name)
    return hits, status


def _next(v):
    """闭区间转半开: 用相邻值近似(整数/日期均可比较, 这里 +1)。"""
    return v + 1


def combine_and(sets):
    return sorted(set.intersection(*[set(s) for s in sets])) if sets else []


def combine_or(sets):
    out = set()
    for s in sets:
        out |= set(s)
    return sorted(out)


def detect_contradiction(partitions, clauses):
    """矛盾条件: `x > 5 AND x < 3` -> PARTCLAUSE_MATCH_CONTRADICT(全裁)。"""
    sets = [set(prune(partitions, op, v)[0]) for op, v in clauses]
    inter = set.intersection(*sets) if sets else set()
    if not inter:
        return [], "MATCH_CONTRADICT"
    return sorted(inter), "MATCH_STEPS"

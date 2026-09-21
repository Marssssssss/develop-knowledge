"""裁剪的三个阶段模型(文档 5.12.4)。

| 阶段 | 何时 | 在 EXPLAIN 里怎么看出 |
| --- | --- | --- |
| 计划期 | 规划时值已知(字面量) | 被裁的分区**不出现在计划里** |
| 执行期初始化 | PREPARE 参数 / 子查询值 | 出现 **`Subplans Removed: N`**(但仍会加锁) |
| 执行期逐次 | 嵌套循环参数每次变化 | 看 EXPLAIN ANALYZE 的 **loops** 差异; 完全没跑的显示 **(never executed)** |

`enable_partition_pruning = off` 会同时关掉计划期与执行期裁剪(全部扫描)。
"""

from bounds import prune


class PrunePlan(object):
    def __init__(self, partitions, enable_pruning=True):
        self.partitions = partitions
        self.names = [p.name for p in partitions]
        self.enable_pruning = enable_pruning

    # --- 阶段一: 计划期 --------------------------------------------------------
    def planner_prune(self, clauses):
        """clauses: [(op, value), ...], 默认按 AND 组合。"""
        if not self.enable_pruning or not clauses:
            return list(self.names), 0
        sets = [set(prune(self.partitions, op, v)[0]) for op, v in clauses]
        inter = set.intersection(*sets) if sets else set(self.names)
        survivors = [n for n in self.names if n in inter]
        return survivors, len(self.names) - len(survivors)

    # --- 阶段二: 执行期初始化(参数已知) ---------------------------------------
    def initial_prune(self, clauses):
        """返回 (存活分区, Subplans Removed 数, 仍被加锁的分区)。"""
        if not self.enable_pruning:
            return list(self.names), 0, list(self.names)
        survivors = self.planner_prune(clauses)[0]
        removed = len(self.names) - len(survivors)
        # 文档: 这一阶段被裁掉的分区**在开始执行时仍会被加锁**
        return survivors, removed, list(self.names)

    # --- 阶段三: 执行期逐次 ------------------------------------------------------
    def exec_prune(self, outer_values, clause_for):
        """outer_values: 外层循环的取值序列; clause_for(v) -> [(op, value)]。

        返回 {分区名: loops} 与 never executed 的分区列表。
        """
        loops = {n: 0 for n in self.names}
        for v in outer_values:
            clauses = clause_for(v)
            if not self.enable_pruning:
                for n in self.names:
                    loops[n] += 1
                continue
            sets = [set(prune(self.partitions, op, val)[0]) for op, val in clauses]
            inter = set.intersection(*sets) if sets else set(self.names)
            for n in inter:
                if n in loops:
                    loops[n] += 1
        never = [n for n in self.names if loops[n] == 0]
        return loops, never

    def render(self, survivors, removed):
        lines = ["Append"]
        for n in survivors:
            lines.append("  -> Seq Scan on %s" % n)
        if removed:
            lines.append("  Subplans Removed: %d" % removed)
        return "\n".join(lines)

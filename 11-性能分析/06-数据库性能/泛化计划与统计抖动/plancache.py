# -*- coding: utf-8 -*-
"""PREPARE 泛化/定制计划切换与统计抖动模型。

口径(实读源):PostgreSQL 官方 sql-prepare.html(PREPARE 页 Notes 段)——
前五次执行用定制计划并计算平均代价,之后比较泛化计划代价;
比较的具体倍数文档未给出,本模型用可配置 factor 表示(标注为模型)。
"""

FORCED_REPLAN_TRIGGERS = [
    "DDL 变更(语句引用的对象定义变了)",
    "规划器统计信息被更新(ANALYZE)",
    "search_path 变化(9.3+ 按新 search_path 重新解析)",
]


class PreparedStmt:
    """参数化语句的计划选择状态机(plan_cache_mode=auto)。"""

    def __init__(self, has_params, factor=1.1):
        self.has_params = has_params
        self.factor = factor          # 模型系数:官方文档只说"不高于平均太多"
        self.custom_costs = []
        self.generic_cost = None
        self.using_generic = False

    def observe_custom(self, cost):
        """前五次执行:逐次定制计划并记账。"""
        if len(self.custom_costs) < 5:
            self.custom_costs.append(cost)
            return "custom"
        return self.decide()

    def decide(self):
        """第五次之后:泛化代价 ≤ factor×平均定制代价 → 切换泛化。"""
        avg = sum(self.custom_costs) / len(self.custom_costs)
        if self.generic_cost is not None and \
                self.generic_cost <= self.factor * avg:
            self.using_generic = True
            return "generic"
        return "custom"

    def run(self, value_cost=None):
        """value_cost: 本次参数值下定制计划的真实代价(有偏斜时与均值差很大)。"""
        if not self.has_params:
            return "generic"          # 无参数:永远泛化
        if len(self.custom_costs) < 5:
            return self.observe_custom(value_cost)
        mode = self.decide()
        return mode


def explain_shape(mode, params):
    """EXPLAIN EXECUTE 的形态:泛化计划显示 $n 占位,定制计划显示代入值。"""
    if mode == "generic":
        return "Index Scan ... Index Cond: (id = $1)"
    return f"Index Scan ... Index Cond: (id = {params})"


def replan_needed(previous, current):
    """上一次使用到本次使用之间发生了触发条件 → 强制重新分析/重规划。"""
    for t in FORCED_REPLAN_TRIGGERS:
        if t in current and t not in previous:
            return True
    return False

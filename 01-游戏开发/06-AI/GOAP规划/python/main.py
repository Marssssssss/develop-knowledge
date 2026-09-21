"""ReGoap 的 GOAP 规划器：反向 A* 的 Python 转写。

逐行实读后转写的官方源码（luxkun/ReGoap，master）：

- ``ReGoap/Planner/ReGoapNode.cs``   Init（g/h/cost、Goal 合并、goalMergedWithWorld）、Expand、CalculatePath、IsGoal
- ``ReGoap/Planner/AStar.cs``        Run（frontier / explored / stateToNode、earlyExit、迭代上限）
- ``ReGoap/Planner/ReGoapPlanner.cs`` Plan（目标按优先级排序、SelectNextGoal 加权随机、UsingDynamicActions 预检）
- ``ReGoap/Core/ReGoapState.cs``     HasAny / HasAnyConflict / MissingDifference / ReplaceWithMissingDifference
- ``ReGoap/Core/ReGoapCondition.cs`` IsMatch / AreCompatible

语言差异显式落地：
- C# 的 ``ConcurrentDictionary<T,W>`` → Python ``dict``（顺序即插入顺序，与 Go 侧的 map 无序不同，
  因此本模块把「状态」显式做成 **排序后的键值对元组** 才能当 dict 的 key）；
- C# 里 ``out`` 参数取不到键时给 ``default(W)`` → Python 用 ``dict.get`` 返回 None，
  ``ReGoapCondition.IsMatch`` 对 None 走等值比较，与官方一致；
- C# ``float`` → Python ``float``；成本比较直接比大小，不做 epsilon。
"""

# ---------------------------------------------------------------- 条件与匹配

class Cond:
    """ReGoapCondition：Equal / NotEqual / GreaterOrEqual / LessOrEqual。"""

    EQ, NE, GE, LE = "eq", "ne", "ge", "le"

    def __init__(self, op, value):
        self.op = op
        self.value = value

    @staticmethod
    def ge(value):
        return Cond(Cond.GE, value)

    @staticmethod
    def le(value):
        return Cond(Cond.LE, value)

    def satisfied_by_raw(self, candidate):
        if self.op == Cond.EQ:
            return candidate == self.value
        if self.op == Cond.NE:
            return candidate != self.value
        if self.op == Cond.GE:
            return candidate is not None and candidate >= self.value
        if self.op == Cond.LE:
            return candidate is not None and candidate <= self.value
        return False

    def compatible_with(self, other):
        # 与官方 IsCompatibleWith 同构：等值条件看对方是否被自己满足
        if self.op == Cond.EQ:
            return other.satisfied_by_raw(self.value)
        if other.op == Cond.EQ:
            return self.satisfied_by_raw(other.value)
        if self.op == Cond.NE:
            if other.op == Cond.NE:
                return True
            return self.value != other.value
        if other.op == Cond.NE:
            return other.compatible_with(self)
        # 区间 × 区间：两个界要有交集
        return True

    def __repr__(self):
        return "Cond(%s,%r)" % (self.op, self.value)


def is_match(required, candidate):
    if isinstance(required, Cond):
        return required.satisfied_by_raw(candidate)
    if isinstance(candidate, Cond):
        return candidate.satisfied_by_raw(required)
    return required == candidate


def are_compatible(left, right):
    if isinstance(left, Cond) and isinstance(right, Cond):
        return left.compatible_with(right)
    if isinstance(left, Cond):
        return left.satisfied_by_raw(right)
    if isinstance(right, Cond):
        return right.satisfied_by_raw(left)
    return left == right


# ---------------------------------------------------------------- 状态

class State:
    """ReGoapState 的最小语义。"""

    def __init__(self, values=None):
        self.values = dict(values or {})

    def clone(self):
        return State(self.values)

    def get(self, key):
        return self.values.get(key)

    def add_from_state(self, other):
        self.values.update(other.values)

    def __len__(self):
        return len(self.values)

    @property
    def count(self):
        return len(self.values)

    def key(self):
        return tuple(sorted((k, repr(v)) for k, v in self.values.items()))

    def has_any(self, other):
        for k, v in other.values.items():
            if is_match(v, self.values.get(k)):
                return True
        return False

    def has_any_conflict(self, other):
        for k, other_value in other.values.items():
            if k not in self.values:
                continue
            if not are_compatible(other_value, self.values[k]):
                return True
        return False

    def has_any_conflict_relaxed(self, changes, other):
        """更宽松的版本：被 changes 修掉的冲突不算冲突。"""
        for k, other_value in other.values.items():
            if k not in self.values:
                continue
            effect_value = changes.values.get(k)
            if (not are_compatible(other_value, self.values[k])
                    and not are_compatible(effect_value, self.values[k])):
                return True
        return False

    def missing_difference(self, other, into=None):
        count = 0
        for k, v in self.values.items():
            if not is_match(v, other.values.get(k)):
                count += 1
                if into is not None:
                    into.values[k] = v
        return count

    def replace_with_missing_difference(self, other):
        kept = {}
        for k, v in self.values.items():
            if not is_match(v, other.values.get(k)):
                kept[k] = v
        self.values = kept
        return len(kept)

    def __repr__(self):
        return "{" + ", ".join("%s=%r" % (k, v) for k, v in sorted(self.values.items())) + "}"


# ---------------------------------------------------------------- 动作与目标

class Action:
    def __init__(self, name, preconditions, effects, cost=1.0):
        self.name = name
        self.preconditions = State(preconditions)
        self.effects = State(effects)
        self.cost = cost

    def __repr__(self):
        return self.name


class Goal:
    def __init__(self, name, goal_state, priority=1.0):
        self.name = name
        self.goal_state = State(goal_state)
        self.priority = priority
        self.plan = None

    def __repr__(self):
        return self.name


# ---------------------------------------------------------------- 搜索节点

class GoapNode:
    def __init__(self, planner, goal, parent=None, action=None):
        self.planner = planner
        self.action = action
        self.parent = parent
        if parent is not None:
            self.state = parent.state.clone()
            self.g = parent.g
        else:
            self.state = planner.world.clone()
            self.g = 0.0

        if action is not None:
            self.goal = goal.clone()
            self.preconditions = action.preconditions.clone()
            self.effects = action.effects.clone()
            self.g += action.cost
            self.state.add_from_state(self.effects)
            self.goal.replace_with_missing_difference(self.effects)
            self.goal.add_from_state(self.preconditions)
        else:
            self.goal = goal
            self.preconditions = State()
            self.effects = State()

        self.h = len(self.goal)                       # h(node) = 剩余目标条件数
        self.cost = self.g + self.h                   # f = g + h（heuristicMultiplier = 1）

        merged = State()
        self.goal.missing_difference(self.planner.world, into=merged)
        self.goal_merged_with_world = merged

    def is_goal(self):
        return len(self.goal_merged_with_world) <= 0

    def expand(self):
        out = []
        actions = self.planner.actions
        for index in range(len(actions) - 1, -1, -1):   # 官方倒序遍历动作表
            candidate = actions[index]
            precond = candidate.preconditions
            effects = candidate.effects
            if (effects.has_any(self.goal)
                    and not self.goal.has_any_conflict_relaxed(effects, precond)
                    and not self.goal.has_any_conflict(effects)):
                out.append(GoapNode(self.planner, self.goal, self, candidate))
        return out

    def calculate_path(self):
        result = []
        node = self
        while node.parent is not None:
            result.append(node.action.name)
            node = node.parent
        return result                                   # 反向规划 ⇒ 回溯即正序

"""GOAP 规划器的搜索部分（A* 与目标选择），模型见 main.py。"""


import heapq

from main import Goal, GoapNode, State

# ---------------------------------------------------------------- A*

class AStar:
    def __init__(self, max_nodes_to_expand=1000):
        self.max_nodes_to_expand = max_nodes_to_expand
        self.enqueued_count = 0
        self.iterations = 0

    def run(self, start, max_iterations=1000, early_exit=False):
        frontier = []
        counter = 0
        heapq.heappush(frontier, (start.cost, counter, start))
        state_to_node = {}
        explored = {}

        while frontier and self.iterations < max_iterations and len(frontier) + 1 < self.max_nodes_to_expand:
            _, _, node = heapq.heappop(frontier)
            if node.is_goal():
                return node
            explored[node.state.key()] = node

            for child in node.expand():
                self.iterations += 1
                if early_exit and child.is_goal():
                    return child
                child_cost = child.cost
                state_key = child.state.key()
                if state_key in explored:
                    continue
                similar = state_to_node.get(state_key)
                if similar is not None:
                    if similar.cost > child_cost:
                        # frontier.Remove(similiarNode)：把旧节点真正从队列里摘掉
                        state_to_node.pop(state_key, None)
                        frontier = [item for item in frontier if item[2] is not similar]
                        heapq.heapify(frontier)
                    else:
                        break                                # 官方是 break 不是 continue
                counter += 1
                heapq.heappush(frontier, (child_cost, counter, child))
                state_to_node[state_key] = child
                self.enqueued_count += 1
        return None


# ---------------------------------------------------------------- 规划器

class Planner:
    def __init__(self, world, actions, settings=None):
        self.world = State(world)
        self.actions = list(actions)
        # 默认值取自 ReGoapPlannerSettings：PlanningEarlyExit=false、MaxIterations=1000、
        # MaxNodesToExpand=10000、UsingDynamicActions=false、WeightedRandomMinimumWeight=0.001
        self.settings = settings or {
            "max_iterations": 1000,
            "max_nodes_to_expand": 10000,
            "planning_early_exit": False,
            "using_dynamic_actions": False,
            "use_weighted_random": False,
            "weighted_random_priority_power": 1.0,
            "weighted_random_min_weight": 0.001,
        }
        self.astar = AStar(self.settings["max_nodes_to_expand"])

    def _goal_reachable_precheck(self, goal_state):
        """UsingDynamicActions == false 时的廉价可达性预检。"""
        wanted = goal_state.clone()
        for action in self.actions:
            nxt = State()
            wanted.missing_difference(action.effects, into=nxt)
            wanted = nxt
        final = State()
        wanted.missing_difference(self.world, into=final)
        return len(final) <= 0

    def select_next_goal(self, goals, rng=None):
        goals = sorted(goals, key=lambda g: g.priority)
        if not self.settings["use_weighted_random"] or len(goals) == 1 or rng is None:
            return goals[-1]
        power = max(0.01, self.settings["weighted_random_priority_power"])
        min_weight = max(1e-6, self.settings["weighted_random_min_weight"])
        weights = []
        total = 0.0
        for g in goals:
            weight = max(0.0, g.priority) ** power
            if weight < min_weight:
                weight = min_weight
            weights.append(weight)
            total += weight
        if total <= 0.0:
            return goals[-1]
        roll = rng() * total
        cumulative = 0.0
        for g, w in zip(goals, weights):
            cumulative += w
            if roll <= cumulative:
                return g
        return goals[-1]

    def plan(self, goals):
        possible = [g for g in goals if g.priority is not None]
        possible = sorted(possible, key=lambda g: g.priority)
        while possible:
            current = self.select_next_goal(possible)
            possible = [g for g in possible if g is not current]
            goal_state = current.goal_state.clone()
            if not self.settings["using_dynamic_actions"] and not self._goal_reachable_precheck(goal_state):
                continue
            leaf = self.astar.run(
                GoapNode(self, goal_state),
                max_iterations=self.settings["max_iterations"],
                early_exit=self.settings["planning_early_exit"])
            if leaf is None:
                continue
            result = leaf.calculate_path()
            if len(result) == 0:
                # 目标已被世界状态满足 ⇒ 空计划，官方视为「没有计划」
                continue
            current.plan = result
            return current
        return None

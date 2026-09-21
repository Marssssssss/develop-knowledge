"""BehaviorTree.CPP 控制节点 / 装饰器执行语义的 Python 转写。

行号级对应的官方源码（逐行实读后转写）：

- ``src/controls/sequence_node.cpp``     SequenceNode::tick / halt
- ``src/controls/fallback_node.cpp``     FallbackNode::tick / halt
- ``src/controls/reactive_sequence.cpp`` ReactiveSequence::tick / halt
- ``src/controls/parallel_node.cpp``     ParallelNode::tick / successThreshold / failureThreshold
- ``src/decorators/inverter_node.cpp``   InverterNode::tick
- ``src/decorators/repeat_node.cpp``     RepeatNode::tick / halt
- ``src/control_node.cpp``               resetChildren / haltChild
- ``include/behaviortree_cpp/basic_types.h``  NodeStatus 枚举与 isStatusActive

语言差异显式落地：
- C++ 的 ``enum class NodeStatus`` → Python 模块级常量（取值 0..4 与官方一致）；
- C++ 抛 ``LogicError`` → Python 抛 ``LogicError``（本模块自定义）；
- ``size_t`` 下标不会下溢 → Python 里 ``current_child_idx_`` 同样只在命中分支自增；
- 官方 ``ReactiveSequence::throw_if_multiple_running`` 是 **static** 且默认 false，
  这里建模为模块级开关 ``REACTIVE_THROW_IF_MULTIPLE_RUNNING``。
"""

IDLE = 0
RUNNING = 1
SUCCESS = 2
FAILURE = 3
SKIPPED = 4

STATUS_NAME = {IDLE: "IDLE", RUNNING: "RUNNING", SUCCESS: "SUCCESS",
               FAILURE: "FAILURE", SKIPPED: "SKIPPED"}

# ReactiveSequence::throw_if_multiple_running —— 官方默认 false
REACTIVE_THROW_IF_MULTIPLE_RUNNING = False


class LogicError(Exception):
    """对应 BT::LogicError。"""


def is_status_active(status):
    # basic_types.h: status != IDLE && status != SKIPPED
    return status != IDLE and status != SKIPPED


def is_status_completed(status):
    return status == SUCCESS or status == FAILURE


class TreeNode:
    """TreeNode 的最小语义：status + executeTick + haltNode。"""

    def __init__(self, name, children=()):
        self.name = name
        self.status = IDLE
        self.children = list(children)

    # --- 对应 TreeNode::executeTick() ---
    def execute_tick(self):
        new_status = self.tick()
        self.status = new_status
        return new_status

    def tick(self):
        raise NotImplementedError

    def halt(self):
        pass

    def reset_status(self):
        self.status = IDLE

    # ControlNode::haltChild：RUNNING 才 haltNode，随后无条件 resetStatus
    def _halt_child(self, i):
        child = self.children[i]
        if child.status == RUNNING:
            child.halt()
        child.reset_status()

    def reset_children(self):
        for i in range(len(self.children)):
            self._halt_child(i)

    def halt_node(self):
        self.halt()


class Leaf(TreeNode):
    """按剧本返回状态的叶子：每 tick 依次弹出一个状态。"""

    def __init__(self, name, script, on_halt=None):
        super().__init__(name)
        self.script = list(script)
        self.tick_count = 0
        self.halt_count = 0
        self.on_halt = on_halt

    def tick(self):
        self.tick_count += 1
        if not self.script:
            # 剧本耗尽后保持最后一个状态；空剧本视为 IDLE（用于触发 LogicError）
            return IDLE
        if len(self.script) == 1:
            return self.script[0]
        return self.script.pop(0)

    def halt(self):
        self.halt_count += 1
        if self.on_halt:
            self.on_halt()


class SkippedLeaf(TreeNode):
    """恒定返回 SKIPPED 的叶子，用于验证 SKIPPED 传播。"""

    def __init__(self, name="Skipped"):
        super().__init__(name)

    def tick(self):
        return SKIPPED


class SequenceNode(TreeNode):
    """sequence_node.cpp 的逐行转写。"""

    def __init__(self, name, children=(), make_async=False):
        super().__init__(name, children)
        self.current_child_idx_ = 0
        self.skipped_count_ = 0
        self.asynch_ = make_async

    def halt(self):
        self.current_child_idx_ = 0
        self.skipped_count_ = 0
        for i in range(len(self.children)):
            self._halt_child(i)

    def tick(self):
        children_count = len(self.children)
        if not is_status_active(self.status):
            self.skipped_count_ = 0
        self.status = RUNNING

        while self.current_child_idx_ < children_count:
            child = self.children[self.current_child_idx_]
            prev_status = child.status
            child_status = child.execute_tick()

            if child_status == RUNNING:
                return RUNNING
            if child_status == FAILURE:
                self.reset_children()
                self.current_child_idx_ = 0
                return child_status
            if child_status == SUCCESS:
                self.current_child_idx_ += 1
                # async 分支：本 demo 默认 asynch_=False，不进入
                if self.asynch_ and prev_status == IDLE and self.current_child_idx_ < children_count:
                    return RUNNING
            elif child_status == SKIPPED:
                self.current_child_idx_ += 1
                self.skipped_count_ += 1
            elif child_status == IDLE:
                raise LogicError("[%s]: A children should not return IDLE" % self.name)

        all_children_skipped = (self.skipped_count_ == children_count)
        if self.current_child_idx_ == children_count:
            self.reset_children()
            self.current_child_idx_ = 0
            self.skipped_count_ = 0
        return SKIPPED if all_children_skipped else SUCCESS


class FallbackNode(TreeNode):
    """fallback_node.cpp 的逐行转写。"""

    def __init__(self, name, children=(), make_async=False):
        super().__init__(name, children)
        self.current_child_idx_ = 0
        self.skipped_count_ = 0
        self.asynch_ = make_async

    def halt(self):
        self.current_child_idx_ = 0
        self.skipped_count_ = 0
        for i in range(len(self.children)):
            self._halt_child(i)

    def tick(self):
        children_count = len(self.children)
        if not is_status_active(self.status):
            self.skipped_count_ = 0
        self.status = RUNNING

        while self.current_child_idx_ < children_count:
            child = self.children[self.current_child_idx_]
            prev_status = child.status
            child_status = child.execute_tick()

            if child_status == RUNNING:
                return child_status
            if child_status == SUCCESS:
                self.reset_children()
                self.current_child_idx_ = 0
                return child_status
            if child_status == FAILURE:
                self.current_child_idx_ += 1
                if self.asynch_ and prev_status == IDLE and self.current_child_idx_ < children_count:
                    return RUNNING
            elif child_status == SKIPPED:
                self.current_child_idx_ += 1
                self.skipped_count_ += 1
            elif child_status == IDLE:
                raise LogicError("[%s]: A children should not return IDLE" % self.name)

        all_children_skipped = (self.skipped_count_ == children_count)
        if self.current_child_idx_ == children_count:
            self.reset_children()
            self.current_child_idx_ = 0
            self.skipped_count_ = 0
        return SKIPPED if all_children_skipped else FAILURE


class ReactiveSequence(TreeNode):
    """reactive_sequence.cpp：每 tick 都从头扫，命中 RUNNING 就 halt 其余兄弟。"""

    def __init__(self, name, children=()):
        super().__init__(name, children)
        self.running_child_ = -1

    def halt(self):
        self.running_child_ = -1
        for i in range(len(self.children)):
            self._halt_child(i)

    def tick(self):
        all_skipped = True
        if self.status == IDLE:
            self.running_child_ = -1
        self.status = RUNNING

        for index in range(len(self.children)):
            child_status = self.children[index].execute_tick()
            all_skipped = all_skipped and (child_status == SKIPPED)

            if child_status == RUNNING:
                for i in range(len(self.children)):
                    if i != index:
                        self._halt_child(i)
                if self.running_child_ == -1:
                    self.running_child_ = index
                elif REACTIVE_THROW_IF_MULTIPLE_RUNNING and self.running_child_ != index:
                    raise LogicError("[ReactiveSequence]: only a single child can return RUNNING.")
                return RUNNING
            if child_status == FAILURE:
                self.reset_children()
                return FAILURE
            if child_status == SUCCESS:
                pass
            elif child_status == SKIPPED:
                self._halt_child(index)
            elif child_status == IDLE:
                raise LogicError("[%s]: A children should not return IDLE" % self.name)

        self.reset_children()
        return SKIPPED if all_skipped else SUCCESS


class ParallelNode(TreeNode):
    """parallel_node.cpp：阈值语义 + completed_list_ 去重 + 循环内提前结算。"""

    def __init__(self, name, children=(), success_threshold=-1, failure_threshold=1):
        super().__init__(name, children)
        self.success_threshold_ = success_threshold
        self.failure_threshold_ = failure_threshold
        self.completed_list_ = set()
        self.success_count_ = 0
        self.failure_count_ = 0

    def clear(self):
        self.completed_list_.clear()
        self.success_count_ = 0
        self.failure_count_ = 0

    def halt(self):
        self.clear()
        for i in range(len(self.children)):
            self._halt_child(i)

    def success_threshold(self):
        if self.success_threshold_ < 0:
            return max(len(self.children) + self.success_threshold_ + 1, 0)
        return self.success_threshold_

    def failure_threshold(self):
        if self.failure_threshold_ < 0:
            return max(len(self.children) + self.failure_threshold_ + 1, 0)
        return self.failure_threshold_

    def tick(self):
        children_count = len(self.children)
        required_success = self.success_threshold()
        if children_count < required_success:
            raise LogicError("Number of children is less than threshold. Can never succeed.")
        if children_count < self.failure_threshold():
            raise LogicError("Number of children is less than threshold. Can never fail.")

        self.status = RUNNING
        skipped_count = 0

        for i in range(children_count):
            if i not in self.completed_list_:
                child_status = self.children[i].execute_tick()
                if child_status == SKIPPED:
                    skipped_count += 1
                elif child_status == SUCCESS:
                    self.completed_list_.add(i)
                    self.success_count_ += 1
                elif child_status == FAILURE:
                    self.completed_list_.add(i)
                    self.failure_count_ += 1
                elif child_status == RUNNING:
                    pass
                elif child_status == IDLE:
                    raise LogicError("[%s]: A children should not return IDLE" % self.name)

            required_success = self.success_threshold()
            # 官方：success_threshold_ < 0 时，SKIPPED 也算进成功票
            if (self.success_count_ >= required_success or
                    (self.success_threshold_ < 0 and
                     (self.success_count_ + skipped_count) >= required_success)):
                self.clear()
                self.reset_children()
                return SUCCESS

            if ((children_count - self.failure_count_) < required_success or
                    self.failure_count_ == self.failure_threshold()):
                self.clear()
                self.reset_children()
                return FAILURE

        return SKIPPED if skipped_count == children_count else RUNNING


class InverterNode(TreeNode):
    """inverter_node.cpp：SUCCESS/FAILURE 互换，RUNNING/SKIPPED 原样透传。"""

    def __init__(self, name, child):
        super().__init__(name, [child])

    @property
    def child(self):
        return self.children[0]

    def _reset_child(self):
        if self.child.status == RUNNING:
            self.child.halt()
        self.child.reset_status()

    def tick(self):
        self.status = RUNNING
        child_status = self.child.execute_tick()
        if child_status == SUCCESS:
            self._reset_child()
            return FAILURE
        if child_status == FAILURE:
            self._reset_child()
            return SUCCESS
        if child_status in (RUNNING, SKIPPED):
            return child_status
        raise LogicError("[%s]: A children should not return IDLE" % self.name)


class RepeatNode(TreeNode):
    """repeat_node.cpp：N 次成功才算 SUCCESS；失败立即 FAILURE 且计数清零。"""

    def __init__(self, name, child, num_cycles):
        super().__init__(name, [child])
        self.num_cycles_ = num_cycles
        self.repeat_count_ = 0

    @property
    def child(self):
        return self.children[0]

    def _reset_child(self):
        if self.child.status == RUNNING:
            self.child.halt()
        self.child.reset_status()

    def halt(self):
        self.repeat_count_ = 0
        self._reset_child()

    def tick(self):
        self.status = RUNNING
        do_loop = True
        while do_loop:
            prev_status = self.child.status
            child_status = self.child.execute_tick()
            if child_status == SUCCESS:
                self.repeat_count_ += 1
                do_loop = self.repeat_count_ < self.num_cycles_ or self.num_cycles_ == -1
                self._reset_child()
            elif child_status == FAILURE:
                self.repeat_count_ = 0
                self._reset_child()
                return FAILURE
            elif child_status == RUNNING:
                return RUNNING
            elif child_status == SKIPPED:
                self._reset_child()   # 官方注释：不重置 repeat_count_
                return SKIPPED
            elif child_status == IDLE:
                raise LogicError("[%s]: A children should not return IDLE" % self.name)
        self.repeat_count_ = 0
        return SUCCESS

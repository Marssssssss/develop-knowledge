"""SCXML（W3C Recommendation）层级状态机语义的 Python 转写。

逐条实读后转写的规范内容（https://www.w3.org/TR/scxml/ 的 Appendix D 算法）：

- ``selectTransitions``            原子状态按 document order，命中第一个匹配；否则沿祖先链向上找
- ``removeConflictingTransitions`` exit set 相交即冲突；后代优先，否则先出现的（document order）赢
- ``computeExitSet`` / ``exitStates``  LCCA 域内的后代全部退出，按 exitOrder（后代先于祖先）
- ``computeEntrySet`` / ``enterStates`` addDescendant → addAncestor，按 entryOrder（祖先先于后代）
- ``getTransitionDomain``           internal 且目标是源的后代 → 源；否则 findLCCA
- ``findLCCA``                      Least Common Compound Ancestor
- 历史状态                          exit 时记录 shallow（直接子）/ deep（原子后代）

语言差异显式落地：
- 规范的 ``OrderedSet`` → Python 用 list 保序 + 判重（**顺序本身就是语义**，不能换成 set）；
- 规范里 targetless transition 的 ``t.target`` 为空 → Python 用空 list；
- 规范 ``lambda`` 形式的判据（如 ``isAtomicState(s0) and isDescendant(s0, s)``）在 Python 里就是普通表达式。
"""

SCXML = "<scxml>"


class State:
    def __init__(self, sid, parent=None, kind="compound", initial=(), history=None):
        self.id = sid
        self.parent = parent
        self.kind = kind              # "compound" | "parallel" | "atomic" | "final"
        self.initial = list(initial)  # 默认进入的子状态 id
        self.history = history        # ("shallow"|"deep", hid, default_target)
        self.transitions = []
        self.children = []

    def is_atomic(self):
        if self.kind in ("atomic", "final"):
            return True
        if self.kind in ("parallel", "history"):
            return False
        return not self.children

    def is_parallel(self):
        return self.kind == "parallel"

    def is_compound(self):
        return self.kind == "compound" and not self.is_atomic()


class Transition:
    def __init__(self, source, event=None, targets=(), kind="external", order=0, cond=None):
        self.source = source
        self.event = event
        self.targets = list(targets)
        self.kind = kind
        self.order = order
        self.cond = cond

    def is_targetless(self):
        return not self.targets


class Machine:
    def __init__(self, states, doc_order):
        self.states = states          # id -> State
        self.doc_order = doc_order    # id -> 文档序（先序遍历序号）
        self.configuration = set()
        self.history_value = {}
        self.log = {"exit": [], "entry": [], "transition": []}

    # -------------------------------------------------- 关系谓词
    def ancestors(self, sid):
        out = []
        cur = self.states[sid].parent
        while cur is not None:
            out.append(cur)
            cur = self.states[cur].parent
        return out

    def proper_ancestors(self, sid, stop=None):
        out = []
        for a in self.ancestors(sid):
            if a == stop:
                break
            out.append(a)
        return out

    def is_descendant(self, sid, anc):
        # 规范：isDescendant 只认「子 / 孙」，**不包含相等**
        return anc in self.ancestors(sid)

    def has_child(self, anc, sid):
        """sid 是否有后代已经在 states_to_enter 里（isDescendant 的任一存在版本）。"""
        return any(self.is_descendant(x, anc) for x in sid)

    def in_doc_order(self, ids):
        return sorted(ids, key=lambda s: self.doc_order[s])

    def entry_order(self, ids):
        # 祖先先于后代；等价于 document order
        return self.in_doc_order(ids)

    def exit_order(self, ids):
        # 后代先于祖先；等价于 reverse document order
        return list(reversed(self.in_doc_order(ids)))

    def atomic_states(self):
        return [s for s in self.configuration if self.states[s].is_atomic()]

    # -------------------------------------------------- selectTransitions
    def select_transitions(self, event):
        enabled = []
        for state in self.in_doc_order(self.atomic_states()):
            for s in [state] + self.proper_ancestors(state):
                found = None
                for t in sorted(self.states[s].transitions, key=lambda x: x.order):
                    if t.event == event and (t.cond is None or t.cond()):
                        found = t
                        break
                if found is not None:
                    enabled.append(found)
                    break
        return self.remove_conflicting(enabled)

    def compute_exit_set(self, transitions):
        out = []
        for t in transitions:
            if t.is_targetless():
                continue
            domain = self.get_transition_domain(t)
            for s in self.configuration:
                if self.is_descendant(s, domain) and s not in out:
                    out.append(s)
        return out

    def remove_conflicting(self, enabled):
        filtered = []
        for t1 in enabled:
            preempted = False
            to_remove = []
            for t2 in filtered:
                if set(self.compute_exit_set([t1])) & set(self.compute_exit_set([t2])):
                    if self.is_descendant(t1.source, t2.source):
                        to_remove.append(t2)
                    else:
                        preempted = True
                        break
            if not preempted:
                for t3 in to_remove:
                    filtered.remove(t3)
                filtered.append(t1)
        return filtered

    # -------------------------------------------------- 域与 LCCA
    def get_effective_target_states(self, t):
        out = []
        for s in t.targets:
            st = self.states[s]
            if st.kind == "history":
                if self.history_value.get(s):
                    for x in self.history_value[s]:
                        if x not in out:
                            out.append(x)
                else:
                    for x in self.get_effective_target_states(st.transitions[0]):
                        if x not in out:
                            out.append(x)
            elif s not in out:
                out.append(s)
        return out

    def get_transition_domain(self, t):
        targets = self.get_effective_target_states(t)
        if not targets:
            return None
        src = self.states[t.source]
        if (t.kind == "internal" and src.is_compound()
                and all(self.is_descendant(x, t.source) for x in targets)):
            return t.source
        return self.find_lcca([t.source] + targets)

    def find_lcca(self, state_list):
        """Least Common Compound Ancestor：是所有 state 的**真**祖先，且没有后代也满足。"""
        best, best_depth = SCXML, -1
        for s in self.doc_order:
            if s == SCXML or s in state_list:
                continue                       # 必须是 proper ancestor
            if all(self.is_descendant(x, s) for x in state_list):
                depth = len(self.ancestors(s))
                if depth > best_depth:
                    best, best_depth = s, depth
        return best

    # -------------------------------------------------- 进入 / 退出
    def add_descendant_states_to_enter(self, sid, to_enter, for_default, hist_content):
        st = self.states[sid]
        if st.kind == "history":
            values = self.history_value.get(sid) or []
            if values:
                for x in values:
                    self.add_descendant_states_to_enter(x, to_enter, for_default, hist_content)
                for x in values:
                    self.add_ancestor_states_to_enter(x, st.parent, to_enter, for_default, hist_content)
            else:
                hist_content[st.parent] = st.transitions[0].targets
                for x in st.transitions[0].targets:
                    self.add_descendant_states_to_enter(x, to_enter, for_default, hist_content)
                for x in st.transitions[0].targets:
                    self.add_ancestor_states_to_enter(x, st.parent, to_enter, for_default, hist_content)
            return
        if sid not in to_enter:
            to_enter.append(sid)
        if st.is_parallel():
            for child in st.children:
                if not any(self.is_descendant(x, child) for x in to_enter):
                    self.add_descendant_states_to_enter(child, to_enter, for_default, hist_content)
        elif st.is_compound():
            for_default.add(sid)
            for child in st.initial:
                self.add_descendant_states_to_enter(child, to_enter, for_default, hist_content)
            for child in st.initial:
                self.add_ancestor_states_to_enter(child, sid, to_enter, for_default, hist_content)

    def add_ancestor_states_to_enter(self, sid, ancestor, to_enter, for_default, hist_content):
        for anc in self.proper_ancestors(sid, ancestor):
            if anc not in to_enter:
                to_enter.append(anc)
            if self.states[anc].is_parallel():
                for child in self.states[anc].children:
                    if not any(self.is_descendant(x, child) for x in to_enter):
                        self.add_descendant_states_to_enter(child, to_enter, for_default, hist_content)

    def compute_entry_set(self, transitions):
        to_enter, for_default, hist_content = [], set(), {}
        for t in transitions:
            for s in t.targets:
                self.add_descendant_states_to_enter(s, to_enter, for_default, hist_content)
            ancestor = self.get_transition_domain(t)
            for s in self.get_effective_target_states(t):
                self.add_ancestor_states_to_enter(s, ancestor, to_enter, for_default, hist_content)
        return to_enter, for_default, hist_content

    # -------------------------------------------------- 一个 macrostep
    def start(self):
        root = self.states[SCXML]
        to_enter, for_default, hist = self.compute_entry_set(
            [Transition(SCXML, targets=list(root.initial), order=0)])
        self._enter(to_enter, for_default, hist)

    def _enter(self, to_enter, for_default, hist):
        for s in self.entry_order(to_enter):
            if s == SCXML:
                continue   # <scxml> 是容器，不算进 configuration（见 README 注意事项）
            self.configuration.add(s)
            self.log["entry"].append(s)

    def _exit(self, states_to_exit):
        ordered = self.exit_order(states_to_exit)
        # 规范：先**统一**记录历史值（此时配置还没被改动），再逐个执行 onexit 并移出配置
        for s in ordered:
            st = self.states[s]
            if not st.history:
                continue
            kind, hid, _default = st.history
            if kind == "deep":
                value = [x for x in self.configuration
                         if self.states[x].is_atomic() and self.is_descendant(x, s)]
            else:
                value = [x for x in self.configuration if self.states[x].parent == s]
            self.history_value[hid] = value
        for s in ordered:
            self.log["exit"].append(s)
            self.configuration.discard(s)

    def fire(self, event):
        enabled = self.select_transitions(event)
        if not enabled:
            return False
        for t in enabled:
            self.log["transition"].append((t.source, t.targets))
        states_to_exit = self.compute_exit_set(enabled)
        self._exit(states_to_exit)
        to_enter, for_default, hist = self.compute_entry_set(enabled)
        self._enter(to_enter, for_default, hist)
        return True

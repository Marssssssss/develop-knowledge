'''因子与团树：连接树校准所需的数据结构（从 main.py 拆出，语义零改动）。'''

class Factor:
    """离散因子：vars 为有序变量名，table 以赋值元组为键。"""

    def __init__(self, variables, table):
        self.vars = list(variables)
        self.table = dict(table)

    def scope(self):
        return set(self.vars)

    def copy(self):
        return Factor(self.vars, self.table)

    def _align(self, other):
        idx = [other.vars.index(v) for v in self.vars if v in other.vars]
        return idx

    def product(self, other):
        vs = self.vars + [v for v in other.vars if v not in self.vars]
        nself, nother = len(self.vars), len(other.vars)
        table = {}
        pos = {v: i for i, v in enumerate(vs)}
        imap = [pos[v] for v in self.vars]
        jmap = [pos[v] for v in other.vars]
        for ka, va in self.table.items():
            for kb, vb in other.table.items():
                key = [None] * len(vs)
                okf = True
                for i, v in enumerate(self.vars):
                    key[imap[i]] = ka[i]
                for j, v in enumerate(other.vars):
                    if key[jmap[j]] is not None and key[jmap[j]] != kb[j]:
                        okf = False
                        break
                    key[jmap[j]] = kb[j]
                if okf:
                    table[tuple(key)] = va * vb
        return Factor(vs, table)

    def marginalize(self, drop):
        keep = [v for v in self.vars if v not in drop]
        kidx = [self.vars.index(v) for v in keep]
        table = {}
        for k, v in self.table.items():
            key = tuple(k[i] for i in kidx)
            table[key] = table.get(key, 0.0) + v
        return Factor(keep, table)

    def divide(self, other):
        idx = [self.vars.index(v) for v in other.vars]
        table = {}
        for k, v in self.table.items():
            key = tuple(k[i] for i in idx)
            table[k] = v / other.table[key]
        return Factor(self.vars, table)

    def normalize(self):
        z = sum(self.table.values())
        return Factor(self.vars, {k: v / z for k, v in self.table.items()})

    def isclose(self, other, tol=1e-9):
        if set(self.vars) != set(other.vars):
            return False
        idx = [self.vars.index(v) for v in other.vars]
        m = {tuple(k[i] for i in idx): v for k, v in self.table.items()}
        if set(m) != set(other.table):
            return False
        return all(abs(m[k] - other.table[k]) <= tol for k in m)


def build_junction_tree(cliques):
    """最大生成树：边权 = |Ci ∩ Cj|（经典连接树构造）。"""
    n = len(cliques)
    if n == 0:
        return []
    in_tree = [0]
    edges = []
    while len(in_tree) < n:
        best = None
        for i in in_tree:
            for j in range(n):
                if j in in_tree:
                    continue
                w = len(set(cliques[i]) & set(cliques[j]))
                if best is None or w > best[0]:
                    best = (w, i, j)
        if best is None:
            break
        edges.append((best[1], best[2], best[0]))
        in_tree.append(best[2])
    return edges


def check_running_intersection(cliques, edges):
    """RIP：任意两团的公共变量必须出现在它们之间路径上的每个团里。"""
    adj = {i: set() for i in range(len(cliques))}
    for i, j, _ in edges:
        adj[i].add(j)
        adj[j].add(i)
    for a in range(len(cliques)):
        for b in range(a + 1, len(cliques)):
            sep = set(cliques[a]) & set(cliques[b])
            if not sep:
                continue
            # 找 a→b 的路
            prev = {a: None}
            stack = [a]
            while stack:
                v = stack.pop()
                if v == b:
                    break
                for w in adj[v]:
                    if w not in prev:
                        prev[w] = v
                        stack.append(w)
            if b not in prev:
                return False
            path = []
            cur = b
            while cur is not None:
                path.append(cur)
                cur = prev[cur]
            for node in path:
                if not sep <= set(cliques[node]):
                    return False
    return True


class JunctionTree:
    """pgmpy BeliefPropagation 的 LS 校准转写。"""

    def __init__(self, cliques, potentials):
        self.cliques = [list(c) for c in cliques]
        self.potentials = potentials  # 与 cliques 等长的 Factor 列表
        self.edges = build_junction_tree(self.cliques)
        self.beliefs = None
        self.sepsets = None

    def calibrate(self, divide_out=True):
        self.beliefs = [f.copy() for f in self.potentials]
        self.sepsets = {}
        adj = {i: [] for i in range(len(self.cliques))}
        for i, j, _ in self.edges:
            adj[i].append(j)
            adj[j].append(i)
        root = 0
        order = []
        seen = {root}
        stack = [root]
        while stack:
            v = stack.pop(0)
            for w in adj[v]:
                if w not in seen:
                    seen.add(w)
                    order.append((v, w))
                    stack.append(w)
        # collect：叶 → 根
        for parent, child in reversed(order):
            self._update(child, parent, divide_out)
        # distribute：根 → 叶
        for parent, child in order:
            self._update(parent, child, divide_out)

    def _update(self, sender, receiver, divide_out):
        sepset = sorted(set(self.cliques[sender]) & set(self.cliques[receiver]))
        drop = [v for v in self.cliques[sender] if v not in sepset]
        sigma = self.beliefs[sender].marginalize(drop)
        # pgmpy 用 frozenset(edge) 作键：一条边只存一份 sepset 信念，
        # collect 阶段存下的 μ 会在 distribute 阶段被「除掉」，避免重复计入
        key = frozenset((sender, receiver))
        if divide_out and key in self.sepsets:
            self.beliefs[receiver] = self.beliefs[receiver].product(
                sigma.divide(self.sepsets[key])
            )
        else:
            self.beliefs[receiver] = self.beliefs[receiver].product(sigma)
        self.sepsets[key] = sigma

    def is_converged(self):
        for i, j, _ in self.edges:
            sepset = sorted(set(self.cliques[i]) & set(self.cliques[j]))
            mi = self.beliefs[i].marginalize(
                [v for v in self.cliques[i] if v not in sepset]
            )
            mj = self.beliefs[j].marginalize(
                [v for v in self.cliques[j] if v not in sepset]
            )
            mu = self.sepsets[frozenset((i, j))]
            if not (mi.isclose(mj) and mi.isclose(mu)):
                return False
        return True

    def marginal(self, var):
        for c, b in zip(self.cliques, self.beliefs):
            if var in c:
                return b.marginalize([v for v in c if v != var]).normalize()
        return None


def brute_force_marginals(variables, cardinality, potentials):
    """暴力枚举联合分布求边缘（作为 JT 结果的独立参照）。"""
    domains = [list(range(cardinality[v])) for v in variables]
    joint = {}
    for assign in _product(domains):
        p = 1.0
        for f in potentials:
            key = tuple(assign[variables.index(v)] for v in f.vars)
            p *= f.table[key]
        joint[assign] = p
    z = sum(joint.values())
    out = {}
    for i, v in enumerate(variables):
        table = {}
        for assign, p in joint.items():
            table[(assign[i],)] = table.get((assign[i],), 0.0) + p / z
        out[v] = Factor([v], table)
    return out


def _product(lists):
    out = [[]]
    for lst in lists:
        out = [a + [b] for a in out for b in lst]
    return [tuple(x) for x in out]


"""HNSW 分层可导航小世界图 —— 按 hnswlib 官方 hnswalg.h 转写的核心机制。

转写对照（官方源码 nmslib/hnswlib @ master, hnswlib/hnswalg.h）：
  - `mult_ = 1 / log(1.0 * M_)`、`revSize_ = 1.0 / mult_`
  - `maxM_ = M_`、`maxM0_ = M_ * 2`、`Mcurmax = level ? maxM_ : maxM0_`
  - `searchBaseLayer` 的双堆 + 打断条件 + `lowerBound = top_candidates.top().first`
  - `getNeighborsByHeuristic2` 的多样性剪枝
  - `mutuallyConnectNewElement` 的回连与满载收缩
  - `searchKnn` 在 >0 层是纯贪心（ef=1），底层用 `ef = max(ef_, k)`

原子构件（堆 / 距离 / 层级 / 随机源）在 `hnsw_prim.py`。
"""

import math

from hnsw_prim import (  # noqa: E402
    MaxHeapOnDist, MinHeapOnDist, get_random_level, l2sqr,
)


class HierarchicalNSW:
    """去掉锁、删除标记、SIMD 与内存池后的 HNSW 语义骨架。"""

    def __init__(self, dim, M=16, ef_construction=200, ef=10, uniform_source=None):
        self.dim = dim
        self.M_ = M
        self.maxM_ = M
        self.maxM0_ = M * 2
        self.mult_ = 1.0 / math.log(1.0 * M)
        self.revSize_ = 1.0 / self.mult_
        self.ef_construction_ = ef_construction
        self.ef_ = ef

        self.vectors = []          # internal_id -> vector
        self.levels = []           # internal_id -> 最高层号
        self.links = []            # internal_id -> {layer: [neighbour ids]}
        self.enterpoint = None
        self.maxlevel = -1

        # 确定性均匀源：每生成一层消耗一个 U
        self._uniform = list(uniform_source) if uniform_source is not None else None
        self._u_pos = 0

        self.hops = 0
        self.dist_computations = 0
        self.last_broke_early = False
        self._level_fn = None

    def set_level_fn(self, fn):
        """注入层级生成函数（默认 `getRandomLevel`），便于确定性测试。"""
        self._level_fn = fn

    def _next_level(self):
        u = self._uniform[self._u_pos % len(self._uniform)]
        self._u_pos += 1
        fn = self._level_fn or get_random_level
        return fn(u, self.mult_)

    def m_cur_max(self, level):
        """`size_t Mcurmax = level ? maxM_ : maxM0_;`"""
        return self.maxM0_ if level == 0 else self.maxM_

    def _dist(self, a, b):
        self.dist_computations += 1
        return l2sqr(a, b)

    def neighbors(self, node, level):
        return self.links[node].get(level, [])

    def _set_links(self, node, level, ids):
        self.links[node][level] = list(ids)

    # -- 插入 -----------------------------------------------------
    def add_point(self, vec):
        """对应 `addPoint`：逐层下降 + 双向连接。"""
        cur_c = len(self.vectors)
        self.vectors.append(list(vec))
        self.links.append({})
        curlevel = self._next_level()
        self.levels.append(curlevel)

        if self.enterpoint is None:
            # 官方：第一个元素「Do nothing」，直接登记为入口
            self.enterpoint = 0
            self.maxlevel = curlevel
            return cur_c

        maxlevelcopy = self.maxlevel
        ep = self.enterpoint
        next_closest = ep

        # 高层（level > curlevel）：纯贪心下降，等价于 ef=1
        for level in range(maxlevelcopy, curlevel, -1):
            ep = self._greedy_descend(vec, ep, level)

        for level in range(min(curlevel, maxlevelcopy), -1, -1):
            cands = self.search_layer(vec, ep, self.ef_construction_, level)
            selected = self._mutually_connect(cur_c, cands, level)
            # 官方 `next_closest_entry_point = selectedNeighbors.back()`：
            # selectedNeighbors 由大顶堆逐个 pop（远→近），back() 即最近者
            if selected:
                next_closest = min(selected)[1]
            ep = next_closest

        # 官方：入口点**只在** curlevel 超过当前最大层时换成新点
        if curlevel > maxlevelcopy:
            self.enterpoint = cur_c
            self.maxlevel = curlevel
        return cur_c

    def _greedy_descend(self, vec, ep, level):
        """`searchKnn` 里 level>0 的 `while (changed)` 循环：只朝最近邻移动。"""
        curr, curdist = ep, self._dist(vec, self.vectors[ep])
        changed = True
        while changed:
            changed = False
            self.hops += 1
            for cand in self.neighbors(curr, level):
                d = self._dist(vec, self.vectors[cand])
                if d < curdist:
                    curdist, curr, changed = d, cand, True
        return curr

    # -- 搜索 -----------------------------------------------------
    def search_layer(self, q, ep, ef, level):
        """`searchBaseLayer` 的语义转写。返回 MaxHeapOnDist（大顶堆）。"""
        visited = {ep}
        top = MaxHeapOnDist()
        cand = MinHeapOnDist()

        d = self._dist(q, self.vectors[ep])
        top.push(d, ep)
        lower_bound = d
        cand.push(d, ep)

        self.last_broke_early = False
        while cand.size():
            cdist, cnode = cand.top()
            # `if ((-curr_el_pair.first) > lowerBound && top_candidates.size() == ef_construction_) break;`
            if cdist > lower_bound and top.size() == ef:
                self.last_broke_early = True
                break
            cand.pop()
            for nb in self.neighbors(cnode, level):
                if nb in visited:
                    continue
                visited.add(nb)
                d1 = self._dist(q, self.vectors[nb])
                if top.size() < ef or lower_bound > d1:
                    cand.push(d1, nb)
                    top.push(d1, nb)
                    if top.size() > ef:
                        top.pop()
                    if not top.empty():
                        lower_bound = top.top()[0]
        return top

    def search_knn(self, q, k, ef=None):
        """`searchKnn`：高层贪心下降 + 底层 `ef = max(ef_, k)`。"""
        if not self.vectors:
            return []
        eff_ef = max(self.ef_ if ef is None else ef, k)
        curr = self.enterpoint
        curdist = self._dist(q, self.vectors[curr])
        for level in range(self.maxlevel, 0, -1):
            changed = True
            while changed:
                changed = False
                self.hops += 1
                for c in self.neighbors(curr, level):
                    d = self._dist(q, self.vectors[c])
                    if d < curdist:
                        curdist, curr, changed = d, c, True
        top = self.search_layer(q, curr, eff_ef, 0)
        # 官方 `while (top_candidates.size() > k) pop()` —— 淘汰最远，保留最近 k 个
        items = top.items()
        return items[:k] if len(items) > k else items

    # -- 连接 -----------------------------------------------------
    def _mutually_connect(self, cur_c, top_candidates, level):
        """`mutuallyConnectNewElement`：先给自己挑 M 个，再回连并按需收缩。"""
        Mcurmax = self.m_cur_max(level)
        selected = get_neighbors_by_heuristic2(top_candidates, self.M_, self.vectors)
        if not selected:
            self._set_links(cur_c, level, [])
            return []
        self._set_links(cur_c, level, [n for _, n in selected])
        for d, nb in selected:
            links = list(self.neighbors(nb, level))
            if len(links) < Mcurmax:
                self._set_links(nb, level, links + [cur_c])
            else:
                # 满了：把新点并进去再跑一次启发式（以 nb 为「查询」），容量用 Mcurmax
                heap = MaxHeapOnDist()
                for x in links:
                    heap.push(self._dist(self.vectors[nb], self.vectors[x]), x)
                heap.push(d, cur_c)
                pruned = get_neighbors_by_heuristic2(heap, Mcurmax, self.vectors)
                self._set_links(nb, level, [x for _, x in pruned])
        return selected


def get_neighbors_by_heuristic2(top_candidates, M, vectors):
    """`getNeighborsByHeuristic2` 的转写。

    判据：候选 c 被丢弃，当且仅当存在**已入选**的 s 满足 `d(s,c) < d(q,c)`，
    其中 `d(q,c)` 取候选在 top_candidates 里记录的距离。
    """
    items = top_candidates.items()
    if len(items) < M:
        return list(items)          # 官方 `if (top_candidates.size() < M) return;`

    queue = sorted(items)           # 按到查询的距离升序，近 → 远
    chosen = []
    for dist_to_query, cand in queue:
        if len(chosen) >= M:
            break
        good = True
        for _, s in chosen:
            if l2sqr(vectors[s], vectors[cand]) < dist_to_query:
                good = False
                break
        if good:
            chosen.append((dist_to_query, cand))
    return chosen

"""Redis ZSET 的 skiplist 编码：层高生成、span 维护、rank 查询与 listpack 转换阈值。

事实来源（本轮实读，非记忆）：
  - redis/redis unstable `src/server.h` 678-679：ZSKIPLIST_MAXLEVEL 32 / ZSKIPLIST_P 0.25
  - redis/redis unstable `src/t_zset.c` 75-95：zslGet/Set/Incr/DecrNodeSpanAtLevel
    —— **level[0] 的 span 字段被改存 NodeInfo(levels+sdsoffset)，写操作在 level 0 上是空操作**，
    读操作返回「level[0].forward ? 1 : 0」（尾节点为 0）。这是本 demo 最容易写反的一处。
  - redis/redis unstable `src/t_zset.c` 254-259：zslRandomLevel
  - redis/redis unstable `src/t_zset.c` 265-330：zslInsertNode
  - redis/redis unstable `src/t_zset.c` 345-364：zslUnlinkNode + 顶层回收
  - redis/redis unstable `src/t_zset.c` 441-453：zslIsInRange 的两个提前判空
  - redis/redis unstable `src/t_zset.c` 645-660：zslGetRank（1-based，找不到返回 0）
  - redis/redis unstable `src/t_zset.c` 672-687：zslGetRankByNode
  - redis/redis unstable `src/t_zset.c` 1405-1432：zsetTypeCreate / zsetTypeMaybeConvert
  - redis/redis unstable `src/t_zset.c` 1523-1531：zsetConvertToListpackIfNeeded
  - redis/redis unstable `src/t_zset.c` 1650-1665：ZADD 的 listpack 转换判定
  - redis/redis unstable `redis.conf` 2358-2359 与 `src/config.c` 3650/3654：默认 128 / 64
"""

ZSKIPLIST_MAXLEVEL = 32
ZSKIPLIST_P = 0.25

# glibc random() 的上界；unstable 分支的 zslRandomLevel 用的是 random() 全值域
RAND_MAX = 2147483647
RANDOM_THRESHOLD = ZSKIPLIST_P * RAND_MAX  # 536870911.75

LISTPACK = "listpack"
SKIPLIST = "skiplist"


# ---------------------------------------------------------------- 层高生成
def zsl_random_level(rand):
    """t_zset.c:254 复刻：threshold = P*RAND_MAX，while (random() < threshold) level++。

    rand 是可调用对象，每次返回 [0, RAND_MAX] 内的整数（自检用确定性源钉死，避免「通过只是运气」）。
    """
    level = 1
    while rand() < RANDOM_THRESHOLD:
        level += 1
    return level if level < ZSKIPLIST_MAXLEVEL else ZSKIPLIST_MAXLEVEL


# ---------------------------------------------------------------- 节点
class Level:
    __slots__ = ("forward", "span")

    def __init__(self, forward=None, span=0):
        self.forward = forward
        self.span = span


class Node:
    __slots__ = ("score", "ele", "backward", "level")

    def __init__(self, levels, score, ele):
        self.score = score
        self.ele = ele
        self.backward = None
        self.level = [Level() for _ in range(levels)]


def compare_with_node(score, ele, node):
    """t_zset.c:116：NULL 视为 +infinity，先比 score 再比 ele 字典序。"""
    if node is None:
        return -1
    if score < node.score:
        return -1
    if score > node.score:
        return 1
    if ele < node.ele:
        return -1
    if ele > node.ele:
        return 1
    return 0


def get_span(node, i):
    """t_zset.c:75：level 0 的 span 是 NodeInfo，真实 span 恒为 1（尾节点 0）。"""
    if i > 0:
        return node.level[i].span
    return 1 if node.level[0].forward else 0


def set_span(node, i, value):
    """t_zset.c:83：level 0 上什么也不做。"""
    if i > 0:
        node.level[i].span = value


def incr_span(node, i, delta):
    """t_zset.c:89 / 95。"""
    if i > 0:
        node.level[i].span += delta


class ZSkipList:
    """zslCreate() 后 level=1、length=0，header 固定占 MAXLEVEL 个层槽。"""

    def __init__(self):
        self.header = Node(ZSKIPLIST_MAXLEVEL, 0.0, None)
        self.tail = None
        self.length = 0
        self.level = 1

    # -------------------------------------------------------------- 插入
    def insert(self, score, ele, rand):
        level = zsl_random_level(rand)
        node = Node(level, score, ele)
        self.insert_node(node)
        return node

    def insert_node(self, node):
        """t_zset.c:265 zslInsertNode。"""
        update = [None] * ZSKIPLIST_MAXLEVEL
        rank = [0] * ZSKIPLIST_MAXLEVEL
        score, ele = node.score, node.ele
        level = len(node.level)

        x = self.header
        for i in range(self.level - 1, -1, -1):
            rank[i] = 0 if i == (self.level - 1) else rank[i + 1]
            while compare_with_node(score, ele, x.level[i].forward) > 0:
                rank[i] += get_span(x, i)
                x = x.level[i].forward
            update[i] = x

        if level > self.level:
            for i in range(self.level, level):
                rank[i] = 0
                update[i] = self.header
                set_span(update[i], i, self.length)
            self.level = level

        for i in range(level):
            node.level[i].forward = update[i].level[i].forward
            update[i].level[i].forward = node
            set_span(node, i, get_span(update[i], i) - (rank[0] - rank[i]))
            set_span(update[i], i, (rank[0] - rank[i]) + 1)

        for i in range(level, self.level):
            incr_span(update[i], i, 1)

        node.backward = None if update[0] is self.header else update[0]
        if node.level[0].forward:
            node.level[0].forward.backward = node
        else:
            self.tail = node

        self.length += 1

    # -------------------------------------------------------------- 删除
    def delete(self, score, ele):
        """t_zset.c:371：update[] 用「严格大于」收集，再 zslUnlinkNode。"""
        update = [None] * ZSKIPLIST_MAXLEVEL
        x = self.header
        for i in range(self.level - 1, -1, -1):
            while compare_with_node(score, ele, x.level[i].forward) > 0:
                x = x.level[i].forward
            update[i] = x
        target = x.level[0].forward
        if target is None or target.score != score or target.ele != ele:
            return None
        self.unlink_node(target, update)
        return target

    def unlink_node(self, x, update):
        """t_zset.c:345：命中层做 span 递补，未命中层 span 减一。"""
        for i in range(self.level):
            if update[i].level[i].forward is x:
                incr_span(update[i], i, get_span(x, i) - 1)
                update[i].level[i].forward = x.level[i].forward
            else:
                incr_span(update[i], i, -1)
        if x.level[0].forward:
            x.level[0].forward.backward = x.backward
        else:
            self.tail = x.backward
        while self.level > 1 and self.header.level[self.level - 1].forward is None:
            set_span(self.header, self.level - 1, 0)
            self.level -= 1
        self.length -= 1

    # -------------------------------------------------------------- 查询
    def get_rank(self, score, ele):
        """t_zset.c:645：1-based，找不到返回 0。"""
        rank = 0
        x = self.header
        for i in range(self.level - 1, -1, -1):
            while compare_with_node(score, ele, x.level[i].forward) >= 0:
                rank += get_span(x, i)
                x = x.level[i].forward
            if x is not self.header and compare_with_node(score, ele, x) == 0:
                return rank
        return 0

    def get_element_by_rank(self, rank):
        """t_zset.c:681：rank 必须 1-based，越界返回 None。"""
        if rank <= 0 or rank > self.length:
            return None
        traversed = 0
        x = self.header
        for i in range(self.level - 1, -1, -1):
            while x.level[i].forward and (traversed + get_span(x, i)) <= rank:
                traversed += get_span(x, i)
                x = x.level[i].forward
            if traversed == rank:
                return x
        return None

    def rank_via_span(self, node):
        """t_zset.c:672：length 减去「从该节点按各自顶层跳到尾部的 span 之和」。"""
        distance = 0
        x = node
        while x:
            level = len(x.level) - 1
            distance += get_span(x, level)
            x = x.level[level].forward
        return self.length - distance

    def is_in_range(self, minimum, maximum, minex=False, maxex=False):
        """t_zset.c:441：两处提前判空 —— tail 够不到下界、或首节点超过上界。"""
        if minimum > maximum or (minimum == maximum and (minex or maxex)):
            return False
        if self.tail is None:
            return False
        if not (self.tail.score > minimum if minex else self.tail.score >= minimum):
            return False
        first = self.header.level[0].forward
        if first is None:
            return False
        return (first.score < maximum if maxex else first.score <= maximum)

    def in_order(self):
        """按 level[0] 串起来的完整序（校验 span/rank 一致性用）。"""
        out = []
        x = self.header.level[0].forward
        while x:
            out.append((x.score, x.ele))
            x = x.level[0].forward
        return out

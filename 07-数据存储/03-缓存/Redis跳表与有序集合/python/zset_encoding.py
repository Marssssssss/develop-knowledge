"""Redis ZSET 的 listpack / skiplist 编码选择与转换。

事实来源（本轮实读，非记忆）：
  - redis/redis unstable `src/t_zset.c` 1405-1418：zsetTypeCreate（两个 hint 都要满足）
  - redis/redis unstable `src/t_zset.c` 1428-1437：zsetTypeMaybeConvert（只看 size_hint）
  - redis/redis unstable `src/t_zset.c` 1523-1531：zsetConvertToListpackIfNeeded（length 用 <=）
  - redis/redis unstable `src/t_zset.c` 1650-1665：ZADD 的三条件或判定
  - redis/redis unstable `redis.conf` 2358-2359 与 `src/config.c` 3650/3654：默认 128 / 64
"""

from skiplist import SKIPLIST, Node, ZSkipList

# zset-max-listpack-entries 128 / zset-max-listpack-value 64
ZSET_MAX_LISTPACK_ENTRIES = 128
ZSET_MAX_LISTPACK_VALUE = 64

LISTPACK = "listpack"


class ZSetObject:
    """只建模编码选择与转换，不建模 listpack 本身的字节布局。"""

    def __init__(self, encoding=LISTPACK):
        self.encoding = encoding
        self.members = {}          # listpack 形态下的 ele -> score
        self.zsl = None
        if encoding == SKIPLIST:
            self.zsl = ZSkipList()

    def length(self):
        return len(self.members) if self.encoding == LISTPACK else self.zsl.length

    def convert(self, encoding):
        if self.encoding == encoding:
            return
        if encoding == SKIPLIST:
            zsl = ZSkipList()
            for ele, score in sorted(self.members.items(), key=lambda kv: (kv[1], kv[0])):
                zsl.insert_node(Node(1, score, ele))
            self.zsl = zsl
            self.members = {}
            self.encoding = SKIPLIST
        elif encoding == LISTPACK:
            members = {}
            x = self.zsl.header.level[0].forward
            while x:
                members[x.ele] = x.score
                x = x.level[0].forward
            self.members = members
            self.zsl = None
            self.encoding = LISTPACK


def zset_type_create(size_hint, val_len_hint):
    """t_zset.c:1414：两个 hint 都满足才建 listpack。"""
    if size_hint <= ZSET_MAX_LISTPACK_ENTRIES and val_len_hint <= ZSET_MAX_LISTPACK_VALUE:
        return ZSetObject(LISTPACK)
    return ZSetObject(SKIPLIST)


def zset_type_maybe_convert(zobj, size_hint):
    """t_zset.c:1428：**只看 size_hint，不看 val_len_hint**。"""
    if zobj.encoding == LISTPACK and size_hint > ZSET_MAX_LISTPACK_ENTRIES:
        zobj.convert(SKIPLIST)


def zadd(zobj, ele, score, safe_to_add=True):
    """t_zset.c:1650：插入后长度 > 128、或 ele 长度 > 64、或 lpSafeToAdd 失败 → 转 skiplist。

    safe_to_add 对应真实实现里的 lpSafeToAdd(zobj->ptr, sdslen(ele))；
    本模块不建模 listpack 的溢出保护，默认放行（口径见 README）。
    """
    if zobj.encoding == LISTPACK:
        if (len(zobj.members) + 1 > ZSET_MAX_LISTPACK_ENTRIES
                or len(ele) > ZSET_MAX_LISTPACK_VALUE
                or not safe_to_add):
            zobj.convert(SKIPLIST)
        else:
            zobj.members[ele] = score
            return
    if zobj.encoding == SKIPLIST:
        zobj.zsl.insert_node(Node(1, score, ele))


def zset_convert_to_listpack_if_needed(zobj, maxelelen, totelelen, safe_to_add=True):
    """t_zset.c:1523：反向转换同样要三个条件同时成立，且 length 用的是 <= 不是 <。"""
    if zobj.encoding == LISTPACK:
        return
    if (zobj.zsl.length <= ZSET_MAX_LISTPACK_ENTRIES
            and maxelelen <= ZSET_MAX_LISTPACK_VALUE
            and safe_to_add):
        zobj.convert(LISTPACK)

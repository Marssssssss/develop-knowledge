"""Redis 6.0+ 多线程 I/O：常量、拷贝规避判定与配置别名。

转写/摘录对象：
  - redis/src/server.h         （IO_THREADS_MAX_NUM / COPY_AVOID_* 常量、
                                 IOThread 结构、io_threads_num 字段）
  - redis/src/networking.c     （isCopyAvoidPreferred()、
                                 acceptCommonHandler() 里 io_threads_num > 1 的分派门控）
  - redis/src/config.c         （deprecatedConfig 表：io-threads-do-reads 已废弃）
  - redis/redis.conf           （THREADED I/O 段落的线程数建议）

所有数值常量均取自上述源码，未作推测。
"""

# ---- server.h ----
IO_THREADS_MAX_NUM = 128
COPY_AVOID_MIN_IO_THREADS = 7
COPY_AVOID_MIN_STRING_SIZE = 16384
COPY_AVOID_MIN_STRING_SIZE_THREADED = 65536

# ---- server.h: client flags / types ----
CLIENT_PUSHING = 1 << 46
CLIENT_TYPE_NORMAL = 0
CLIENT_TYPE_MASTER = 3

# ---- object.h ----
OBJ_ENCODING_RAW = 0
OBJ_REFCOUNT_BITS = 23
OBJ_SHARED_REFCOUNT = (1 << OBJ_REFCOUNT_BITS) - 1      # 8388607
OBJ_STATIC_REFCOUNT = (1 << OBJ_REFCOUNT_BITS) - 2      # 8388606
OBJ_FIRST_SPECIAL_REFCOUNT = OBJ_STATIC_REFCOUNT


class FakeClient:
    """isCopyAvoidPreferred() 用到的 client 字段子集。"""

    def __init__(self, conn=True, client_type=CLIENT_TYPE_NORMAL, flags=0):
        self.conn = conn                 # None 表示 fake client
        self.client_type = client_type
        self.flags = flags


class FakeRobj:
    def __init__(self, encoding=OBJ_ENCODING_RAW, refcount=1):
        self.encoding = encoding
        self.refcount = refcount


class Server:
    def __init__(self, io_threads_num=1, reply_copy_avoidance_enabled=1):
        self.io_threads_num = io_threads_num
        self.reply_copy_avoidance_enabled = reply_copy_avoidance_enabled


def is_copy_avoid_preferred(server, c, obj, length):
    """networking.c: isCopyAvoidPreferred() 的等价转写。"""
    # 1) fake client（无连接）或功能未开启 → 不用
    if c.conn is None or not server.reply_copy_avoidance_enabled:
        return 0

    # 2) 只有普通客户端参与
    if c.client_type != CLIENT_TYPE_NORMAL:
        return 0

    # 3) push 消息需要延迟到 pending_push_messages，不能走引用
    if c.flags & CLIENT_PUSHING:
        return 0

    # 4) 必须是 RAW 编码，且不是共享/静态对象
    if obj.encoding != OBJ_ENCODING_RAW or obj.refcount >= OBJ_FIRST_SPECIAL_REFCOUNT:
        return 0

    # 5) 线程数够多 → 不看长度，一律走引用
    if server.io_threads_num >= COPY_AVOID_MIN_IO_THREADS:
        return 1

    # 6) 纯主线程
    if server.io_threads_num == 1:
        return 1 if length >= COPY_AVOID_MIN_STRING_SIZE else 0

    # 7) 主线程 + I/O 线程（2..6）
    return 1 if length >= COPY_AVOID_MIN_STRING_SIZE_THREADED else 0


def io_threads_activated(server):
    """networking.c: acceptCommonHandler() 中 `server.io_threads_num > 1` 才分派客户端。"""
    return server.io_threads_num > 1


def suggested_io_threads(cores):
    """redis.conf THREADED I/O 段：「4 核用 3，8 核用 7」，即 cores - 1。

    conf 同时要求「至少 4 核、并且留出一个空闲核」；不满足建议时返回 1（保持单线程）。
    """
    if cores < 4:
        return 1
    return cores - 1


# ---- config.c: deprecatedConfig deprecated_configs[] ----
DEPRECATED_CONFIGS = (
    ("list-max-ziplist-entries", 2, 2),
    ("list-max-ziplist-value", 2, 2),
    ("lua-replicate-commands", 2, 2),
    ("io-threads-do-reads", 2, 2),
)


def is_deprecated_config(name):
    return any(name == entry[0] for entry in DEPRECATED_CONFIGS)

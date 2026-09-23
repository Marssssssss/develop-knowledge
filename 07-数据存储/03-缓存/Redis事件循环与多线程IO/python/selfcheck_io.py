"""多线程 I/O 常量与 isCopyAvoidPreferred() 自检。

判定函数逐分支成对构造：只差一个入参，确保每个 return 都被真正走到。
"""

from io_threads import (
    CLIENT_PUSHING,
    CLIENT_TYPE_MASTER,
    CLIENT_TYPE_NORMAL,
    COPY_AVOID_MIN_IO_THREADS,
    COPY_AVOID_MIN_STRING_SIZE,
    COPY_AVOID_MIN_STRING_SIZE_THREADED,
    DEPRECATED_CONFIGS,
    IO_THREADS_MAX_NUM,
    OBJ_ENCODING_RAW,
    OBJ_FIRST_SPECIAL_REFCOUNT,
    OBJ_REFCOUNT_BITS,
    OBJ_SHARED_REFCOUNT,
    OBJ_STATIC_REFCOUNT,
    FakeClient,
    FakeRobj,
    Server,
    io_threads_activated,
    is_copy_avoid_preferred,
    is_deprecated_config,
    suggested_io_threads,
)

PASS = 0
FAIL = 0


def check(name, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL {name}: got {got!r} want {want!r}")


# ---------------------------------------------------------------- 常量
check("IO_THREADS_MAX_NUM", IO_THREADS_MAX_NUM, 128)
check("COPY_AVOID_MIN_IO_THREADS", COPY_AVOID_MIN_IO_THREADS, 7)
check("COPY_AVOID_MIN_STRING_SIZE", COPY_AVOID_MIN_STRING_SIZE, 16384)
check("COPY_AVOID_MIN_STRING_SIZE_THREADED", COPY_AVOID_MIN_STRING_SIZE_THREADED, 65536)
check("  带线程阈值是无线程阈值的 4 倍", COPY_AVOID_MIN_STRING_SIZE_THREADED, COPY_AVOID_MIN_STRING_SIZE * 4)

check("OBJ_REFCOUNT_BITS", OBJ_REFCOUNT_BITS, 23)
check("OBJ_SHARED_REFCOUNT = (1<<23)-1", OBJ_SHARED_REFCOUNT, 8388607)
check("OBJ_STATIC_REFCOUNT = (1<<23)-2", OBJ_STATIC_REFCOUNT, 8388606)
check("OBJ_FIRST_SPECIAL_REFCOUNT 就是 OBJ_STATIC_REFCOUNT", OBJ_FIRST_SPECIAL_REFCOUNT, OBJ_STATIC_REFCOUNT)
check("OBJ_ENCODING_RAW", OBJ_ENCODING_RAW, 0)
check("CLIENT_TYPE_NORMAL", CLIENT_TYPE_NORMAL, 0)
check("CLIENT_TYPE_MASTER", CLIENT_TYPE_MASTER, 3)
check("CLIENT_PUSHING = 1<<46", CLIENT_PUSHING, 1 << 46)


def cap(threads=1, length=COPY_AVOID_MIN_STRING_SIZE, conn=True, ctype=CLIENT_TYPE_NORMAL,
        flags=0, encoding=OBJ_ENCODING_RAW, refcount=1, enabled=1):
    srv = Server(io_threads_num=threads, reply_copy_avoidance_enabled=enabled)
    return is_copy_avoid_preferred(srv, FakeClient(conn, ctype, flags), FakeRobj(encoding, refcount), length)


# ---------------------------------------------------------------- 分支 1：开关 / fake client
check("功能关闭 → 0", cap(enabled=0), 0)
check("功能开启（其余相同）→ 1", cap(enabled=1), 1)
check("fake client（无 conn）→ 0", cap(conn=None), 0)
check("有 conn（其余相同）→ 1", cap(conn=True), 1)

# ---------------------------------------------------------------- 分支 2：客户端类型
check("MASTER 类型 → 0", cap(ctype=CLIENT_TYPE_MASTER), 0)
check("NORMAL 类型（其余相同）→ 1", cap(ctype=CLIENT_TYPE_NORMAL), 1)

# ---------------------------------------------------------------- 分支 3：CLIENT_PUSHING
check("CLIENT_PUSHING → 0", cap(flags=CLIENT_PUSHING), 0)
check("无 PUSHING（其余相同）→ 1", cap(flags=0), 1)

# ---------------------------------------------------------------- 分支 4：编码 / refcount
check("非 RAW 编码 → 0", cap(encoding=1), 0)
check("RAW 编码（其余相同）→ 1", cap(encoding=OBJ_ENCODING_RAW), 1)
check("refcount = STATIC → 0", cap(refcount=OBJ_STATIC_REFCOUNT), 0)
check("refcount = STATIC-1 → 1", cap(refcount=OBJ_STATIC_REFCOUNT - 1), 1)
check("refcount = SHARED → 0", cap(refcount=OBJ_SHARED_REFCOUNT), 0)

# ---------------------------------------------------------------- 分支 5：线程数够多不看长度
check("io_threads_num = 7，len = 0 → 1", cap(threads=7, length=0), 1)
check("io_threads_num = 6，len = 0 → 0", cap(threads=6, length=0), 0)
check("  阈值是 >= 7（7 生效、6 不生效）",
      (cap(threads=7, length=0), cap(threads=6, length=0)), (1, 0))
check("io_threads_num = 128，len = 0 → 1", cap(threads=IO_THREADS_MAX_NUM, length=0), 1)

# ---------------------------------------------------------------- 分支 6：纯主线程
check("1 线程，len = 16383 → 0", cap(threads=1, length=16383), 0)
check("1 线程，len = 16384 → 1", cap(threads=1, length=16384), 1)

# ---------------------------------------------------------------- 分支 7：主线程 + I/O 线程
check("2 线程，len = 65535 → 0", cap(threads=2, length=65535), 0)
check("2 线程，len = 65536 → 1", cap(threads=2, length=65536), 1)
check("6 线程，len = 65535 → 0", cap(threads=6, length=65535), 0)
check("6 线程，len = 65536 → 1", cap(threads=6, length=65536), 1)
check("  2 线程的门槛高于 1 线程", (cap(threads=2, length=16384), cap(threads=1, length=16384)), (0, 1))

# ---------------------------------------------------------------- io_threads_activated
check("io_threads_num = 1 → 不启用", io_threads_activated(Server(1)), False)
check("io_threads_num = 2 → 启用", io_threads_activated(Server(2)), True)

# ---------------------------------------------------------------- 线程数建议（redis.conf）
check("2 核 → 保持 1（conf 要求至少 4 核）", suggested_io_threads(2), 1)
check("4 核 → 3", suggested_io_threads(4), 3)
check("8 核 → 7", suggested_io_threads(8), 7)
check("  建议值总比核数少 1（留出主线程）", suggested_io_threads(16), 15)

# ---------------------------------------------------------------- 废弃配置别名
check("io-threads-do-reads 已废弃", is_deprecated_config("io-threads-do-reads"), True)
check("list-max-ziplist-entries 已废弃", is_deprecated_config("list-max-ziplist-entries"), True)
check("io-threads 本身未废弃", is_deprecated_config("io-threads"), False)
check("废弃表共 4 项", len(DEPRECATED_CONFIGS), 4)
check("  每项都是 (名字, 2, 2) 三元组", all(len(e) == 3 and e[1] == 2 and e[2] == 2 for e in DEPRECATED_CONFIGS), True)

print(f"\nio_threads 自检：{PASS} 条通过，{FAIL} 条失败")
if FAIL:
    raise SystemExit(1)

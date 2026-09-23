"""ae.h 常量（ae.c:22-38）。单独成文件，避免 ae 与 ae_time 循环 import。"""

# ---- ae.h:22-24 事件掩码 ----
AE_NONE = 0
AE_READABLE = 1
AE_WRITABLE = 2
AE_BARRIER = 4

# ---- ae.h:30-35 处理标志 ----
AE_FILE_EVENTS = 1 << 0
AE_TIME_EVENTS = 1 << 1
AE_ALL_EVENTS = AE_FILE_EVENTS | AE_TIME_EVENTS
AE_DONT_WAIT = 1 << 2
AE_CALL_BEFORE_SLEEP = 1 << 3
AE_CALL_AFTER_SLEEP = 1 << 4

# ---- ae.h:37-38 ----
AE_NOMORE = -1
AE_DELETED_EVENT_ID = -1

AE_OK = 0
AE_ERR = -1

INITIAL_EVENT = 16  # ae.c: 初始 events/fired 数组容量

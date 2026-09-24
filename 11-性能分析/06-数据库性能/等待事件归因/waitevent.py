# -*- coding: utf-8 -*-
"""pg_stat_activity 等待事件归因模型(采样口径)。

口径(实读源):PostgreSQL 18 官方文档 27.2 Monitoring Statistics —
Table 27.4 Wait Event Types(十类事件的定义原文)。
采样法:反复抓 pg_stat_activity,各桶样本占比 = 时间占比(与 perf 采样同理)。
"""

# Table 27.4 的十类(InjectionPoint 为测试注入用,生产不可见)
WAIT_TYPES = {
    "Activity":     "进程空闲,在主循环里等活动(多为后台进程)",
    "BufferPin":    "等数据缓冲区的独占 pin(打开的游标可能拖长等待)",
    "Client":       "等**用户应用**的 socket 活动——服务器在等客户端,不是 DB 慢",
    "Extension":    "等扩展模块定义的条件",
    "InjectionPoint": "等测试注入点(仅测试)",
    "IO":           "等一个 I/O 操作完成",
    "IPC":          "等另一个服务器进程的交互",
    "Lock":         "等重量级锁(锁管理器锁,保护表等 SQL 可见对象)",
    "LWLock":       "等轻量级锁(保护共享内存数据结构)",
    "Timeout":      "等超时到期",
}


def classify_sample(state, wait_event_type):
    """单样本归桶:active 且无等待类型 = 正在 CPU 上跑;idle 系 = 会话空闲。

    pg_stat_activity:wait_event_type 为 NULL 表示进程当前没有在等待(在跑);
    state 区分 active / idle / idle in transaction 等。
    """
    if state == "active":
        return "running" if wait_event_type is None else wait_event_type
    return "idle-session"                       # 采样到的空闲后端不占资源


def attribute(samples):
    """samples: [(state, wait_event_type)] → 各桶时间占比 + 主瓶颈。"""
    buckets = {}
    for state, wet in samples:
        b = classify_sample(state, wet)
        buckets[b] = buckets.get(b, 0) + 1
    total = len(samples)
    share = {k: v / total for k, v in buckets.items()}
    return share


def dominant(share, exclude=("idle-session",)):
    """排除空闲会话后取占比最大的等待桶。"""
    items = [(k, v) for k, v in share.items() if k not in exclude and k != "running"]
    return max(items, key=lambda kv: kv[1]) if items else (None, 0)


def verdict(share):
    """把主桶翻译成人话:DB 内部瓶颈 vs 应用侧瓶颈 vs CPU 饱和。"""
    dom, frac = dominant(share)
    running = share.get("running", 0)
    if running > 0.5:
        return f"CPU 饱和({running:.0%} 样本在跑):先看执行计划/加索引,别加连接"
    if dom is None:
        return "无显著等待"
    if dom == "Client":
        return (f"应用侧瓶颈({frac:.0%}):服务器在等客户端 socket"
                f"(ClientRead 之类)——think time 长,加 DB 资源无效")
    if dom == "Lock":
        return f"重量级锁等待({frac:.0%}):表级/咨询锁冲突,查 pg_locks 阻塞链"
    if dom == "LWLock":
        return f"轻量级锁({frac:.0%}):共享内存结构争用(缓冲区/WAL 插入等)"
    if dom == "IO":
        return f"I/O 等待({frac:.0%}):看 buffer 命中率与 shared_buffers/磁盘"
    return f"{dom} 等待({frac:.0%})"

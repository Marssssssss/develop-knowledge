"""OCC 竞争轮次模型 —— 用来量"退避到底省了多少工"。

AWS 博客用的模拟器是完整事件驱动版(`OccServer` + `OccClient` + 网络延迟正态抖动)。
本模块是**刻意的简化版**:网络延迟取固定 `rtt`(不含方差),同一时刻到达的写按
client id 定序。做这个简化是为了让结果**可复现**(自检能写死期望值),代价是绝对值
与博客的图不同 —— 这点在 README 里明确标注。

模型的 OCC 语义(与源码一致):
- 客户端先 read 拿到版本号,再 write 带上该版本号
- 服务端只在"带过来的版本号 == 当前版本号"时写入并把版本 +1
- 因此**同一时刻发起的一批客户端里,只有一个能成功** —— 这就是竞争

博客正文的两条定性结论(本模块用来做方向性核对):
- "With N clients contending, the total amount of work done by the system
  increases with N2."(不做退避时工作量随 N² 增长)
- "one client succeeds every round, so it takes N rounds for all N clients to
  succeed"
"""

import backoff as BK


def simulate(n_clients, name, rtt=10.0, seed=20260921, base=5.0, cap=2000.0,
             max_time=5e6, slot=1.0):
    """返回 (总调用次数, 全部完成所用时间ms)。

    时间轴:客户端在 t 发起 → read 在 t+rtt 到达 → write 在 t+2*rtt 到达。
    失败者在 t+2*rtt 之后再睡 backoff(attempt) 才发起下一次。

    `slot` 是**时间槽宽度(ms)**:重试时刻向上取整到槽边界,同一槽内发起的客户端
    视为同时到达因而互相冲突。没有这个离散化,连续随机数几乎永不相等,任何非零
    jitter 都能把 100 个客户端完全错开,四种策略会给出同一个数字(实测都是 199),
    模型就失去了分辨力 —— 这是第一版写错的地方。
    """
    import math
    import random
    rng = random.Random(seed)
    clients = []
    for i in range(n_clients):
        clients.append({
            "id": i,
            "next": 0.0,           # 下一次发起的时刻
            "attempt": 0,          # 已失败次数
            "bo": BK.make(name, base, cap, rng),
            "done": False,
        })
    version = 0
    calls = 0
    now = 0.0
    finished = 0
    while finished < n_clients and now <= max_time:
        pending = [c for c in clients if not c["done"]]
        now = min(c["next"] for c in pending)
        batch = [c for c in pending if c["next"] == now]
        # 同一批发起:read 全部读到同一个版本号,write 按 id 定序,只有第一个成功
        read_version = version
        for c in sorted(batch, key=lambda x: x["id"]):
            calls += 1
            if read_version == version:
                version += 1
                c["done"] = True
                finished += 1
            else:
                c["attempt"] += 1
                wake = now + 2 * rtt + c["bo"].backoff(c["attempt"])
                # 向上取整到槽边界:同一槽内的重试互相冲突
                c["next"] = math.ceil(wake / slot - 1e-12) * slot
        if not batch:
            break
    total_time = max(c["next"] for c in clients)
    return calls, total_time


def sweep(names, client_counts=range(10, 110, 10), **kw):
    """扫一遍 (策略 × 客户端数)。"""
    out = {}
    for name in names:
        row = []
        for n in client_counts:
            calls, tm = simulate(n, name, **kw)
            row.append((n, calls, tm))
        out[name] = row
    return out

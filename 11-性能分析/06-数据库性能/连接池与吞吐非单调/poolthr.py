# -*- coding: utf-8 -*-
"""连接池语义与吞吐非单调模型。

口径(实读源):
  pgbouncer 官方 features.html —— session/transaction/statement 三档池化语义
  pgbouncer 官方 config.html —— max_client_conn 的 fd 公式与 default_pool_size(默认 20)
吞吐曲线为本 demo 的解析模型(标注为模型),池语义与 fd 公式为官方口径。
"""


POOLING_MODES = {
    "session": "客户端连接期间独占一条服务器连接;断开才归还。支持全部 PostgreSQL 特性。",
    "transaction": "仅在事务期间持有服务器连接,事务结束即归还;会破坏部分会话级特性。",
    "statement": "每条语句独占,最激进;破坏特性最多,应用必须完全配合。",
}


def fd_limit_needed(max_client_conn, pool_size, databases, users=1):
    """config.html:理论最大 fd = max_client_conn + max_pool_size×库×用户数。"""
    return max_client_conn + pool_size * databases * users


def little_N(throughput, response_time):
    """Little 定律:系统内平均数 N = 吞吐 X × 驻留时间 R(算术恒等式)。"""
    return throughput * response_time


def throughput_curve(n_conns, capacity, demand, switch_cost=0.0):
    """解析模型:并发 ≤ 容量时线性扩展;超出后共享容量且付上下文切换税。

    n_conns: 并发连接数; capacity: 数据库可用并发(≈CPU 核数);
    demand: 单查询服务需求(秒); switch_cost: 超载后每连接的额外开销系数。
    返回稳态吞吐(查询/秒)。标注:**曲线形状是模型**,不是官方文档数字。
    """
    effective = min(n_conns, capacity)
    if n_conns <= capacity:
        return n_conns / demand
    overload = n_conns - capacity
    return effective / (demand * (1.0 + switch_cost * overload / capacity))


def best_pool_size(capacity, demand, switch_cost, ceiling):
    """扫描找吞吐峰值对应的连接数(模型:峰值在容量附近,超出单调下降)。"""
    xs = [(n, throughput_curve(n, capacity, demand, switch_cost)) for n in range(1, ceiling + 1)]
    return max(xs, key=lambda t: t[1])


def multiplex_capacity(mode, clients, pool_size):
    """池化的复用数学:同一 pool_size 下不同模式能同时接纳多少客户端活动。"""
    if mode == "session":
        return min(clients, pool_size)          # 独占:连接数即上限
    if mode == "transaction":
        return clients                          # 事务间隙释放,全体可排队轮转
    return clients                              # statement 同理,粒度更细

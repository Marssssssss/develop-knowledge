"""重连退避。

口径说明（重要，避免把两套东西混为一谈）：
  - **RFC 6455 没有规定重连策略**。它只定义了关闭状态码的语义（§7.4.1）
    与区间归属（§7.4.2）。所以"收到哪个码该不该重连"是**工程策略**，
    本模块依据 §7.4.1 的文字描述给出一张表，并在下面显式标出依据。
  - **退避算法本身**取自 gRPC 官方 `doc/connection-backoff.md`
    （github.com/grpc/grpc, master）——一份有明确数值的公开规范：
    INITIAL_BACKOFF=1s / MULTIPLIER=1.6 / MAX_BACKOFF=120s / JITTER=0.2 /
    MIN_CONNECT_TIMEOUT=20s。原文要求替代实现必须"让同时开始的退避发散，
    且不得比该算法更频繁地尝试连接"。
"""

import random

# --- gRPC connection-backoff.md 的固定参数 ---
INITIAL_BACKOFF = 1.0
MULTIPLIER = 1.6
MAX_BACKOFF = 120.0
JITTER = 0.2
MIN_CONNECT_TIMEOUT = 20.0

# --- 重连策略：依据 RFC 6455 §7.4.1 各码的语义文字 ---
# 1000 目的已达       → 不重连
# 1001 端点要离开     → 重连（服务器重启/页面跳转）
# 1002 协议错误       → 不重连（重试只会再错一次）
# 1003 无法接受的数据类型 → 不重连
# 1007 载荷类型不符   → 不重连（消息本身有问题）
# 1008 违反策略       → 不重连
# 1009 消息过大       → 不重连
# 1010 客户端要求的扩展没谈成 → 不重连（谈不成的扩展再连也谈不成）
# 1011 服务器内部意外 → 重连（原文 "unexpected condition"，是瞬时的）
# 1012 Service Restart / 1013 Try Again Later / 1014 Bad Gateway（IANA 注册）
#                     → 重连（名字本身就要求重试，1013 还带"稍后"语义）
# 1015 TLS 握手失败   → 不重连（证书问题不会自己好）
RECONNECT = {
    1000: False, 1001: True, 1002: False, 1003: False,
    1007: False, 1008: False, 1009: False, 1010: False,
    1011: True, 1012: True, 1013: True, 1014: True, 1015: False,
}

#: 收到这些码时连退避都不必等 —— 服务端明确要求"稍后再来"。
IMMEDIATE_RETRY = frozenset({1012, 1013})


class Backoff:
    """gRPC 连接退避算法（含 jitter 与重置）。"""

    def __init__(self, initial=INITIAL_BACKOFF, multiplier=MULTIPLIER,
                 max_backoff=MAX_BACKOFF, jitter=JITTER,
                 min_connect_timeout=MIN_CONNECT_TIMEOUT):
        self.initial = initial
        self.multiplier = multiplier
        self.max_backoff = max_backoff
        self.jitter = jitter
        self.min_connect_timeout = min_connect_timeout
        self.current = initial

    def next_wait(self, rnd=None):
        """返回本次应等待的秒数，并把游标推进到下一档。

        原文：deadline = now + current_backoff + Uniform(-J*cb, +J*cb)，
        随后 current_backoff = Min(current_backoff * MULTIPLIER, MAX_BACKOFF)。
        注意**先算等待、后推进游标**，所以第一次调用给出的是 INITIAL_BACKOFF。
        """
        rnd = rnd or random
        wait = self.current + rnd.uniform(-self.jitter * self.current,
                                          self.jitter * self.current)
        self.current = min(self.current * self.multiplier, self.max_backoff)
        return max(wait, 0.0)

    def connect_deadline(self, now):
        """单次连接尝试的超时：不短于 MIN_CONNECT_TIMEOUT。

        原文是 TryConnect(Max(current_deadline, now() + MIN_CONNECT_TIMEOUT))。
        """
        return max(self._current_deadline(now), now + self.min_connect_timeout)

    def _current_deadline(self, now):
        return now + self.current

    def reset(self):
        """原文：退避必须能重置，否则"新连接"与"断线重连"行为不一致。

        gRPC 选在收到 SETTINGS 帧时重置（确认连接被服务端接受）。WebSocket
        的对应点是**收到 101 握手响应之后、且双向已经有帧流通**；本 demo 取
        "收到第一个数据帧或 Pong"作为确认点（README 标注为工程映射）。
        """
        self.current = self.initial

    def schedule(self, attempts, rnd=None):
        """连续若干次的等待序列，便于观察发散与收敛。"""
        return [self.next_wait(rnd) for _ in range(attempts)]


def should_reconnect(code):
    """收到关闭码后是否重连。未知码一律重连（保守但不会永久停摆）。"""
    return RECONNECT.get(code, True)


def wait_before_retry(code, backoff, rnd=None):
    """返回重连前应等待的秒数；不该重连时返回 None。"""
    if not should_reconnect(code):
        return None
    if code in IMMEDIATE_RETRY:
        return 0.0
    return backoff.next_wait(rnd)

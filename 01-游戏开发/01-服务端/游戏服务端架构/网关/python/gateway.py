#!/usr/bin/env python3
"""游戏网关 (Gateway / Connector) 最小实现与自检.

权威依据(见 README 参考资料):
  网关作为「连接层 + 路由层」, 要解决的问题是:
  (1) 连接层: 长连接不能直接落在游戏实例上 —— 单机性能瓶颈 + 单点故障;
      统一做鉴权/限流/保活, 并让玩家切换房间/场景时**复用同一条连接**,
      避免反复建连带来的额外延迟;
  (2) 路由层: 收到的消息要送到「哪个服务」+「哪个实例」两步:
      按消息类型查路由表得到服务名; 再按 uid 选一个具体实例;
  (3) 主动推送: 转发时在消息上携带当前网关 id, 内部服务 push 时才能找回
      具体的网关实例 (否则不知道玩家挂在哪台网关上);
  (4) 网关自身要**无状态**才能水平扩容, 因此会话状态要么放外部存储、
      要么允许在网关故障后由客户端重连重建。

实例选择部分见同目录 hashring.py。本 demo 把上述四点全部量化:
  一、消息路由表(消息类型 -> 服务);
  二、实例选择: uid 取模 vs 一致性哈希 —— 扩容/缩容时的**重映射率**;
  三、会话表: 绑定 / 查询 / TTL 回收 / 推送回程路由;
  四、网关故障: 断连重连 + 会话重建。
"""

from __future__ import annotations

from hashring import VNODES, ModuloPicker, remap_rate, ring_load

# ---------------------------------------------------------------- 服务与路由
SERVICES = ("login", "match", "room", "battle", "chat", "rank", "inventory")

ROUTING_TABLE = {
    0x01: "login", 0x02: "login",              # 登录/心跳
    0x10: "match", 0x11: "match",              # 匹配
    0x20: "room", 0x21: "room", 0x22: "room",  # 房间
    0x30: "battle", 0x31: "battle",            # 战斗
    0x40: "chat", 0x41: "chat",                # 聊天
    0x50: "rank",                              # 排行榜
    0x60: "inventory", 0x61: "inventory",      # 背包
}

# 每个服务部署的实例数(决定该服务的实例选择空间)
INSTANCES = {"login": 4, "match": 2, "room": 8, "battle": 16,
             "chat": 2, "rank": 2, "inventory": 4}

SESSION_TTL_MS = 300_000       # 会话空闲 5 分钟回收
HEARTBEAT_MS = 30_000          # 客户端心跳间隔


def route(msg_type: int) -> str | None:
    """第一步: 按消息类型找服务。未知类型应被拒绝而不是乱投。"""
    return ROUTING_TABLE.get(msg_type)




# =====================================================================
# 三/四、会话表、推送回程与网关故障
# =====================================================================
class GatewayNode:
    def __init__(self, gid: str) -> None:
        self.gid = gid
        self.conns: set[int] = set()          # 挂在它上面的连接(uid)

    def __repr__(self) -> str:
        return f"<gw {self.gid} conns={len(self.conns)}>"


class GatewayCluster:
    def __init__(self, n_gateways: int) -> None:
        self.gws = [GatewayNode(f"gw{i}") for i in range(n_gateways)]
        # 会话表: uid -> (gateway_id, service_instance, last_active_ms)
        self.sessions: dict[int, tuple[str, str, int]] = {}
        self.rebound = 0
        self.rejected_unknown = 0

    # ---------- 连接层 ----------
    def connect(self, uid: int, now_ms: int, prefer: int | None = None) -> str:
        gid = self.gws[prefer if prefer is not None else uid % len(self.gws)].gid
        gw = self._gw(gid)
        gw.conns.add(uid)
        return gid

    def _gw(self, gid: str) -> GatewayNode:
        for g in self.gws:
            if g.gid == gid:
                return g
        raise KeyError(gid)

    # ---------- 路由层 ----------
    def dispatch(self, uid: int, msg_type: int, now_ms: int) -> tuple[str, str] | None:
        svc = route(msg_type)
        if svc is None:
            self.rejected_unknown += 1
            return None
        n = INSTANCES[svc]
        inst = f"{svc}#{uid % n}"
        prev = self.sessions.get(uid)
        if prev is None or prev[1] != inst:
            self.sessions[uid] = (self.conn_owner(uid), inst, now_ms)
        else:
            self.sessions[uid] = (prev[0], inst, now_ms)
        return svc, inst

    def conn_owner(self, uid: int) -> str:
        for g in self.gws:
            if uid in g.conns:
                return g.gid
        return "?"

    # ---------- 推送回程 ----------
    def push(self, uid: int, payload: str) -> str | None:
        """内部服务主动推送: 靠会话表里记录的网关 id 找回网关。"""
        s = self.sessions.get(uid)
        if s is None:
            return None
        gid = s[0]
        if gid not in [g.gid for g in self.gws]:
            return None                      # 网关已下线, 推送失败(等重连)
        if uid not in self._gw(gid).conns:
            return None
        return f"{gid}->{uid}:{payload}"

    # ---------- 运维 ----------
    def expire(self, now_ms: int) -> int:
        dead = [u for u, (_, _, ts) in self.sessions.items()
                if now_ms - ts > SESSION_TTL_MS]
        for u in dead:
            del self.sessions[u]
        return len(dead)

    def kill_gateway(self, gid: str) -> int:
        """网关下线: 其承载的连接全部断开, 会话记录作废, 客户端需重连。"""
        gw = self._gw(gid)
        dropped = len(gw.conns)
        for uid in list(gw.conns):
            self.sessions.pop(uid, None)
        gw.conns.clear()
        self.gws = [g for g in self.gws if g.gid != gid]
        return dropped

    def reconnect_all(self, uids, now_ms: int) -> int:
        """断线重连: 可能落到别的网关(模数变了), 因此必须重建会话绑定。"""
        n = 0
        for uid in uids:
            old = self.conn_owner(uid)
            gid = self.connect(uid, now_ms)
            if old != gid or uid not in self.sessions:
                n += 1
        self.rebound += n
        return n


# =====================================================================
# 自检
# =====================================================================
def main() -> None:
    print("== 一、消息路由表(消息类型 -> 服务) ==")
    assert route(0x01) == "login" and route(0x31) == "battle"
    assert route(0x99) is None
    print(f"  已知 {len(ROUTING_TABLE)} 个消息类型 -> "
          f"{len(set(ROUTING_TABLE.values()))} 个服务 "
          f"{sorted(set(ROUTING_TABLE.values()))}")
    print("  未知消息类型返回 None(拒绝而非乱投)  OK")

    print("\n== 二、实例选择: uid 取模 vs 一致性哈希 ==")
    sticky = ModuloPicker(8)
    assert all(sticky.pick(u) == sticky.pick(u) for u in range(5000))
    print("  拓扑不变时两者都满足粘性(same uid -> same instance)  OK")
    for before, after, label in ((8, 9, "扩容"), (9, 8, "缩容")):
        r = remap_rate(before, after)
        print(f"  {label} {before}->{after}: 取模重映射 {r['modulo']:.2%}, "
              f"一致性哈希重映射 {r['ring']:.2%} (理论下限 1/max = {r['ideal']:.2%})")
        assert r["modulo"] > 0.80, r
        assert r["ring"] < 0.25, r
    ld = ring_load(8)
    print(f"  负载均衡(8 节点, 20000 uid): 取模最大偏差 {ld['modulo_max_dev']:.2%}, "
          f"一致性哈希 {ld['ring_max_dev']:.2%} (虚拟节点 {VNODES}/节点)")
    assert ld["ring_max_dev"] < 0.35, ld
    print("  -> 取模扩容几乎全员迁移; 一致性哈希只动相邻区间(约 1/N)  OK")

    print("\n== 三、会话表 / 推送回程 ==")
    cl = GatewayCluster(3)
    now = 1_000_000
    for uid in range(6):
        cl.connect(uid, now)
        cl.dispatch(uid, 0x20, now)          # 建立 6 个会话
    r0 = cl.dispatch(0, 0x20, now)
    r1 = cl.dispatch(1, 0x30, now)
    assert r0 == ("room", "room#0"), r0
    assert r1 == ("battle", "battle#1"), r1
    assert cl.push(0, "invite") == "gw0->0:invite", cl.push(0, "invite")
    assert cl.push(5, "x") is not None
    assert cl.push(99, "x") is None          # 没有会话记录 -> 推送无处可去
    print(f"  连接分布: {[f'{g.gid}:{len(g.conns)}' for g in cl.gws]}")
    print(f"  uid=0 发 0x20 -> {r0}; uid=1 发 0x30 -> {r1}")
    print(f"  推送回程: {cl.push(0, 'invite')} (走会话表里记录的网关 id)")
    print("  无会话的 uid=99 推送返回 None(不知道挂在哪)  OK")
    assert cl.dispatch(0, 0x99, now) is None and cl.rejected_unknown == 1
    # TTL: 心跳刷新 vs 不刷新
    later = now + HEARTBEAT_MS * 12          # 6 分钟
    for uid in (0, 1, 2):
        cl.dispatch(uid, 0x02, later)        # 心跳 -> 刷新
    expired = cl.expire(later)
    print(f"  TTL={SESSION_TTL_MS // 1000}s; {later - now}ms 后心跳过的 3 个保留, "
          f"回收 {expired} 个 -> 剩余 {len(cl.sessions)} 个会话")
    assert expired == 3 and len(cl.sessions) == 3, (expired, cl.sessions)

    print("\n== 四、网关故障: 断连重连 + 会话重建 ==")
    cl2 = GatewayCluster(4)
    for uid in range(40):
        cl2.connect(uid, now)
    before = cl2.conn_owner
    victims = [u for u in range(40) if before(u) == "gw1"]
    dropped = cl2.kill_gateway("gw1")
    print(f"  gw1 下线: 断开 {dropped} 条连接, 会话记录作废; "
          f"剩余网关 {[g.gid for g in cl2.gws]}")
    assert dropped == len(victims) == 10, (dropped, len(victims))
    assert all(u not in cl2.sessions for u in victims)
    rebuilt = cl2.reconnect_all(victims, now)
    print(f"  客户端重连并重建会话: {rebuilt} 个 (模数由 4 变 3, 落点必然变化)")
    assert rebuilt == len(victims)
    assert all(cl2.dispatch(u, 0x20, now) is not None for u in victims)
    print("  -> 网关必须无状态(或状态外置), 才能随便宕机/扩容  OK")

    print("\n全部自检通过。")


if __name__ == "__main__":
    main()

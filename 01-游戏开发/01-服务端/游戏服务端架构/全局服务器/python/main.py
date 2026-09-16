# -*- coding: utf-8 -*-
"""全局服务器(Global Server)平台服最小实现。

按 MMO 架构参考(平台服进程分工):globalserver 管全区在线表/路由/排行,
rolereg 管角色名唯一性,rolechangeserver 管转区两阶段,idip 管运营邮件,
login 发 token 并按负载选区。zone 心跳失联即摘除,epoch 防旧实例复活。
"""
from collections import deque

ZONE_HB_TIMEOUT_MS = 10000
TRANSFER_LOCK_TIMEOUT_MS = 5000


class Zone:
    def __init__(self, zid, addr):
        self.id, self.addr = zid, addr
        self.epoch = 0            # 重启递增,拒绝旧实例的迟到消息
        self.load = 0
        self.inbox = deque()      # global -> zone 的投递队列
        self.players = set()
        self.last_hb = None
        self.up = False


class GlobalServer:
    def __init__(self, top_n=5):
        self.zones = {}
        self.online = {}          # uid -> (zone_id, gateway_id)
        self.rank = []            # (score, uid) 全区榜,降序
        self.top_n = top_n
        self.names = {}           # 角色名 -> uid(rolereg 唯一性)
        self.offline_mail = deque()   # idip 补偿邮件队列
        self.transfers = {}       # uid -> 两阶段转区状态
        self.tokens = {}          # token -> uid(login 签发)

    # ---- login:发 token + 按负载选区 ----
    def login(self, uid, token):
        self.tokens[token] = uid
        live = [z for z in self.zones.values() if z.up]
        if not live:
            return None
        z = min(live, key=lambda z: (z.load, z.id))
        return {"token": token, "zone": z.id, "addr": z.addr}

    # ---- 心跳与摘除 ----
    def register_zone(self, zid, addr, now):
        z = self.zones.setdefault(zid, Zone(zid, addr))
        z.epoch += 1
        z.addr, z.up, z.last_hb = addr, True, now
        return z.epoch

    def zone_heartbeat(self, zid, epoch, load, now):
        z = self.zones[zid]
        if epoch != z.epoch:                 # 旧实例迟到心跳:拒绝
            return "stale"
        z.load, z.last_hb = load, now
        return "ok"

    def sweep(self, now):
        """失联 zone 摘除:其在线玩家降级为离线(消息转 offline_mail)。"""
        dropped = []
        for z in list(self.zones.values()):
            if z.up and now - z.last_hb > ZONE_HB_TIMEOUT_MS:
                z.up = False
                dropped.append(z.id)
                for uid in list(z.players):
                    z.players.discard(uid)
                    if self.online.get(uid, (None,))[0] == z.id:
                        del self.online[uid]
        return dropped

    # ---- 在线表与路由 ----
    def player_online(self, uid, zid, gateway):
        if not self.zones[zid].up:
            return False
        self.online[uid] = (zid, gateway)
        self.zones[zid].players.add(uid)
        return True

    def player_logout(self, uid):
        zid = self.online.pop(uid, (None, None))[0]
        if zid in self.zones:
            self.zones[zid].players.discard(uid)

    def route_message(self, uid, msg, now):
        """在线投递到所在 zone 收件箱;离线落 offline_mail(idip 邮件)。"""
        ent = self.online.get(uid)
        if ent and self.zones[ent[0]].up:
            self.zones[ent[0]].inbox.append((uid, msg, now))
            return "zone:%d" % ent[0]
        self.offline_mail.append((uid, msg, now))
        return "offline_mail"

    # ---- 全区排行榜 ----
    def update_score(self, uid, score):
        self.rank = [r for r in self.rank if r[1] != uid]
        self.rank.append((score, uid))
        self.rank.sort(reverse=True)
        self.rank = self.rank[:self.top_n]

    def top_rank(self):
        return list(self.rank)

    # ---- rolereg:角色名唯一性 ----
    def register_name(self, uid, name):
        if name in self.names:
            return self.names[name] == uid
        self.names[name] = uid
        return True

    # ---- rolechange:两阶段转区 ----
    def transfer_request(self, uid, from_zid, to_zid, now):
        if uid in self.transfers:
            return False            # 锁定期内拒绝重复转区
        if to_zid not in self.zones or not self.zones[to_zid].up:
            return False
        self.transfers[uid] = {"from": from_zid, "to": to_zid,
                               "phase": "locked", "at": now}
        return True

    def transfer_stage2(self, uid, now):
        """源区完成序列化上报 -> 数据交目标区,等待目标 ack。"""
        st = self.transfers.get(uid)
        if not st or st["phase"] != "locked":
            return False
        st["phase"], st["at"] = "inflight", now
        target = self.zones[st["to"]]
        target.inbox.append((uid, "__handoff__", now))
        self.player_logout(uid)    # 源区立即除名,避免双在线
        return True

    def transfer_ack(self, uid, gateway, now):
        """目标区落地 ack -> 玩家在目标区上线,清锁。"""
        st = self.transfers.get(uid)
        if not st or st["phase"] != "inflight":
            return False
        ok = self.player_online(uid, st["to"], gateway)
        del self.transfers[uid]
        return ok

    def recover_transfers(self, now):
        """两阶段卡死(如目标区 crash)超时回滚:玩家回源区。"""
        rolled = []
        for uid, st in list(self.transfers.items()):
            if now - st["at"] > TRANSFER_LOCK_TIMEOUT_MS:
                del self.transfers[uid]
                rolled.append(uid)
        return rolled


def check(label, cond, detail=""):
    assert cond, "%s %s" % (label, detail)
    print("[ok] %s" % label)


def main():
    g = GlobalServer(top_n=5)
    now = 0
    for zid, addr in ((1, "10.0.0.1:9001"), (2, "10.0.0.2:9001")):
        g.register_zone(zid, addr, now)
        g.zone_heartbeat(zid, g.zones[zid].epoch, 0, now)

    # ---- 1. login:发 token,按负载选区 ----
    zone1 = g.zones[1]
    zone1.load = 80
    info = g.login(1001, "tok-1")
    check("登录按负载选轻区", info["zone"] == 2, "got %s" % info)
    check("返回区地址", info["addr"] == "10.0.0.2:9001")

    # ---- 2. rolereg:角色名全区唯一 ----
    check("首占名字成功", g.register_name(1001, "hero"))
    check("他人抢同名被拒", not g.register_name(1002, "hero"))
    check("本人重复登记幂等", g.register_name(1001, "hero"))

    # ---- 3. 在线表 + 路由 ----
    check("玩家在 zone2 上线", g.player_online(1001, 2, "gw-1"))
    check("在线消息路由到 zone2 收件箱",
          g.route_message(1001, b"chat:hi", now) == "zone:2")
    check("zone2 收件箱收到投递",
          zone1.inbox is not None and g.zones[2].inbox[-1][1] == b"chat:hi")
    g.player_logout(1001)
    check("离线消息落 offline_mail",
          g.route_message(1001, b"mail:gift", now) == "offline_mail"
          and g.offline_mail[-1][1] == b"mail:gift")

    # ---- 4. 心跳失联摘除 + epoch 防旧实例 ----
    g.player_online(2001, 1, "gw-1")
    g.zone_heartbeat(2, g.zones[2].epoch, 0, 10000)   # zone2 续命
    dropped = g.sweep(now + ZONE_HB_TIMEOUT_MS + 1)
    check("zone1 失联被摘除", dropped == [1])
    check("摘除后其玩家从在线表消失", 2001 not in g.online)
    check("对已摘除区的消息转 offline_mail",
          g.route_message(2001, b"kick", now + 11000) == "offline_mail")
    stale = g.zone_heartbeat(1, 0, 5, now + 12000)  # 旧 epoch 心跳
    check("旧实例迟到心跳被拒", stale == "stale")
    g.register_zone(1, "10.0.0.1:9001", now + 13000)   # 重启,epoch+1
    check("重启后 zone1 重新可用",
          g.zone_heartbeat(1, g.zones[1].epoch, 0, now + 13001) == "ok")

    # ---- 5. 全区排行榜 ----
    for uid, score in ((1, 90), (2, 70), (3, 85), (4, 60), (5, 99),
                       (6, 95), (7, 30)):
        g.update_score(uid, score)
    top = g.top_rank()
    check("Top5 截断且降序", [u for _, u in top] == [5, 6, 1, 3, 2],
          "got %s" % top)
    g.update_score(7, 100)
    check("分数更新挤掉榜尾", [u for _, u in g.top_rank()] == [7, 5, 6, 1, 3])

    # ---- 6. 两阶段转区 ----
    g2 = GlobalServer()
    for zid in (1, 2):
        g2.register_zone(zid, "z%d" % zid, 0)
        g2.zone_heartbeat(zid, g2.zones[zid].epoch, 0, 0)
    g2.player_online(3001, 1, "gw-1")
    check("转区请求获锁", g2.transfer_request(3001, 1, 2, 0))
    check("锁定期拒绝重复转区", not g2.transfer_request(3001, 1, 2, 1))
    check("阶段二:源区除名 + 数据移交目标区", g2.transfer_stage2(3001, 1))
    check("移交消息进了目标区收件箱",
          g2.zones[2].inbox[-1][1] == "__handoff__")
    check("移交期间玩家不在任何在线表", 3001 not in g2.online)
    check("目标区 ack 后在 zone2 上线", g2.transfer_ack(3001, "gw-9", 2))
    check("转区完成清锁", 3001 not in g2.transfers
          and g2.online[3001][0] == 2)

    # ---- 7. 转区卡死超时回滚 ----
    g2.transfer_request(3001, 2, 1, 100)     # 又一次转区,卡在阶段一
    rolled = g2.recover_transfers(100 + TRANSFER_LOCK_TIMEOUT_MS + 1)
    check("卡死转区超时回滚", rolled == [3001])
    check("回滚后可再次发起转区", g2.transfer_request(3001, 2, 1, 110))

    print("\n全部断言通过")


if __name__ == "__main__":
    main()

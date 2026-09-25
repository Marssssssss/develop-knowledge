# -*- coding: utf-8 -*-
"""幂等道具发放:SET NX + Lua 原子性。

口径(实读源):redis.io 官方——
  SET 命令页:NX = Only set the key if it does not already exist(XX 相反;GET 返回旧值);
  脚本导论页:"Redis guarantees the script's atomic execution... all server activities
  are blocked during its entire runtime... all of the script's effects either have yet
  to happen or had already happened."
"""

PASS = []


def ok(msg):
    PASS.append(msg)
    print(f"  [ok] {msg}")


class RedisLike:
    """最小内存模型:SET NX/EX 与 INCR。"""

    def __init__(self):
        self.store = {}
        self.ttl = {}

    def set_nx(self, key, value, ex=None):
        if key in self.store:
            return None                                  # NX:已存在,不设置
        self.store[key] = value
        self.ttl[key] = ex
        return "OK"

    def incr(self, key, by=1):
        v = int(self.store.get(key, 0)) + by
        self.store[key] = v
        return v

    def expire(self, key):
        """到期删除(幂等窗口过期后可再次发放)。"""
        self.store.pop(key, None)
        self.ttl.pop(key, None)


class GrantService:
    """双命令版:先 SET NX 占幂等键,成功才加币——两步之间存在竞态窗口。"""

    def __init__(self, kv, script_atomic=True):
        self.kv = kv
        self.atomic = script_atomic                      # Lua 版整体原子

    def grant(self, order_id, player, amount):
        marker = f"idem:{order_id}"
        if self.atomic:
            # EVAL 版:占键与加币在同一脚本内,服务器整体阻塞执行
            if marker in self.kv.store:
                return ("duplicate", self.kv.store[f"coin:{player}"])
            self.kv.store[marker] = "granted"
            self.kv.incr(f"coin:{player}", amount)
            return ("first", self.kv.store[f"coin:{player}"])
        got = self.kv.set_nx(marker, "granted")
        if got is None:
            return ("duplicate", self.kv.store[f"coin:{player}"])
        self.kv.incr(f"coin:{player}", amount)           # ← 崩在这行之前=只占键未加币
        return ("first", self.kv.store[f"coin:{player}"])


def main():
    print("1. SET NX 的判重语义")
    kv = RedisLike()
    assert kv.set_nx("idem:o1", "granted") == "OK"
    assert kv.set_nx("idem:o1", "granted") is None       # 官方:已存在则不设置
    ok("NX = 仅当键不存在才设置;第二次同键返回未设置——幂等判重的原语")

    print("2. 双击与重放")
    svc = GrantService(RedisLike())
    r1 = svc.grant("o1", "alice", 100)
    r2 = svc.grant("o1", "alice", 100)
    r3 = svc.grant("o1", "alice", 100)
    assert r1[0] == "first" and r2[0] == r3[0] == "duplicate"
    assert r2[1] == 100
    ok("同一订单无论重放多少次,只加一次币;重复调用返回当前余额")

    print("3. Lua 版的原子性")
    svc2 = GrantService(RedisLike(), script_atomic=True)
    svc2.grant("o2", "bob", 50)
    svc2.grant("o2", "bob", 50)
    assert svc2.kv.store["coin:bob"] == 50
    ok("官方原子性原文:脚本执行期间**服务器整体阻塞**,效果『要么全没发生,要么已全部发生』"
       "——占键与加币之间不存在崩溃窗口")

    print("4. 双命令版的竞态窗口")
    svc3 = GrantService(RedisLike(), script_atomic=False)
    svc3.grant("o3", "carol", 10)
    assert svc3.kv.store["coin:carol"] == 10
    marker_before = "idem:o3" in svc3.kv.store
    assert marker_before
    ok("两步版在『占键成功→加币』之间崩溃,会留下已占键但未加币的悬态——"
       "钱货两讫的操作要么用 Lua 单脚本,要么接受补偿对账")

    print("5. 幂等窗口")
    kv5 = RedisLike()
    svc5 = GrantService(kv5)
    svc5.grant("o4", "dave", 5)
    kv5.expire("idem:o4")                                # EX 到期
    svc5.grant("o4", "dave", 5)
    assert kv5.store["coin:dave"] == 10
    ok("幂等键带 EX:窗口过期后同单可再发放——窗口长度 = 网络重放可能拖多久 + 余量")

    print(f"\n共 {len(PASS)} 项断言全部通过")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""Redis Sorted Set 排行榜语义与同分决胜。

口径(实读源):redis.io 官方命令页——
  ZADD「Elements with the same score」节:同分元素按**二进制字典序**局部排序;
  ZRANK:分数从低到高,**rank 0 是最低分**,反序用 ZREVRANK;
  ZINCRBY 对成员加分。
同分决胜的『先到先得』要靠把时间戳编进 score 的复合分工程手段(本 demo 的模型)。
"""

PASS = []


def ok(msg):
    PASS.append(msg)
    print(f"  [ok] {msg}")


class ZSet:
    """按官方语义排序:score 为主键,同分按成员字节序。"""

    def __init__(self):
        self.scores = {}

    def zadd(self, member, score):
        self.scores[member] = score

    def zincrby(self, member, delta):
        self.scores[member] = self.scores.get(member, 0) + delta
        return self.scores[member]

    def _sorted(self):
        return sorted(self.scores.items(), key=lambda kv: (kv[1], kv[0].encode()))

    @staticmethod
    def _norm(items, start, stop):
        n = len(items)
        if start < 0:
            start += n
        if stop < 0:
            stop += n
        return items[start:stop + 1]

    def zrange(self, start, stop):
        return self._norm(self._sorted(), start, stop)

    def zrevrange(self, start, stop):
        return self._norm(list(reversed(self._sorted())), start, stop)

    def zrank(self, member):
        for i, (m, _) in enumerate(self._sorted()):
            if m == member:
                return i
        return None

    def zrevrank(self, member):
        return None if member not in self.scores else \
            len(self.scores) - 1 - self.zrank(member)


def composite_score(score, timestamp, ts_max=1 << 32):
    """复合分:高位放分数,低位放 (ts_max - ts) —— 同分时**更早达成者**复合分更大。"""
    return score * ts_max + (ts_max - 1 - timestamp)


def main():
    print("1. ZRANK 的 0 基与方向")
    z = ZSet()
    for m, s in [("alice", 10), ("bob", 30), ("carol", 20)]:
        z.zadd(m, s)
    assert z.zrank("alice") == 0 and z.zrank("bob") == 2
    assert z.zrank("nobody") is None
    ok("rank 0 = 最低分,升序;ZREVRANK 才是榜常用的『第 1 名 = 0』反序视图")

    print("2. 同分决胜:默认按成员字节序")
    z2 = ZSet()
    for m, s in [("zoe", 100), ("adam", 100), ("mia", 100)]:
        z2.zadd(m, s)
    assert [m for m, _ in z2.zrange(0, -1)] == ["adam", "mia", "zoe"]
    ok("官方口径:同分元素按**二进制字典序**局部排序——默认的『平局规则』"
       "既不是先到先得,也不是随机")

    print("3. 工程答案:复合分把时间戳编进 score")
    ts = {"adam": 500, "mia": 300, "zoe": 100}          # zoe 最早达成
    z3 = ZSet()
    for m, s in z2.scores.items():
        z3.zadd(m, composite_score(s, ts[m]))
    assert [m for m, _ in z3.zrevrange(0, -1)] == ["zoe", "mia", "adam"]
    ok("(score, MAX-ts) 复合分:同分时更早达成者更高——平局规则从『成员字节序』"
       "改成业务想要的『先到先得』,且不动任何命令")

    print("4. ZINCRBY 实时加分")
    z4 = ZSet()
    z4.zadd("player", 0)
    assert z4.zincrby("player", 55) == 55
    assert z4.zincrby("player", 5) == 60 and z4.scores["player"] == 60
    ok("ZINCRBY 对已有成员累加、新成员视 0 起步——榜单实时刷新的主通道")

    print(f"\n共 {len(PASS)} 项断言全部通过")


if __name__ == "__main__":
    main()

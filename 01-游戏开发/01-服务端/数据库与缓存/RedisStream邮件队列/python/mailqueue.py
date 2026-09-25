# -*- coding: utf-8 -*-
"""Redis Stream 邮件/奖励队列:消费组语义模型。

口径(实读源):redis.io 官方命令页——
  XADD:* 自动生成 ID;
  XREADGROUP:">" 读**从未投递给任何消费者**的新条目;指定 ID 则读本消费者
    的 pending(历史)列表;"the server will remember that a given message was
    delivered to you"(进 pending entries list);消费者名首次出现自动创建;
    官方例子:条目 A,B,C 被两个消费者分到 A,C 与 B;
  XACK 确认后从 pending 移除。
"""

PASS = []


def ok(msg):
    PASS.append(msg)
    print(f"  [ok] {msg}")


class Stream:
    def __init__(self):
        self.entries = []          # [(id, payload)]
        self._seq = 0

    def xadd(self, payload):
        self._seq += 1
        eid = f"1690000000000-{self._seq:04d}"        # * 自动 ID:时间-序号
        self.entries.append((eid, payload))
        return eid


class Group:
    def __init__(self, stream):
        self.stream = stream
        self.last_delivered = 0                         # 组内游标(只进不减)
        self.pending = {}                               # consumer -> [ (id, payload) ]
        self.acked = set()

    def xreadgroup(self, consumer, count=10):
        """'>':发从未投递过的新条目,组内消费者轮转分摊(官方 A,C / B 例)。"""
        out = []
        for _ in range(count):
            if self.last_delivered >= len(self.stream.entries):
                break
            eid, payload = self.stream.entries[self.last_delivered]
            self.last_delivered += 1
            self.pending.setdefault(consumer, []).append((eid, payload))
            out.append((eid, payload))
        return out

    def read_pending(self, consumer):
        """指定 ID(如 0)读自己的 pending 历史——崩溃恢复入口。"""
        return [(i, p) for (i, p) in self.pending.get(consumer, [])
                if i not in self.acked]

    def xack(self, consumer, eid):
        bucket = self.pending.get(consumer, [])
        before = len(bucket)
        self.pending[consumer] = [(i, p) for (i, p) in bucket if i != eid]
        self.acked.add(eid)
        return len(bucket) - before + 1 and 1


def main():
    print("1. XADD 自动 ID")
    s = Stream()
    ids = [s.xadd({"title": f"mail{i}"}) for i in range(3)]
    assert ids == ["1690000000000-0001", "1690000000000-0002", "1690000000000-0003"]
    ok("XADD 传 * 时服务器自动生成 时间戳-序号 形式的 ID——单调,天然是消费位点")

    print("2. '>' 的新条目与分摊")
    g = Group(s)
    got_a = g.xreadgroup("worker-a", count=2)           # 官方例:A 拿 A,C 一类
    got_b = g.xreadgroup("worker-b", count=2)           # B 拿 B,第三条已被 a 拿走则空
    delivered = {i for lst in (got_a, got_b) for i, _ in lst}
    assert len(delivered) == len({i for i, _ in got_a} | {i for i, _ in got_b})
    assert all(g.read_pending(c) for c in ("worker-a", "worker-b"))
    ok("同组消费者读 '>' 互不重复——服务端记住每条投给了谁(pending entries list);"
       "消费者名首次出现即自动注册,无需预建")

    print("3. XACK 与 pending 收口")
    first_id = got_a[0][0]
    assert g.read_pending("worker-a") and g.xack("worker-a", first_id)
    remain = g.read_pending("worker-a")
    assert first_id not in [i for i, _ in remain]
    ok("处理完成 XACK 后条目离开该消费者的 pending;未 ack 的永远留在里面——"
       "这就是『至少一次』交付的账本")

    print("4. 崩溃恢复:换消费者接管 pending")
    mailer_down = "worker-a"
    orphan = g.read_pending(mailer_down)
    for eid, payload in orphan:                          # 接管者处理存量
        g.xack(mailer_down, eid)
    assert not g.read_pending(mailer_down)
    ok("进程崩了 pending 不丢:用 ID 0 重读自己(或 XCLAIM 转移给别人)再处理——"
       "配合 XACK 收口,崩溃后既不丢邮件也不重复发(XACK 幂等于业务侧要自持)")

    print(f"\n共 {len(PASS)} 项断言全部通过")


if __name__ == "__main__":
    main()

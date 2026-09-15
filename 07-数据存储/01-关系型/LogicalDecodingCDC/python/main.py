#!/usr/bin/env python3
"""PostgreSQL 逻辑复制(logical decoding)与 CDC 最小模拟。

依据 postgresql.org/docs/current/logicaldecoding.html 与
debezium.io documentation/reference/stable/architecture.html 归纳:
- WAL → logical decoding 解码行级变更 → replication slot(持留 WAL 起点)
  → output plugin 输出 begin/insert/update/delete/commit 事件流。
- UPDATE/DELETE 能否拿到旧元组取决于 REPLICA IDENTITY(无 PK 时 old 为 null)。
- Debezium:Kafka Connect source connector 读 binlog(PG: logical stream),
  事件含 before/after/op;offset 断点续传;先 snapshot 后增量流。
"""
from collections import deque


class WALRecord:
    def __init__(self, lsn, kind, payload=None):
        self.lsn, self.kind, self.payload = lsn, kind, payload  # lsn: int


class LogicalSlot:
    """复制槽:记录 confirmed_flush_lsn,持留其后的 WAL 不被回收。"""

    def __init__(self, name):
        self.name = name
        self.confirmed_lsn = 0     # 消费者已确认刷到的 LSN
        self.streamed_lsn = 0       # 已解码输出到的 LSN
        self._pending = deque()     # 已输出但未 ACK 的逻辑事件

    def decode(self, wal):
        """output plugin:把 streamed_lsn 之后的 WAL 解码为逻辑变更事件。

        每个事件自带其 WAL 记录的 LSN —— 消费者按事件级 LSN 做 offset,
        而不是用整批解码的尾 LSN(否则批内后续事件会被误判已消费)。
        """
        events = []
        for rec in wal:
            if rec.lsn <= self.streamed_lsn:
                continue
            self.streamed_lsn = rec.lsn
            if rec.kind == "begin":
                events.append((rec.lsn, "begin", rec.payload["xid"]))
            elif rec.kind == "commit":
                events.append((rec.lsn, "commit", rec.payload["xid"]))
            elif rec.kind in ("insert", "update", "delete"):
                ev = {"op": rec.kind, "table": rec.payload["table"],
                      "after": rec.payload.get("after"),
                      "old": rec.payload.get("old")}   # old 依赖 replica identity
                events.append((rec.lsn, "change", ev))
        self._pending.extend(events)
        return events

    def ack(self, lsn):
        """消费者处理完 lsn 之前的事件后 ACK(flush LSN 前移,旧 WAL 才能回收)。"""
        self.confirmed_lsn = lsn

    def retention_lsn(self):
        """槽持留的 WAL 下界 = confirmed_lsn。"""
        return self.confirmed_lsn


class PG:
    """极简 PG:表 + WAL + REPLICA IDENTITY 设置。"""

    def __init__(self):
        self.tables = {}
        self.replica_identity = {}    # table -> "default"(PK) | "nothing"
        self.wal = []
        self._lsn = 0
        self._xid = 0

    def insert(self, table, pk, row):
        self._xid += 1
        xid = self._xid
        self._append("begin", {"xid": xid})
        self.tables.setdefault(table, {})[pk] = dict(row)
        self._append("insert", {"table": table, "after": {pk: dict(row)}})
        self._append("commit", {"xid": xid})

    def update(self, table, pk, row):
        old = dict(self.tables[table][pk])
        self.tables[table][pk] = dict(row)
        ident = self.replica_identity.get(table, "default")
        old_tuple = old if ident == "default" else None   # nothing → 不可见旧元组
        self._xid += 1
        self._append("update", {"table": table, "after": {pk: dict(row)}, "old": old_tuple})

    def delete(self, table, pk):
        old = self.tables[table].pop(pk)
        ident = self.replica_identity.get(table, "default")
        old_tuple = old if ident == "default" else None
        self._append("delete", {"table": table, "old": old_tuple})

    def _append(self, kind, payload):
        self._lsn += 1
        self.wal.append(WALRecord(self._lsn, kind, payload))


class DebeziumSink:
    """模拟 Debezium connector:按 offset 恢复,输出 Kafka 风格事件。"""

    def __init__(self, slot):
        self.slot = slot
        self.offset = 0              # 已消费的 LSN(offset 存在 connector 侧)
        self.events = []

    def poll(self, wal):
        """walsender 先解码 WAL → _pending 事件流;消费者从这里取走并处理。

        (直接消费已流式化的事件,而不是重新解码 —— 重启/慢消费者场景
        下,事件早已发出,只需从 pending 队列续取。)
        """
        self.slot.decode(wal)
        pending = list(self.slot._pending)
        self.slot._pending.clear()
        for lsn, kind, payload in pending:
            if lsn <= self.offset:
                continue             # offset 恢复后跳过已消费部分
            if kind == "change":
                self.events.append({
                    "op": "c" if payload["op"] == "insert" else
                          "u" if payload["op"] == "update" else "d",
                    "before": payload["old"],
                    "after": payload["after"],
                    "source": {"lsn": lsn, "slot": self.slot.name},
                })
            self.offset = lsn
        self.slot.ack(self.offset)  # 定期 ACK → confirmed_flush_lsn 前移

    def snapshot(self, pg, table):
        """初始全量 snapshot:先导当前状态,再无缝转增量流。"""
        for pk, row in pg.tables[table].items():
            self.events.append({"op": "r", "before": None, "after": {pk: dict(row)},
                                "source": {"snapshot": True}})


def main():
    pg = PG()
    slot = LogicalSlot("dbz_slot")

    # ---- 1. WAL 解码为逻辑事件流 ----
    pg.insert("orders", 1, {"amount": 100})
    pg.insert("orders", 2, {"amount": 200})
    pg.update("orders", 1, {"amount": 150})
    sink = DebeziumSink(slot)
    sink.poll(pg.wal)
    ops = [e["op"] for e in sink.events]
    assert ops == ["c", "c", "u"], ops
    assert sink.events[2]["after"] == {1: {"amount": 150}}
    assert sink.events[2]["before"] == {"amount": 100}

    # ---- 2. REPLICA IDENTITY:无 PK 时 UPDATE/DELETE 拿不到旧元组 ----
    pg.replica_identity["logs"] = "nothing"
    pg.insert("logs", 9, {"msg": "a"})
    sink.poll(pg.wal)
    assert sink.events[-1]["op"] == "c" and sink.events[-1]["after"] == {9: {"msg": "a"}}
    pg.update("logs", 9, {"msg": "b"})
    sink.poll(pg.wal)
    last = sink.events[-1]
    assert last["op"] == "u" and last["after"] == {9: {"msg": "b"}}
    assert last["before"] is None                  # old 为 null(replica identity)

    # ---- 3. 槽持留 WAL:未 ACK 前 WAL 不得回收 ----
    pg.insert("orders", 3, {"amount": 300})
    slot.decode(pg.wal)                    # 已解码但消费者未 ACK
    retain = slot.retention_lsn()
    assert retain < slot.streamed_lsn      # confirmed 落后 streamed → WAL 持留
    sink.poll(pg.wal)                      # 消费 + ACK
    assert slot.retention_lsn() == slot.streamed_lsn

    # ---- 4. offset 崩溃恢复:重启后从 offset 续传,不丢不重 ----
    sink2 = DebeziumSink(slot)             # 模拟重启:offset 来自 connector 存储
    sink2.offset = sink.offset
    pg.insert("orders", 4, {"amount": 400})
    sink2.poll(pg.wal)
    new_only = [e for e in sink2.events if e["source"]["lsn"] > sink.offset]
    assert len(new_only) == 1 and new_only[0]["after"] == {4: {"amount": 400}}

    # ---- 5. 初始 snapshot + 增量衔接 ----
    slot3 = LogicalSlot("fresh")
    sink3 = DebeziumSink(slot3)
    sink3.snapshot(pg, "orders")           # 先全量(op=r)
    assert all(e["op"] == "r" for e in sink3.events)
    assert len(sink3.events) == 4
    pg.insert("orders", 5, {"amount": 500})
    sink3.poll(pg.wal)                     # 后增量
    assert sink3.events[-1]["op"] == "c"

    print("ALL 7 DEMO-2 (LogicalDecodingCDC) ASSERTIONS PASSED")


if __name__ == "__main__":
    main()

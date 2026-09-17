# -*- coding: utf-8 -*-
"""lsm_check.py — 自检:逐条核对 leveldb/rocksdb 官方文档里的明文规则。

断言全部对应官方原文(出处见 lsm_engine.py / lsm_compact.py / lsm_formats.py 文件头),
不依赖任何第三方库。

模块分工:lsm_core(数据结构)/ lsm_compact(CompactionMixin)/ lsm_engine(LSMEngine)
/ lsm_formats(WAL 与 SSTable 格式)。这里按"用得最多的那个模块"导入,避免绕路。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lsm_core import DELETE, MB, Entry, compact_entries  # noqa: E402
from lsm_engine import LSMEngine  # noqa: E402
from lsm_formats import (  # noqa: E402
    BLOCK_SIZE, FILTER_BASE, FOOTER_SIZE, FULL, FIRST, LAST, MIDDLE,
    BlockHandle, LogWriter, RECORD_HEADER, decode_footer, decode_varint,
    encode_footer, encode_varint, filter_block_layout, filter_index_for_offset,
    footer_magic_hex, read_log, table_layout,
)

passed = 0
failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("  PASS ", label)
        return
    failed += 1
    print("  FAIL ", label, detail)


class FakeSizes(LSMEngine):
    """只用来验层级目标算法:把每层大小换成给定值。"""

    def __init__(self, sizes, **kw):
        super().__init__(**kw)
        self._sizes = sizes

    def level_size_bytes(self, level):
        return self._sizes.get(level, 0)


class FakeTable:
    """只提供 size_bytes 的占位文件,用于给某一层"灌"指定大小。"""

    def __init__(self, size):
        self._size = size

    def size_bytes(self):
        return self._size


def main():
    print("[1] WAL 日志格式:32KB 块 + 7 字节头部(官方 doc/log_format.md)")
    check("块大小 32768(32KB)", BLOCK_SIZE == 32768)
    check("头部 4+2+1=7 字节",
          RECORD_HEADER == 4 + 2 + 1)
    check("类型编号 FULL=1 FIRST=2 MIDDLE=3 LAST=4",
          (FULL, FIRST, MIDDLE, LAST) == (1, 2, 3, 4))

    print("[2] 官方示例:A(1000) / B(97270) / C(8000)")
    w = LogWriter()
    frag_a = w.append(1000)
    frag_b = w.append(97270)
    frag_c = w.append(8000)
    check("A 是一条 FULL 记录", len(frag_a) == 1 and frag_a[0].label() == "FULL")
    check("B 被切成 FIRST/MIDDLE/LAST 三段",
          [f.label() for f in frag_b] == ["FIRST", "MIDDLE", "LAST"],
          str([f.label() for f in frag_b]))
    check("B 的 FIRST 占满第一块剩余空间",
          frag_b[0].phys_len == BLOCK_SIZE - frag_a[0].phys_len)
    check("B 的 MIDDLE 独占整块", frag_b[1].phys_len == BLOCK_SIZE)
    check("C 是下一条 FULL 记录", len(frag_c) == 1 and frag_c[0].label() == "FULL")
    check("第三块空出 6 字节当 trailer(官方原文)", w.trailer_bytes == 6, str(w.trailer_bytes))
    check("C 落在第 4 个块(下标 3)", frag_c[0].block == 3, str(frag_c[0].block))
    check("用户记录可原样还原", [len(r) for r in read_log(w)] == [1000, 97270, 8000])

    print("[3] 两个边界规则")
    w2 = LogWriter()
    w2.offset = BLOCK_SIZE - 7
    f2 = w2.append(5)
    check("剩余恰好 7 字节 → 写零字节数据的 FIRST 填满该块(官方 Aside)",
          len(f2) == 2 and f2[0].label() == "FIRST" and f2[0].data_len == 0
          and f2[0].phys_len == 7, str([(x.label(), x.data_len) for x in f2]))
    check("随后在下一块写 LAST", f2[1].label() == "LAST" and f2[1].block == 1)
    starts_ok = True
    w3 = LogWriter()
    for n in (100, 900, 40000, 20):
        for f in w3.append(n):
            if BLOCK_SIZE - f.offset < RECORD_HEADER:
                starts_ok = False
    check("任何记录都不会从块最后 6 字节内开始", starts_ok)

    print("[4] varint 与 BlockHandle")
    check("150 → 0x96 0x01(protobuf 同款 varint)",
          encode_varint(150).hex() == "9601", encode_varint(150).hex())
    vals = [0, 1, 127, 128, 300, 16383, 16384, 1 << 40]
    check("varint 往返一致",
          all(decode_varint(encode_varint(v))[0] == v for v in vals))
    check("varint 解码返回正确的新位置",
          decode_varint(encode_varint(300))[1] == len(encode_varint(300)))
    try:
        decode_varint(b"\xff" * 11)
        check("超过 10 字节的 varint 视为损坏", False)
    except ValueError:
        check("超过 10 字节的 varint 视为损坏", True)

    print("[5] SSTable footer(官方 doc/table_format.md)")
    check("footer 定长 48 字节(2*20 + 8)", FOOTER_SIZE == 48)
    check("magic == 0xdb4775248b80fb57", footer_magic_hex() == "0xdb4775248b80fb57",
          footer_magic_hex())
    f = encode_footer(BlockHandle(100, 20), BlockHandle(200, 30))
    check("footer 长度为 48", len(f) == 48)
    meta, idx = decode_footer(f)
    check("metaindex / index handle 往返一致",
          (meta.offset, meta.size, idx.offset, idx.size) == (100, 20, 200, 30))
    try:
        decode_footer(b"\x00" * 48)
        check("magic 不符时报错", False)
    except ValueError:
        check("magic 不符时报错", True)
    lay = table_layout([1000, 2000], [50], 64)
    check("布局顺序 data → meta → metaindex → index → footer",
          lay["data_offset"] == 0 and lay["meta_offset"] == 3000
          and lay["index_offset"] > lay["metaindex_offset"] > lay["meta_offset"])
    check("footer 起于 file_size - 48(官方原文)",
          lay["footer_starts_at"] == lay["file_size"] - FOOTER_SIZE)

    print("[6] filter 元块:base 2KB")
    check("base 常量是 2048", FILTER_BASE == 2048)
    check("偏移 0 与 2047 落第 0 个 filter",
          filter_index_for_offset(0) == 0 and filter_index_for_offset(2047) == 0)
    check("偏移 2048 落第 1 个 filter", filter_index_for_offset(2048) == 1)
    fl = filter_block_layout(3)
    check("块尾顺序:偏移数组 → 数组起点 → lg(base)",
          fl["trailer_positions"] == 4 * 3 + 5 and fl["array_anchor"] == 4)
    check("lg(base) == 11", fl["lg_base_value"] == 11)

    print("[7] 层级目标:静态(LevelDB 10^L MB)")
    e = LSMEngine(max_bytes_for_level_base=10 * MB, num_levels=7)
    check("L1 目标 = max_bytes_for_level_base", e.level_target(1) == 10 * MB)
    check("L2 目标 100MB / L3 目标 1000MB",
          e.level_target(2) == 100 * MB and e.level_target(3) == 1000 * MB)
    check("multiplier 默认 10", e.max_bytes_for_level_multiplier == 10)

    print("[8] 层级目标:动态层级(官方示例 base=1GB / 末层 276GB)")
    GB = 1024 * MB
    # 口径说明:wiki 示例写的是 num_levels=6 而层级标号为 L1..L6;本项目按代码口径
    # 把 num_levels 解释为"含 L0 的总层数",故取 num_levels=7 → 非 0 层正好是 L1..L6。
    fk = FakeSizes({6: 276 * GB}, num_levels=7, max_bytes_for_level_base=1 * GB,
                   dynamic_level_bytes=True)
    t = fk.dynamic_level_targets()
    check("末层(L6)目标 = 末层实际大小 276GB", t[6] == 276 * GB)
    check("逐层除以 10:L5=27.6GB L4=2.76GB L3=0.276GB",
          t[6] // 10 == t[5] and t[5] // 10 == t[4] and t[4] // 10 == t[3], str(t))
    check("低于 base/multiplier(0.1GB)的层保持空 → L1=L2=0", t[1] == 0 and t[2] == 0, str(t))
    check("官方示例的六个数 0/0/0.276/2.76/27.6/276 GB",
          [round(t[l] / GB, 3) for l in range(1, 7)] == [0, 0, 0.276, 2.76, 27.6, 276],
          str([round(t[l] / GB, 3) for l in range(1, 7)]))
    total = sum(t.values())
    check("保证约 90% 数据落在末层(官方原文)",
          abs(t[6] / total - 0.9) < 0.02, "%.4f" % (t[6] / total))

    print("[9] 写停顿三层判定(官方 wiki/Write-Stalls.md)")
    e2 = LSMEngine(max_write_buffer_number=5)
    e2.immutables = [None] * 4
    check("max=5 且有 4 个不可变 memtable → stall(提前一个)",
          e2.write_stall_state() == ("stall", "too_many_immutable_memtables"))
    e2.immutables = [None] * 5
    check("达到 max_write_buffer_number → 完全停止",
          e2.write_stall_state() == ("stop", "too_many_immutable_memtables"))
    e3 = LSMEngine(max_write_buffer_number=2)
    e3.immutables = [None]
    check("max=2 时 1 个不可变不 stall(不适用 >3 的提前规则)",
          e3.write_stall_state()[0] == "ok", str(e3.write_stall_state()))
    e4 = LSMEngine(level0_slowdown_writes_trigger=4, level0_stop_writes_trigger=20)
    e4.levels[0] = [None] * 4
    check("L0 达到 slowdown 触发数 → stall",
          e4.write_stall_state() == ("stall", "too_many_level0_files"))
    e4.levels[0] = [None] * 20
    check("L0 达到 stop 触发数 → 停止",
          e4.write_stall_state() == ("stop", "too_many_level0_files"))
    e4.levels[0] = []
    e4.pending_compaction_bytes = e4.soft_pending_compaction_bytes
    check("待压缩字节达软限 → stall", e4.write_stall_state()[0] == "stall")
    e4.pending_compaction_bytes = e4.hard_pending_compaction_bytes
    check("待压缩字节达硬限 → 停止", e4.write_stall_state()[0] == "stop")

    print("[10] 写路径与删除标记")
    e5 = LSMEngine(write_buffer_size=4096, max_write_buffer_number=4)
    e5.put("k1", "v1")
    check("memtable 立即可读", e5.get("k1") == "v1")
    e5.put("k1", "v2")
    check("同一 key 后写覆盖前写", e5.get("k1") == "v2")
    e5.delete("k1")
    check("删除标记遮住 memtable 里的旧值", e5.get("k1") is None)
    e5.put("k2", "v2")
    check("删除不影响其他 key", e5.get("k2") == "v2")

    print("[11] flush 与 L0 → L1 compaction")
    e6 = LSMEngine(write_buffer_size=200, max_write_buffer_number=4,
                   level0_file_num_compaction_trigger=4, num_levels=4,
                   target_file_size_base=100000, max_bytes_for_level_base=100000000)
    for i in range(40):
        e6.put("k%03d" % i, "v" * 40)
    check("memtable 满后自动 flush 出 L0 文件", len(e6.levels[0]) > 0
          or len(e6.levels[1]) > 0, e6.level_summary())
    check("L0 攒够触发数后压到 L1", len(e6.levels[1]) > 0, e6.level_summary())
    check("所有 key 仍可读到", all(e6.get("k%03d" % i) == "v" * 40 for i in range(40)))
    check("写放大 >= 1(压缩会重写数据)", e6.write_amplification() >= 1.0,
          "%.2f" % e6.write_amplification())

    print("[12] 得分与挑层")
    e7 = LSMEngine(level0_file_num_compaction_trigger=4, max_bytes_for_level_base=100 * MB)
    e7.levels[0] = [FakeTable(10)] * 3
    check("L0 未达触发数 → 得分为 0(官方原文)", e7.compaction_scores()[0] == 0.0)
    e7.levels[0] = [FakeTable(10)] * 4
    check("L0 达触发数 → 得分 >= 1", e7.compaction_scores()[0] >= 1.0)
    check("pick_level 取最高分那一层", e7.pick_level() == 0)
    e7.levels[1] = [FakeTable(300 * MB)]
    check("L1 超过目标 → L1 得分 > 1",
          e7.compaction_scores()[1] > 1.0, "%.2f" % e7.compaction_scores()[1])
    check("两层都超时取分高者", e7.pick_level() == 1,
          str({k: round(v, 2) for k, v in e7.compaction_scores().items()}))

    print("[13] 删除标记的丢弃条件(LevelDB doc/impl.md 原文)")
    eng = LSMEngine(num_levels=4)
    check("更深层无文件覆盖该 key → 可丢删除标记", eng.can_drop_delete("k1", 1))
    deep = SSTableFactory("k1", "k9", level=3)
    eng.levels[3] = [deep]
    check("更深层(L3)有文件覆盖该 key → 不可丢", not eng.can_drop_delete("k1", 1))
    check("范围不覆盖时仍可丢", eng.can_drop_delete("z9", 1))
    ents = [Entry("k1", 3, DELETE, ""), Entry("k1", 1, 0, "old")]
    check("压缩只保留每个 key 的最新版本",
          [e.seq for e in compact_entries(ents, lambda k: False)] == [3])
    check("允许丢删除标记时,标记被移除",
          compact_entries(ents, lambda k: True) == [])
    check("被弃的旧值不写盘,写放大因此下降",
          len(compact_entries([Entry("a", 2, 0, "new"), Entry("a", 1, 0, "old")],
                              lambda k: False)) == 1)

    print("")
    print("断言总数 %d,失败 %d" % (passed + failed, failed))
    if failed:
        return 1
    print("全部通过")
    return 0


def SSTableFactory(lo, hi, level):
    from lsm_engine import SSTable
    return SSTable(99, level, [Entry(lo, 1, 0, "x"), Entry(hi, 2, 0, "y")])


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""lsm_compact.py — 分层目标 / 挑层 / 压缩执行 / 放大系数(CompactionMixin)。

以 mixin 形式承载 LSMEngine 的一部分方法:方法体缩进与语义都不变,
只是换了个文件;宿主类提供 levels / num_levels 等属性。
权威来源与出处见 lsm_engine.py 文件头。
"""

from typing import Dict, List, Optional

from lsm_core import Entry, SSTable, compact_entries


class CompactionMixin:
    """LSMEngine 的压缩与统计部分。"""

    # ------------------------------------------------------------ 分层目标
    def static_level_target(self, level: int) -> int:
        """LevelDB 口径:level-L 的上限是 10^L MB。"""
        if level <= 0:
            return self.max_bytes_for_level_base
        return self.max_bytes_for_level_base * (self.max_bytes_for_level_multiplier ** (level - 1))

    def dynamic_level_targets(self) -> Dict[int, int]:
        """RocksDB 动态层级:末层目标 = 末层实际大小,逐层除以 multiplier。"""
        last = self.num_levels - 1
        targets: Dict[int, int] = {last: self.level_size_bytes(last)}
        v = float(targets[last])
        for l in range(last - 1, 0, -1):
            v = v / self.max_bytes_for_level_multiplier
            targets[l] = int(v)
        floor = self.max_bytes_for_level_base / self.max_bytes_for_level_multiplier
        for l in list(targets):
            if targets[l] < floor:
                targets[l] = 0  # 官方:目标低于 base/multiplier 的层保持空
        return targets

    def level_target(self, level: int) -> int:
        if level <= 0:
            return self.max_bytes_for_level_base
        if self.dynamic_level_bytes:
            return self.dynamic_level_targets().get(level, 0)
        return self.static_level_target(level)

    def level_size_bytes(self, level: int) -> int:
        return sum(t.size_bytes() for t in self.levels[level])

    # ------------------------------------------------------------ compaction
    def compaction_scores(self) -> Dict[int, float]:
        """得分越高越该压。L0 未达触发文件数时官方不触发,得分记 0。"""
        scores: Dict[int, float] = {}
        l0 = self.levels[0]
        base = self.max_bytes_for_level_base
        if len(l0) < self.level0_file_num_compaction_trigger:
            scores[0] = 0.0
        else:
            scores[0] = max(
                len(l0) / self.level0_file_num_compaction_trigger,
                self.level_size_bytes(0) / base,
            )
        for l in range(1, self.num_levels):
            tgt = self.level_target(l)
            if tgt <= 0:
                continue
            denom = float(tgt)
            if self.dynamic_level_bytes:
                denom += self.total_downcompact_bytes(l)
            scores[l] = self.level_size_bytes(l) / denom
        return scores

    def total_downcompact_bytes(self, level: int) -> int:
        """官方:估算从 L0..L(n-1) 压下来的总字节数(compaction 债务)。"""
        return sum(self.level_size_bytes(l) for l in range(level))

    def pick_level(self) -> Optional[int]:
        scores = self.compaction_scores()
        if not scores:
            return None
        level = max(scores, key=lambda l: scores[l])
        return level if scores[level] >= 1.0 else None

    def maybe_compact(self) -> bool:
        level = self.pick_level()
        if level is None:
            return False
        if level == self.num_levels - 1:
            return False  # 末层无处可去
        self.compact_level(level)
        return True

    def compact_level(self, level: int) -> None:
        if level == 0:
            picked = list(self.levels[0])  # L0 文件互相重叠,通常全取
        else:
            picked = [self.levels[level][0]]
        lo = min(t.smallest() for t in picked)
        hi = max(t.largest() for t in picked)
        nxt = [t for t in self.levels[level + 1] if t.overlaps(lo, hi)]
        inputs = picked + sorted(nxt, key=lambda t: t.number)
        self.levels[level] = [t for t in self.levels[level] if t not in picked]
        self.levels[level + 1] = [t for t in self.levels[level + 1] if t not in nxt]

        entries = [e for t in inputs for e in t.entries]
        merged = compact_entries(entries, can_drop_delete=lambda k: self.can_drop_delete(k, level + 1))
        for chunk in self.split_output(merged, level + 1):
            sst = SSTable(self.next_file_number, level + 1, chunk)
            self.next_file_number += 1
            self.levels[level + 1].append(sst)
            self.disk_bytes_written += sst.size_bytes()
        self.levels[level + 1].sort(key=lambda t: t.smallest())
        self.pending_compaction_bytes = max(0, self.pending_compaction_bytes - sum(
            t.size_bytes() for t in inputs))

    def can_drop_delete(self, key: str, output_level: int) -> bool:
        """删除标记只有在更深层没有文件覆盖该 key 时才能丢(LevelDB doc/impl.md 原文)。"""
        for l in range(output_level + 1, self.num_levels):
            for t in self.levels[l]:
                if t.overlaps(key, key):
                    return False
        return True

    def split_output(self, entries: List[Entry], level: int) -> List[List[Entry]]:
        """输出文件按 target_file_size_base 切分(LevelDB:每个 L1 文件 2MB)。"""
        out: List[List[Entry]] = []
        cur: List[Entry] = []
        size = 0
        for e in entries:
            cur.append(e)
            size += e.size_bytes()
            if size >= self.target_file_size_base:
                out.append(cur)
                cur, size = [], 0
        if cur:
            out.append(cur)
        return out

    # ------------------------------------------------------------ 统计
    def write_amplification(self) -> float:
        if self.user_bytes_written == 0:
            return 0.0
        return self.disk_bytes_written / self.user_bytes_written

    def space_amplification(self) -> float:
        logical = {e.key: e for e in self.mem.entries}
        for mt in self.immutables:
            for e in mt.entries:
                cur = logical.get(e.key)
                if cur is None or e.seq > cur.seq:
                    logical[e.key] = e
        for l in range(self.num_levels):
            for t in self.levels[l]:
                for e in t.entries:
                    cur = logical.get(e.key)
                    if cur is None or e.seq > cur.seq:
                        logical[e.key] = e
        live = sum(1 for e in logical.values() if not e.is_delete())
        return live and (self.total_size_bytes() / float(live)) or 0.0

    def total_size_bytes(self) -> int:
        return sum(self.level_size_bytes(l) for l in range(self.num_levels))

    def level_summary(self) -> str:
        return " ".join(
            "L%d=%d" % (l, len(self.levels[l]))
            for l in range(self.num_levels)
            if self.levels[l]
        )

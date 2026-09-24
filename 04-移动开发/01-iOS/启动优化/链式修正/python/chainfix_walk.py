"""链起点解码与链遍历:转写自 ChainedFixups.cpp 的两个 forEach 与 imports 解析。"""

from chainfix_const import (bits, sign_extend, Error, START_NONE, START_MULTI,
                            START_LAST, IMPORT, IMPORT_ADDEND, IMPORT_ADDEND64)
from chainfix_format import make_format


class SegmentStarts:
    """dyld_chained_starts_in_segment 的等价物。"""

    def __init__(self, page_size, pointer_format, page_start, page_count=None):
        self.page_size = page_size
        self.pointer_format = pointer_format
        # page_start 数组尾部可跟 MULTI 的 overflow 区,故 page_count 一般小于数组长度
        self.page_start = list(page_start)
        self.page_count = len(self.page_start) if page_count is None else page_count


class Segment:
    def __init__(self, index, content, page_size):
        self.index = index
        self.content = content  # dict: offset -> 原始条目值
        self.page_size = page_size


def chain_starts_on_page(seg_starts, page_index):
    """复刻 forEachFixupChainStartLocation 里 page_start 的三态解码。

    顺序很关键:0xFFFF 同时满足 NONE 与 MULTI,先按 NONE 拦截。
    """
    ps = seg_starts.page_start[page_index]
    if ps == START_NONE:
        return []
    if ps & START_MULTI:
        idx = ps & ~START_MULTI & 0xFFFF
        out = []
        while True:
            entry = seg_starts.page_start[idx]
            last = bool(entry & START_LAST)
            out.append(entry & ~START_LAST & 0xFFFF)
            idx += 1
            if last:
                break
        return out
    return [ps]


def for_each_chain_start(segments, starts):
    """产出 (segment, page_index, offset_in_page, format)。"""
    for seg_index, seg in enumerate(segments):
        st = starts.get(seg_index)
        if st is None:
            continue
        pf = make_format(st.pointer_format)
        for page_index in range(st.page_count):
            for off in chain_starts_on_page(st, page_index):
                yield seg, page_index, off, pf


def for_each_fixup_in_chain(pf, seg, chain_start_offset, page_index, pref=0):
    """复刻 forEachFixupLocationInChain:先算 next 再回调,next 出页即断。

    两个边界:链起点不在本页 → 整条链丢弃;next 严格大于页末地址 → 断链
    (恰好等于页末地址不算出页)。firmware 传 seg=nullptr 时不做页边界检查。
    """
    page_size = seg.page_size
    start_page = page_index * page_size
    end_page = start_page + page_size
    if chain_start_offset < start_page or chain_start_offset > end_page:
        return []  # 源码: chain is not on page
    out = []
    loc = chain_start_offset
    while loc is not None:
        raw = seg.content.get(loc)
        if raw is None:
            raise Error("no content at %d" % loc)
        nxt = pf.next_location(loc, raw)
        out.append((loc, pf.parse(raw, pref)))
        if nxt is not None and nxt > end_page:
            break  # 源码: chain went off end of page
        loc = nxt
    return out


def parse_import(words, i, imports_format):
    """DYLD_CHAINED_IMPORT*:返回 (dict, 下一个下标)。符号名另存于 symbols 区。"""
    w = words[i]
    if imports_format == IMPORT:
        return {"lib_ordinal": sign_extend(bits(w, 0, 7), 8),
                "weak_import": bits(w, 8, 8), "name_offset": bits(w, 9, 31)}, i + 1
    if imports_format == IMPORT_ADDEND:
        d = {"lib_ordinal": sign_extend(bits(w, 0, 7), 8),
             "weak_import": bits(w, 8, 8), "name_offset": bits(w, 9, 31)}
        d["addend"] = sign_extend(words[i + 1] & 0xFFFFFFFF, 32)
        return d, i + 2
    if imports_format == IMPORT_ADDEND64:
        d = {"lib_ordinal": sign_extend(bits(w, 0, 15), 16),
             "weak_import": bits(w, 16, 16),
             "reserved": bits(w, 17, 31),
             "name_offset": bits(w, 32, 63)}
        d["addend"] = sign_extend(words[i + 1], 64)
        return d, i + 2
    raise Error("bad imports_format %d" % imports_format)

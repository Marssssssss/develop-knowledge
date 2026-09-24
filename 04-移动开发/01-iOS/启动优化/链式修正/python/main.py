"""演示:拼一个有两页修正的假段,按 dyld 的方式走完所有链。

运行: python main.py
"""

from chainfix_model import (
    Fixup, Fmt64, Segment, SegmentStarts, START_NONE,
    chain_starts_on_page, for_each_chain_start, for_each_fixup_in_chain,
    parse_import, PTR_64, IMPORT_ADDEND,
)

PREF = 0x1000000
PAGE = 0x1000


def build():
    """第 0 页:两个链(第二个靠 MULTI 溢出区描述);第 1 页:无修正。"""
    f = Fmt64()
    seg = Segment(0, {}, PAGE)

    # 链 A:0x100 -> 0x108 -> 0x110(末项 next=0 终止)
    seg.content[0x100] = f.write(Fixup(target=0x2100), 8, PREF)
    seg.content[0x108] = f.write(Fixup(target=0x3100), 8, PREF)
    seg.content[0x110] = f.write(Fixup(is_bind=True, ordinal=7, addend=16), 0, PREF)

    # 链 B:0x900 -> 0x904
    seg.content[0x900] = f.write(Fixup(target=0x2900), 4, PREF)
    seg.content[0x904] = f.write(Fixup(is_bind=True, ordinal=3, addend=0), 0, PREF)

    # page_start 前 page_count 项是每页一项,后面才是 MULTI 的 overflow 区。
    # 这里 page[0] 置 MULTI 位,overflow 区从下标 2 起:0x100(非末项) + 0x900(带 LAST)
    starts = SegmentStarts(PAGE, PTR_64,
                           [0x8002, START_NONE, 0x0100, 0x8900], page_count=2)
    return seg, starts


def main():
    seg, starts = build()
    print("page_size=0x%x pointer_format=%d page_count=%d"
          % (starts.page_size, starts.pointer_format, starts.page_count))
    for page_index in range(starts.page_count):
        print("  page[%d] chain starts = %s"
              % (page_index, [hex(x) for x in chain_starts_on_page(starts, page_index)]))

    total = 0
    for _, page_index, off, pf in for_each_chain_start([seg], {0: starts}):
        base = page_index * PAGE
        print("\nchain at page[%d]+0x%x (abs 0x%x)" % (page_index, off, base + off))
        for loc, fx in for_each_fixup_in_chain(pf, seg, base + off, page_index, PREF):
            total += 1
            if fx.is_bind:
                print("  0x%04x bind   ordinal=%d addend=%d"
                      % (loc, fx.ordinal, fx.addend))
            else:
                print("  0x%04x rebase target=0x%x" % (loc, fx.target))
    print("\ntotal fixups =", total)

    # 一个 MULTI 页如果写成普通偏移,第二根链会被静默漏掉
    plain = SegmentStarts(PAGE, PTR_64, [0x0100, START_NONE], page_count=2)
    n_plain = sum(1 for _, pi, off, pf in for_each_chain_start([seg], {0: plain})
                  for _ in for_each_fixup_in_chain(pf, seg, pi * PAGE + off, pi, PREF))
    print("若把 page_start[0] 写成单起点 0x100,则只走完 %d 个修正(漏掉链 B)" % n_plain)

    # imports 表
    d, _ = parse_import([(0x20 << 9) | (1 << 8) | 0xF6, 0xFFFFFFFFFFFFFFF0], 0, IMPORT_ADDEND)
    print("\nimport: lib_ordinal=%d weak=%d name_offset=0x%x addend=%d"
          % (d["lib_ordinal"], d["weak_import"], d["name_offset"], d["addend"]))

    # 空页
    empty = SegmentStarts(PAGE, PTR_64, [START_NONE], page_count=1)
    print("page_start=0xFFFF ->", chain_starts_on_page(empty, 0))


if __name__ == "__main__":
    main()

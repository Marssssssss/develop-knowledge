"""覆盖率引导模糊测试演示：跑一轮迷你 fuzzing  campaign，看覆盖率是怎么长出来的。"""

import random

from afl import (
    MAP_SIZE,
    CAL_CYCLES,
    Coverage,
    QueueEntry,
    classify_counts,
    count_class,
    cull_queue,
    has_new_bits,
    new_virgin,
    run_target,
    trim,
)


def active_tuples(trace):
    return {i for i, v in enumerate(trace) if v}


def campaign(seed, rounds=4000, seed_rand=20260922):
    """一次极简的 fuzz 循环：随机变异 -> 看有没有新覆盖 -> 有就入队。"""
    rnd = random.Random(seed_rand)
    cov = Coverage()
    virgin = new_virgin(MAP_SIZE)
    queue = []
    data = bytearray(seed)
    for _ in range(rounds):
        candidate = bytearray(data)
        for _ in range(rnd.randint(1, 4)):
            if not candidate:
                break
            pos = rnd.randrange(len(candidate))
            candidate[pos] = (candidate[pos] + rnd.choice([1, -1, 16, 64, 128])) & 0xFF
        run_target(bytes(candidate), cov)
        trace = classify_counts(cov.trace_bits)
        verdict = has_new_bits(trace, virgin)
        if verdict:
            queue.append(QueueEntry("id:%d" % len(queue), bytes(candidate),
                                    active_tuples(trace), exec_us=len(candidate)))
            data = candidate
        elif len(candidate) <= len(data):
            data = candidate
    return queue


def demo():
    print("覆盖率引导模糊测试：边覆盖位图 + 命中数分桶")
    print()
    print("1) 命中数分桶（afl-fuzz.c 的 count_class_lookup8）")
    print("   桶: 1 / 2 / 3 / 4-7 / 8-15 / 16-31 / 32-127 / 128+")
    for n in (1, 2, 3, 4, 7, 8, 15, 16, 31, 32, 127, 128, 255):
        print("   %4d -> %3d" % (n, count_class(n)))
    print("   31 与 32 跨桶（%d != %d），47 与 48 同桶（%d == %d）"
          % (count_class(31), count_class(32), count_class(47), count_class(48)))

    print()
    print("2) 一次迷你 fuzzing campaign（种子 b'A'，4000 轮随机变异）")
    queue = campaign(b"A")
    print("   入队用例数: %d" % len(queue))
    print("   覆盖的边数: %d" % len(set().union(*[q.tuples for q in queue])) if queue else 0)
    first = queue[0] if queue else None
    last = queue[-1] if queue else None
    if first:
        print("   首个新覆盖: %r（%d 字节）" % (first.data, len(first.data)))
        print("   末个新覆盖: %r（%d 字节）" % (last.data, len(last.data)))

    print()
    print("3) 队列裁剪（贪心集合覆盖，score = 延迟 × 大小）")
    entries = [
        QueueEntry("slow-big", b"x" * 1000, {1, 2, 3}, exec_us=1000),
        QueueEntry("fast-cover", b"y" * 10, {3, 4}, exec_us=10),
        QueueEntry("redundant", b"z" * 20, {1, 2}, exec_us=50),
        QueueEntry("unique", b"w" * 5, {9}, exec_us=20),
    ]
    favored = cull_queue(entries)
    print("   全部 %d 条 -> favored %d 条: %s"
          % (len(entries), len(favored), sorted(e.name for e in favored)))
    for e in entries:
        print("     %-11s score=%-8d favored=%s" % (e.name, e.score(), e.favored))

    print()
    print("4) trimming：删掉不影响执行路径的数据")
    def dead_tail(data):
        return run_target(bytes(b for b in data if b != ord("Z"))).checksum()

    padded = bytes([0x08, 0x18, 0x28, 0x38]) + b"Z" * 12
    out = trim(padded, dead_tail)
    print("   %d 字节 -> %d 字节: %r" % (len(padded), len(out), out))
    print("   校验和一致: %s" % (dead_tail(out) == dead_tail(padded)))

    print()
    print("5) 常量：MAP_SIZE=%d（2^16），校准每用例跑 %d 遍" % (MAP_SIZE, CAL_CYCLES))


if __name__ == "__main__":
    demo()

#!/usr/bin/env python3
"""archive_append.py — 归档文件的「追加 + 滚动截断」工具

用法:
    python archive_append.py <target> <max_lines> <rows_file>

把 <rows_file> 的非空行追加到 <target> 末尾,然后只保留最后 <max_lines> 行
(丢弃的更早内容可用 `git log -p` 回溯,见 STATE.md §四)。

规范来源:`_docs/OPTIMIZATION.md` §2.3 —— completed.md > 100 行 / schedule.md > 30 条即截断。
每轮巡检收尾都要执行一次,故抽成工具避免「先 Edit 追加、再手工删首行」的重复劳动与错漏。

示例:
    python _docs/tools/archive_append.py _docs/archive/completed.md 100 /path/rows.md
"""
import pathlib
import sys


def main() -> int:
    if len(sys.argv) != 4:
        print(__doc__)
        return 2
    target, max_lines, rows_file = pathlib.Path(sys.argv[1]), int(sys.argv[2]), pathlib.Path(sys.argv[3])

    old = target.read_text(encoding='utf-8').splitlines()
    rows = [line for line in rows_file.read_text(encoding='utf-8').splitlines() if line.strip()]
    merged = old + rows
    total = len(merged)
    dropped = 0
    if total > max_lines:
        dropped = total - max_lines
        merged = merged[-max_lines:]
    target.write_text('\n'.join(merged) + '\n', encoding='utf-8')
    print(f"{target.name}: {len(old)} + {len(rows)} = {total} 行 -> 保留 {len(merged)} 行(丢弃最早 {dropped} 行)")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

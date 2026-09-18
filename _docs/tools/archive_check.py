#!/usr/bin/env python3
"""归档表完整性断言:每行以 | 开头、无空行、字段数一致(按未转义竖线统计)。

用法:python archive_check.py <file> <期望字段数>
"""
import pathlib
import sys


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    path, fields = pathlib.Path(sys.argv[1]), int(sys.argv[2])
    lines = path.read_text(encoding='utf-8').splitlines()
    problems = []
    if not lines:
        problems.append('文件为空')
    for i, line in enumerate(lines, 1):
        if not line.strip():
            problems.append(f'第 {i} 行为空行')
        elif not line.startswith('|'):
            problems.append(f'第 {i} 行不以 | 开头')
    counts = {}
    for i, line in enumerate(lines, 1):
        n = line.replace('\\|', '').count('|')
        counts.setdefault(n, []).append(i)
    # 字段数 = 未转义竖线数 - 1
    got = sorted(counts)
    bad = [k for k in got if k - 1 != fields]
    if bad:
        for k in bad:
            problems.append(f'未转义竖线 {k} 个(应 {fields + 1})的行: {counts[k][:8]}{" ..." if len(counts[k]) > 8 else ""}')
    tag = 'OK' if not problems else 'FAIL'
    print(f'{path}: {len(lines)} 行, 字段数分布 {[(k - 1, len(v)) for k, v in sorted(counts.items())]} -> {tag}')
    for p in problems:
        print('  -', p)
    return 0 if not problems else 1


if __name__ == '__main__':
    raise SystemExit(main())

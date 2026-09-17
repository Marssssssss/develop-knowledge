# -*- coding: utf-8 -*-
"""go tool pprof top 解读复刻：flat vs cum 聚合、栈深截断、-cum 排序、nodefraction 去噪。

口径（Go 官方博客 Profiling Go Programs，本轮实读）：
  - CPU 剖析开启后，Go 程序**每秒约 100 次**停下并记录当前 goroutine 栈上的程序计数器
  - top 各列：前两列 = 该函数**正在运行**（而非等待被调函数返回）的样本数（原始计数 + 占比）
    第三列 = 列表内的**累计百分比**（running total）
    第四五列 = 该函数**出现在调用栈上**（运行中或等待被调函数返回）的样本数与占比（cum）
  - 每个栈样本只含**靠执行侧的 100 帧**：递归深于 100 帧时根帧（main.main 等）被截掉，
    故 main.main 的 cum 不足 100%（博客实例 84.9%）
  - top 默认按 flat 排序；-cum 按第四五列（累计）排序
  - --nodefraction=0.1：忽略占比不足 10% 的节点（博客 web 命令去噪用法）
"""
MAX_STACK = 100  # 每个栈样本保留的最大帧数（靠执行侧）


def truncate(stack):
    """栈以根在前、叶子在末尾表示；截断保留靠执行侧（叶子侧）的 100 帧。

    依据：博客实例中递归 main.DFS 深于 main.main 100 帧以上时，
    main.main / runtime.main / gosched0 的 cum 只剩 84.9% —— 说明被丢弃的是根侧。
    """
    if len(stack) <= MAX_STACK:
        return stack
    return stack[-MAX_STACK:]


def aggregate(samples):
    """flat = 该函数为叶子帧的样本数（正在运行）；cum = 该函数出现在栈上任一位置的样本数。"""
    flat, cum = {}, {}
    for stack in samples:
        t = truncate(stack)
        leaf = t[-1]
        flat[leaf] = flat.get(leaf, 0) + 1
        for f in set(t):
            cum[f] = cum.get(f, 0) + 1
    total = len(samples)
    return flat, cum, total


def pct(x, total):
    return 100.0 * x / total


def top(samples, n=10, by_cum=False, nodefraction=0.0):
    """复刻 top 输出：默认按 flat 降序；-cum 按累计降序；nodefraction 按 cum 占比过滤。"""
    flat, cum, total = aggregate(samples)
    names = [f for f in cum if pct(cum[f], total) >= nodefraction * 100.0]
    # 排序：主键 flat 或 cum 降序，同值按名字典序保证确定性（pprof 输出顺序稳定）
    names.sort(key=lambda f: (-cum[f] if by_cum else -flat.get(f, 0), f))
    rows, running = [], 0.0
    for f in names[:n]:
        fl = flat.get(f, 0)
        running += pct(fl, total)
        rows.append("%7d %5.1f%% %5.1f%% %8d %5.1f%% %s" %
                    (fl, pct(fl, total), running, cum[f], pct(cum[f], total), f))
    return rows, total


def duration_estimate(total_samples):
    """100 样本/秒（官方口径）→ 程序约运行 total_samples/100 秒。"""
    return total_samples / 100.0


def blog_dataset():
    """构造与博客同构的数据集（总量 1000，便于手算）：
    300 × main.main→FindLoops→mapaccess（叶子 mapaccess）
    200 × main.main→FindLoops（叶子 FindLoops）
    100 × main.main→DFS（叶子 DFS，浅递归）
    400 × main.main→DFS×120（深递归：截断后根侧 main.main 丢失）
    """
    samples = []
    samples += [["main.main", "main.FindLoops", "runtime.mapaccess1_fast64"]] * 300
    samples += [["main.main", "main.FindLoops"]] * 200
    samples += [["main.main", "main.DFS"]] * 100
    samples += [["main.main"] + ["main.DFS"] * 120] * 400
    return samples


def main():
    samples = blog_dataset()
    flat, cum, total = aggregate(samples)

    # 1. 总量与时长换算：2525 样本 ≈ 25.25 秒（博客口径 2525 样本"运行了 25 秒多一点"）
    assert total == 1000
    assert abs(duration_estimate(2525) - 25.25) < 1e-9
    assert abs(duration_estimate(total) - 10.0) < 1e-9

    # 2. flat：只记叶子帧（正在运行的函数）
    assert flat == {"runtime.mapaccess1_fast64": 300, "main.FindLoops": 200,
                    "main.DFS": 100 + 400}, flat
    assert "main.main" not in flat, "根帧从不是叶子，flat 恒为 0（不出现在 flat 表）"

    # 3. cum：出现即计；深递归 400 个样本里 main.main 被截断丢失
    assert cum["main.main"] == 300 + 200 + 100 == 600
    assert cum["main.FindLoops"] == 500 and cum["main.DFS"] == 500
    assert cum["runtime.mapaccess1_fast64"] == 300

    # 4. 截断语义：>100 帧保留叶子侧、丢根侧；恰好 100 帧原样保留
    deep = ["main.main"] + ["main.DFS"] * 120
    assert truncate(deep) == ["main.DFS"] * 100 and "main.main" not in truncate(deep)
    exact = ["A"] + ["B"] * 99
    assert truncate(exact) == exact
    over = ["A", "B"] + ["C"] * 100          # 102 帧 → 丢 A、B
    t = truncate(over)
    assert t == ["C"] * 100 and "A" not in t and "B" not in t

    # 5. top 默认：按 flat 降序；三列百分比 = flat%、running%、cum%
    rows, _ = top(samples)
    assert rows[0].startswith("    500  50.0%  50.0%      500  50.0% main.DFS"), rows[0]
    assert rows[1].startswith("    300  30.0%  80.0%      300  30.0% runtime.mapaccess1_fast64"), rows[1]
    assert rows[2].startswith("    200  20.0% 100.0%      500  50.0% main.FindLoops"), rows[2]
    # running% 是列表内累计：50.0 → 80.0 → 100.0（其后 flat=0 的行不再增长）
    assert [r.split()[2] for r in rows][:3] == ["50.0%", "80.0%", "100.0%"]
    assert rows[3].startswith("      0   0.0% 100.0%") and rows[3].endswith("main.main")

    # 6. main.FindLoops 的解读（博客同款）：flat 20% 但 cum 50% ——
    #    "自身运行 20%，其自身或其调用的函数在运行的比例 50%"
    frow = [r for r in rows if r.endswith("main.FindLoops")][0].split()
    assert frow[1] == "20.0%" and frow[4] == "50.0%"

    # 7. -cum：按累计列排序；根帧 flat=0 也能登顶（博客 gosched0/main.main 模式）
    rows, _ = top(samples, by_cum=True)
    assert rows[0].endswith("main.main") and rows[0].startswith("      0   0.0%   0.0%      600  60.0%")
    # 并列 cum=500 的 DFS 与 FindLoops 按名字典序：DFS 在前
    assert rows[1].endswith("main.DFS") and rows[2].endswith("main.FindLoops")
    assert rows[3].endswith("runtime.mapaccess1_fast64")

    # 8. nodefraction 去噪：占比不足阈值的节点被过滤（cum 占比 ≥ 阈值才保留）
    rows, _ = top(samples, by_cum=True, nodefraction=0.5)
    names = [r.split()[-1] for r in rows]
    assert names == ["main.main", "main.DFS", "main.FindLoops"], names
    rows, _ = top(samples, by_cum=True, nodefraction=0.51)
    assert [r.split()[-1] for r in rows] == ["main.main"]

    # 9. 限制行数 n（topN）
    rows, _ = top(samples, n=2)
    assert len(rows) == 2

    # 10. 无截断数据集：浅调用链 cum 应为 100%（与深递归形成对照）
    shallow = [["main.main", "main.work"]] * 40
    _, cum2, total2 = aggregate(shallow)
    assert total2 == 40 and cum2["main.main"] == 40 and cum2["main.work"] == 40

    print("pprof_top: 10 组断言全部通过")


if __name__ == "__main__":
    main()

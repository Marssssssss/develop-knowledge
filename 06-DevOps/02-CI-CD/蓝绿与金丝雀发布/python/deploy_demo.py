"""蓝绿与金丝雀发布 — 四种部署策略模拟 + 分析门控自动回滚.

统一 LCG 随机数(state = 1664525*state + 1013904223 mod 2^32),
三种语言输出可逐行比对:
  demo 1 Recreate vs RollingUpdate 停机对比
  demo 2 Blue-Green 切流 + 瞬时回滚 / 晋升
  demo 3 Canary 步进分权重 + 分析门控(坏版本中止 / 好版本晋升)
"""
from __future__ import annotations

LCG_A = 1664525
LCG_C = 1013904223


class LCG:
    """确定性伪随机数:与 C/Go 版逐位一致,便于跨语言比对输出。"""

    def __init__(self, seed: int):
        self.state = seed & 0xFFFFFFFF

    def rnd(self) -> float:
        self.state = (LCG_A * self.state + LCG_C) & 0xFFFFFFFF
        return self.state / 4294967296.0


def simulate_recreate(n: int, err_new: float, seed: int,
                      gap: float = 0.10) -> tuple[int, int]:
    """Recreate:先删旧再启新;切换窗口内的请求全部失败(必然停机)。"""
    rng = LCG(seed)
    errors, gap_n = 0, int(n * gap)
    for i in range(n):
        if i < gap_n:
            errors += 1                    # 停机窗口:没有版本在服务
        elif rng.rnd() < err_new:
            errors += 1                    # 新版本自身错误
    return errors, gap_n


def simulate_rolling(n: int, err_new: float, seed: int,
                     replicas: int = 5) -> tuple[int, list[str]]:
    """RollingUpdate:逐实例替换;每轮 r/replicas 的请求到新版本,无停机。"""
    rng = LCG(seed)
    errors, batch = 0, n // replicas
    mixes: list[str] = []
    for r in range(1, replicas + 1):
        for _ in range(batch):
            if rng.rnd() < (r / replicas) * err_new:
                errors += 1
        mixes.append(f"round {r}: {r}/{replicas} new pods")
    return errors, mixes


def simulate_bluegreen(n: int, err_new: float, seed: int,
                      threshold: float = 0.05,
                      analysis_after: int = 200) -> tuple[int, float, str]:
    """Blue-Green:切流瞬时;analysis_after 个请求后统计错误率决定回切/晋升。

    回滚瞬时且零额外错误的根因:旧 ReplicaSet 未缩容,切回只是改 selector。
    """
    rng = LCG(seed)
    errors = 0
    for _ in range(analysis_after):        # 切流后新版本全量服务
        if rng.rnd() < err_new:
            errors += 1
    rate = errors / analysis_after
    if rate > threshold:
        return errors, rate, "rolled back (switch activeService to old RS)"
    for _ in range(n - analysis_after):    # 晋升:剩余请求全走新版本
        if rng.rnd() < err_new:
            errors += 1
    return errors, rate, "promoted (old RS scaled down)"


def simulate_canary(n: int, err_new: float, seed: int,
                    steps: tuple[int, ...] = (10, 33, 100),
                    threshold: float = 0.05) -> tuple[int, str]:
    """Canary:按步分权重;每步统计新版本错误率,超阈值即中止回滚。"""
    rng = LCG(seed)
    errors, batch = 0, n // len(steps)
    for w in steps:
        new_total = new_err = 0
        for _ in range(batch):
            if rng.rnd() < w / 100:        # 路由:weight% 到新版本
                new_total += 1
                if rng.rnd() < err_new:    # 新版本自身错误率
                    new_err += 1
                    errors += 1
            # 旧版本(稳定)错误率视为 0
        if new_total and new_err / new_total > threshold:
            return errors, (f"aborted at {w}% "
                            f"(err={new_err}/{new_total} > {threshold})")
    return errors, "promoted to 100%"


def demo_recreate_vs_rolling() -> None:
    print("== demo 1: Recreate vs RollingUpdate (n=1000, 10% 切换窗口) ==")
    n, err_new, seed = 1000, 0.0, 42       # 两版本都健康,隔离"停机"效应
    errors, gap_n = simulate_recreate(n, err_new, seed)
    print(f"  Recreate: {errors} errors ({gap_n} 请求落在停机窗口)"
          f" — 两版本从不共存,但必然停机")
    errors, mixes = simulate_rolling(n, err_new, seed)
    print(f"  Rolling:  {errors} errors — 无停机,但版本共存:")
    for m in mixes:
        print(f"    {m}")


def demo_bluegreen() -> None:
    print("== demo 2: Blue-Green (n=1000, analysis after 200 req) ==")
    n = 1000
    errors, rate, verdict = simulate_bluegreen(n, 0.20, 7)
    print(f"  坏版本(err=20%): {errors} errors, 检测错误率 {rate:.2f}"
          f" -> {verdict}")
    print("    回滚零额外成本:旧 RS 未缩容,切回 selector 即恢复")
    errors, rate, verdict = simulate_bluegreen(n, 0.0, 7)
    print(f"  好版本(err=0%):  {errors} errors, 检测错误率 {rate:.2f}"
          f" -> {verdict}")
    print("    切换期间 2x 副本成本是蓝绿的代价")


def demo_canary() -> None:
    print("== demo 3: Canary steps=[10,33,100] (n=1000, threshold=5%) ==")
    n = 1000
    errors, verdict = simulate_canary(n, 0.20, 9)
    print(f"  坏版本(err=20%): {errors} errors -> {verdict}")
    print("    爆炸半径被限制在第 1 步的 10% 流量内")
    errors, verdict = simulate_canary(n, 0.0, 9)
    print(f"  好版本(err=0%):  {errors} errors -> {verdict}")
    print("    三步分析全过,新版本晋升为 stable")


if __name__ == "__main__":
    demo_recreate_vs_rolling()
    print()
    demo_bluegreen()
    print()
    demo_canary()

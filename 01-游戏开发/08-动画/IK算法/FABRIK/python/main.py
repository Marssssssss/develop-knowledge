"""
FABRIK (Forward And Backward Reaching Inverse Kinematics) - 2D 单链 / 单末端 / 无约束版本.

参考:
  Aristidou & Lasenby (2011), Graphical Models 73(5): 243-260.
  DOI: 10.1016/j.gmod.2011.05.003

算法两步迭代 (backward + forward) 在位置上求解, 不操作旋转:
  - backward: p[n] <- t, 从 i = n-1 递减到 0: p[i] = p[i+1] + normalize(p[i]-p[i+1]) * d[i]
  - forward : p[0] <- root, 从 i = 1 递增到 n: p[i] = p[i-1] + normalize(p[i]-p[i-1]) * d[i-1]
重复直到 |p[n]-t| < tol.

不可达目标 (target 距 root 距离 > chain 总长):
  按 Aristidou 论文 §3.7 描述, 沿 (root -> target) 方向把链条沿直线拉直, 末端停在最大可达处.

运行:
    python3 main.py

期望 (允许最后一位浮点误差):
    Case 1 reachable: 末端误差 ~ 1e-12, 3-6 次迭代
    Case 2 unreachable: 末端停在 (6, 0), 即沿 +x 拉直的极限
"""

from __future__ import annotations
import math
from typing import List, Tuple

Vec2 = Tuple[float, float]


def sub(a: Vec2, b: Vec2) -> Vec2: return (a[0] - b[0], a[1] - b[1])
def add(a: Vec2, b: Vec2) -> Vec2: return (a[0] + b[0], a[1] + b[1])
def scale(a: Vec2, s: float) -> Vec2: return (a[0] * s, a[1] * s)
def dot(a: Vec2, b: Vec2) -> float: return a[0] * b[0] + a[1] * b[1]
def length(a: Vec2) -> float: return math.hypot(a[0], a[1])
def norm(a: Vec2) -> Vec2:
    """单位化; 退化情形 (|a|≈0) 默认返回 (1, 0) 避免除零."""
    l = length(a)
    if l < 1e-12:
        return (1.0, 0.0)
    return (a[0] / l, a[1] / l)


def fabrik_backward(p: List[Vec2], d: List[float], target: Vec2) -> None:
    """反向: 末端钉到 target, 从 n-1 递减回拉每个关节到固定骨头长度 d[i]."""
    n = len(d)
    p[n] = target
    for i in range(n - 1, -1, -1):
        p[i] = add(p[i + 1], scale(norm(sub(p[i], p[i + 1])), d[i]))


def fabrik_forward(p: List[Vec2], d: List[float], root: Vec2) -> None:
    """正向: 根钉回 root, 从 1 递增前推每个关节到固定骨头长度 d[i-1]."""
    n = len(d)
    p[0] = root
    for i in range(1, n + 1):
        p[i] = add(p[i - 1], scale(norm(sub(p[i], p[i - 1])), d[i - 1]))


def fabrik_solve(p: List[Vec2], d: List[float],
                 target: Vec2, tol: float = 1e-9,
                 max_iter: int = 100) -> int:
    """完整 FABRIK 求解. 返回实际迭代次数 (不可达时返回 0)."""
    root = p[0]
    total = sum(d)
    if length(sub(target, root)) > total:
        # 不可达: 沿 (root -> target) 单位向量把链条沿直线拉直
        dir_ = norm(sub(target, root))
        p[0] = root
        for i in range(1, len(p)):
            p[i] = add(p[i - 1], scale(dir_, d[i - 1]))
        return 0
    it = 0
    for it in range(max_iter):
        if length(sub(p[-1], target)) < tol:
            return it
        fabrik_backward(p, d, target)
        fabrik_forward(p, d, root)
    return max_iter


def dump(p: List[Vec2], title: str) -> None:
    print(title)
    for i, (x, y) in enumerate(p):
        print(f"  p[{i}] = ({x:6.3f}, {y:6.3f})")


def main() -> None:
    # 3 段骨头, 总长 6, 初始沿 +x 直线
    d = [2.0, 2.0, 2.0]
    p0 = [(0.0, 0.0), (2.0, 0.0), (4.0, 0.0), (6.0, 0.0)]
    n_bones = len(d)

    # Case 1: 可达 (4, 3), 距离 5 < 6
    p1 = list(p0)
    t1 = (4.0, 3.0)
    it1 = fabrik_solve(p1, d, t1)
    dump(p1, "=== Case 1: reachable target ===")
    print(f"  target = {t1}, root = {p1[0]}, iterations = {it1}")
    print(f"  end-effector error = {length(sub(p1[-1], t1)):.3e}\n")

    # Case 2: 不可达 (10, 0), 距离 10 > 6, 应沿 +x 拉直
    p2 = list(p0)
    t2 = (10.0, 0.0)
    fabrik_solve(p2, d, t2)
    dump(p2, "=== Case 2: unreachable target (stretched along +x) ===")
    print(f"  target = {t2}, end-effector reached = {p2[-1]}\n")

    # Case 3: 只做一次 backward + forward, 显示一迭代的效果
    p3 = list(p0)
    t3 = (4.0, 3.0)
    fabrik_backward(p3, d, t3)
    dump(p3, "=== Case 3: 1 BACKWARD pass only ===")
    print(f"  end effector is at target {p3[-1]}")
    print(f"  but root drifted from (0,0) to {p3[0]} (forward will fix it)")
    fabrik_forward(p3, d, (0.0, 0.0))
    dump(p3, "=== Case 3: 1 BACKWARD + 1 FORWARD ===")
    print(f"  root back to (0,0), end-effector error = "
          f"{length(sub(p3[-1], t3)):.3e}")

    # 收敛速度演示 (用于 README 的"性能与边界"段)
    print("\n=== Convergence trace (case 1) ===")
    p4 = list(p0)
    for it in range(8):
        err_before = length(sub(p4[-1], t1))
        if err_before < 1e-12:
            print(f"  converged at iter {it}, err = {err_before:.3e}")
            break
        fabrik_backward(p4, d, t1)
        fabrik_forward(p4, d, (0.0, 0.0))
        err_after = length(sub(p4[-1], t1))
        print(f"  iter {it + 1}: err = {err_after:.3e} (before backward: {err_before:.3e})")


if __name__ == "__main__":
    main()

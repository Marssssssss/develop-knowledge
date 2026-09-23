"""glTF 动画采样器自检：全部断言实跑，期望值逐条手算过。"""

import math

from sampler import (
    Sampler, Animation, lerp, slerp, slerp_naive, cubic, smoothstep,
    quat_normalize, quat_dot, PATH_ACCESSOR,
)

COUNT = 0
FAIL = []


def ok(cond, msg):
    global COUNT
    COUNT += 1
    if not cond:
        FAIL.append(msg)
        print("FAIL:", msg)


def close(a, b, tol=1e-9):
    return abs(a - b) <= tol


def vclose(a, b, tol=1e-9):
    return isinstance(a, list) and len(a) == len(b) and all(close(x, y, tol) for x, y in zip(a, b))


def raises(fn, label):
    try:
        fn()
    except ValueError:
        return True
    except Exception as e:  # noqa: BLE001
        print("  (", label, "抛了非 ValueError:", type(e).__name__, ")")
        return False
    print("  (", label, "没有抛异常 )")
    return False


# ---------------------------------------------------------------- STEP
st = Sampler([0.0, 1.0, 2.0], [0.0, 10.0, 20.0], "STEP", "weights")
ok(st.sample(0.0) == 0.0, "STEP 命中关键帧 0 直出 v_0")
ok(st.sample(1.5) == 10.0, f"STEP 在 (1,2) 内应恒为 v_1=10, 实得 {st.sample(1.5)}")
ok(st.sample(0.999) == 0.0, "STEP 在 (0,1) 内应恒为 v_0=0")
ok(st.sample(5.0) == 20.0, "STEP 超出上界应 clamp 到最后一个值")
ok(st.sample(-3.0) == 0.0, "STEP 低于下界应 clamp 到第一个值")

# ---------------------------------------------------------------- LINEAR（非旋转）
ln = Sampler([0.0, 1.0, 2.0], [[0.0, 0.0, 0.0], [10.0, 20.0, -4.0], [20.0, 40.0, -8.0]],
             "LINEAR", "translation")
ok(vclose(ln.sample(0.5), [5.0, 10.0, -2.0]), f"LINEAR 中点应为一半, 实得 {ln.sample(0.5)}")
ok(vclose(ln.sample(1.25), [12.5, 25.0, -5.0]), f"LINEAR 1.25s 实得 {ln.sample(1.25)}")
ok(vclose(ln.sample(-1.0), [0.0, 0.0, 0.0]), "LINEAR 下界外 clamp")
ok(vclose(ln.sample(9.0), [20.0, 40.0, -8.0]), "LINEAR 上界外 clamp")
ok(ln.sample(1.0) == [10.0, 20.0, -4.0], "命中关键帧时不做插值（原值直出）")

# ---------------------------------------------------------------- SLERP
q0 = [0.0, 0.0, 0.0, 1.0]                                   # 单位旋转
q90 = [0.0, math.sin(math.pi / 4), 0.0, math.cos(math.pi / 4)]  # 绕 Y +90°
rot = Sampler([0.0, 1.0], [q0, q90], "LINEAR", "rotation")
mid = rot.sample(0.5)
expect45 = [0.0, math.sin(math.pi / 8), 0.0, math.cos(math.pi / 8)]
ok(vclose(mid, expect45), f"SLERP 中点应为绕 Y +45°, 实得 {mid}")
ok(close(math.sqrt(sum(c * c for c in mid)), 1.0), "SLERP 结果应为单位四元数")
ok(close(2 * math.acos(min(1.0, mid[3])), math.pi / 4, 1e-9), "SLERP 中点对应 45° 旋转")

# 角度单调推进：t=0.25 -> 22.5°
q25 = rot.sample(0.25)
ok(close(2 * math.acos(min(1.0, q25[3])), math.pi / 8, 1e-9),
   f"t=0.25 应给 22.5°, 实得 {2 * math.acos(min(1.0, q25[3]))}")
ok(close(q25[1] / math.sin(math.pi / 16), 1.0, 1e-9) or close(q25[1], math.sin(math.pi / 16)),
   f"t=0.25 的 y 分量应为 sin(11.25°), 实得 {q25[1]}")

# 最短路：把 q_{k+1} 取反（同一个姿态的另一表示），规范公式结果必须不变
q90n = [-c for c in q90]
rot_n = Sampler([0.0, 1.0], [q0, q90n], "LINEAR", "rotation")
mid_n = rot_n.sample(0.5)
ok(vclose(mid_n, expect45), f"最短路修正后取反的四元数应给出同一结果, 实得 {mid_n}")
naive = slerp_naive(q0, q90n, 0.5)
ok(not vclose(naive, expect45, 1e-6),
   f"不做最短路修正会走长弧, 实得 {naive}")
ok(close(2 * math.acos(min(1.0, abs(naive[3]))), 3 * math.pi / 4, 1e-6),
   f"长弧中点对应 135° 而非 45°, 实得 {2 * math.acos(min(1.0, abs(naive[3])))}")
ok(close(math.sqrt(sum(c * c for c in naive)), 1.0, 1e-9), "长弧结果仍是单位四元数（只是方向相反）")

# dot 为正时不触发符号翻转
ok(vclose(slerp(q0, q90, 0.5), slerp_naive(q0, q90, 0.5)),
   "dot > 0 时最短路修正不生效，两种实现应一致")

# a → 0 时退化为线性插值
qnear = [0.0, 1e-9, 0.0, 1.0]
r = slerp(q0, quat_normalize(qnear), 0.5)
ok(close(math.sqrt(sum(c * c for c in r)), 1.0, 1e-12), "夹角趋零时仍返回单位四元数")

# ---------------------------------------------------------------- CUBICSPLINE
# 段 [0, 2]，值 0 -> 10，切线全零
cs = Sampler([0.0, 2.0], [(0.0, 0.0, 0.0), (0.0, 10.0, 0.0)],
             "CUBICSPLINE", "weights")
ok(close(cs.sample(0.0), 0.0), "CUBICSPLINE 端点应精确等于 v_k")
ok(close(cs.sample(2.0), 10.0), "CUBICSPLINE 端点应精确等于 v_{k+1}")
ok(close(cs.sample(1.0), 5.0), "切线全零时中点恰好也是 5（smoothstep(0.5)=0.5）")
val25 = cs.sample(0.5)  # 段内 t = 0.25
ok(close(val25, 10.0 * smoothstep(0.25), 1e-12),
   f"切线全零时 t=0.25 应为 10·smoothstep(0.25)={10.0 * smoothstep(0.25)}, 实得 {val25}")
ok(not close(val25, 2.5, 1e-6), "CUBICSPLINE（零切线）不等于 LINEAR 的 2.5")

# 切线取「割线速度」时精确退化为 LINEAR
sec = (10.0 - 0.0) / 2.0  # = 5 每秒
cs_lin = Sampler([0.0, 2.0], [(0.0, 0.0, sec), (sec, 10.0, 0.0)],
                 "CUBICSPLINE", "weights")
for tc, exp in ((0.26, 1.3), (1.0, 5.0), (1.77, 8.85)):
    got = cs_lin.sample(tc)
    ok(close(got, exp, 1e-9), f"切线=割线速度时 t_c={tc} 应给 {exp}, 实得 {got}")

# t_d 参与缩放：把段长减半而切线不变 → 切线贡献减半
cs_half = Sampler([0.0, 1.0], [(0.0, 0.0, sec), (sec, 10.0, 0.0)],
                  "CUBICSPLINE", "weights")
# 注意别挑 t=0.5 这种对称点：h10 与 h11 在该点恰好互为相反数，切线贡献会抵消
ok(not close(cs_half.sample(0.25), cs_lin.sample(0.5), 1e-6),
   f"同样切线在 t_d 减半后结果不同（切线单位是每秒）: {cs_half.sample(0.25)} vs {cs_lin.sample(0.5)}")
hand = (1.0 * (0.25 ** 3 - 2 * 0.25 ** 2 + 0.25) * sec
        + (-2 * 0.25 ** 3 + 3 * 0.25 ** 2) * 10.0
        + 1.0 * (0.25 ** 3 - 0.25 ** 2) * sec)
ok(close(cs_half.sample(0.25), hand, 1e-12),
   f"t_d 缩放后结果应等于手算值 {hand}, 实得 {cs_half.sample(0.25)}")

# 端点切线未被使用：a_1 与 b_n 改任意值不影响输出
cs_a1 = Sampler([0.0, 2.0], [(999.0, 0.0, 0.0), (0.0, 10.0, -777.0)],
                "CUBICSPLINE", "weights")
ok(close(cs_a1.sample(1.0), cs.sample(1.0), 1e-12),
   "首帧 in-tangent 与末帧 out-tangent 不参与计算（规范建议置零）")

# C1 连续性：段内端点处对真实时间的导数 = 切线 b_k
# 切线 b_k 的单位是「每秒」，故对真实时间的导数 = Δ值 / Δ时间（不再除 t_d）
eps = 1e-6
d0 = (cs_lin.sample(eps) - cs_lin.sample(0.0)) / eps
ok(close(d0, sec, 1e-3), f"段起点导数应等于 out-tangent {sec}, 实得 {d0}")
d1 = (cs_lin.sample(2.0) - cs_lin.sample(2.0 - eps)) / eps
ok(close(d1, sec, 1e-3), f"段终点导数应等于下一帧 in-tangent {sec}, 实得 {d1}")

# rotation 用 CUBICSPLINE 必须归一化
cr = Sampler([0.0, 1.0],
             [([0.0, 0.0, 0.0, 0.0], [0.0, 0.0, 0.0, 1.0], [0.3, 0.0, 0.0, 0.0]),
              ([0.0, 0.0, 0.0, 0.0], [0.0, math.sin(math.pi / 4), 0.0, math.cos(math.pi / 4)],
               [0.0, 0.0, 0.0, 0.0])],
             "CUBICSPLINE", "rotation")
r = cr.sample(0.5)
ok(close(math.sqrt(sum(c * c for c in r)), 1.0, 1e-12),
   f"rotation 的 CUBICSPLINE 结果 MUST 归一化, 实得模长 {math.sqrt(sum(c * c for c in r))}")

# 只有 1 个关键帧的 CUBICSPLINE 非法
ok(raises(lambda: Sampler([0.0], [([0.0], [0.0], [0.0])], "CUBICSPLINE", "weights"),
          "CUBICSPLINE 单帧"), "CUBICSPLINE 采样器 MUST 至少 2 个关键帧")

# ---------------------------------------------------------------- 访问器类型校验
ok(PATH_ACCESSOR["rotation"][0] == "VEC4", "rotation 输出访问器是 VEC4")
ok(PATH_ACCESSOR["weights"][0] == "SCALAR", "weights 输出访问器是 SCALAR")
ok(raises(lambda: Sampler([0.0, 1.0], [[0, 0, 0, 0], [1, 1, 1, 1]], "LINEAR", "translation"),
          "translation 用 VEC4"), "translation 必须 VEC3")
ok(raises(lambda: Sampler([0.0, 1.0], [0.0, 1.0], "LINEAR", "scale"), "scale 用 SCALAR"),
   "scale 必须 VEC3")
ok(raises(lambda: Sampler([0.0, 1.0], [[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]], "LINEAR",
                          "translation", component_type="unorm8"), "translation 用 unorm8"),
   "translation 只允许 float32")
ok(raises(lambda: Sampler([0.0, 1.0], [0.0, 1.0], "LINEAR", "weights",
                          has_minmax=False), "input 缺 min/max"),
   "sampler 的 input 访问器 MUST 定义 min/max")
ok(raises(lambda: Sampler([0.0, 1.0], [0.0, 1.0], "SMOOTH", "weights"), "非法插值"),
   "interpolation 只能取 LINEAR/STEP/CUBICSPLINE")
ok(raises(lambda: Sampler([0.0, 1.0], [0.0], "LINEAR", "weights"), "input/output 数量不等"),
   "input 与 output 元素数必须一致")
ok(raises(lambda: Sampler([1.0, 0.0], [[0.0], [1.0]], "LINEAR", "weights"), "时间倒序"),
   "input 时间必须单调不减")

# ---------------------------------------------------------------- 通道校验
s_tr = Sampler([0.0, 1.0], [[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]], "LINEAR", "translation")
s_ro = Sampler([0.0, 1.0], [q0, q90], "LINEAR", "rotation")
s_w = Sampler([0.0, 1.0], [0.0, 1.0], "LINEAR", "weights")

ok(Animation([{"node": 1, "path": "translation", "sampler": 0},
              {"node": 1, "path": "rotation", "sampler": 1}],
             [s_tr, s_ro]).validate() is None, "不同 path 的通道可以共存")
ok(raises(lambda: Animation([{"node": 1, "path": "rotation", "sampler": 0},
                             {"node": 1, "path": "rotation", "sampler": 1}],
                            [s_ro, s_ro]).validate(), "重复 (node,path)"),
   "同一 (node, path) MUST NOT 被使用多次")
ok(raises(lambda: Animation([{"node": 2, "path": "translation", "sampler": 0}],
                            [s_tr], nodes_with_matrix=[2]).validate(), "matrix 节点动画 TRS"),
   "节点定义 matrix 时 MUST NOT 动画 TRS")
ok(Animation([{"node": 2, "path": "weights", "sampler": 0}], [s_w],
             nodes_with_matrix=[2]).validate() is None,
   "matrix 不影响 weights 通道（规范明确说明）")
ok(raises(lambda: Animation([{"node": 3, "path": "weights", "sampler": 0, "has_morph": False}],
                            [s_w]).validate(), "无 morph 的节点被 weights 指向"),
   "没有 morph target 的节点 MUST NOT 被 weights 通道指向")
ok(Animation([{"path": "rotation", "sampler": 0}], [s_ro]).validate() is None,
   "未定义 node 的通道被忽略（不报错）")
ok(raises(lambda: Animation([{"node": 1, "path": "rotation", "sampler": 5}], [s_ro]).validate(),
          "sampler 越界"), "channel.sampler 必须指向本动画内的采样器")
ok(raises(lambda: Animation([{"node": 1, "path": "scale", "sampler": 0}], [s_ro]).validate(),
          "采样器类型与 path 不符"), "采样器输出类型必须与通道 path 匹配")

print(f"\n断言总数: {COUNT}, 失败: {len(FAIL)}")
if FAIL:
    for m in FAIL:
        print("  -", m)
    raise SystemExit(1)
print("全部通过")

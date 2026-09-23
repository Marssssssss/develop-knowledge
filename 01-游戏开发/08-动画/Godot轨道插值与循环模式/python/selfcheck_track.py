"""Godot 轨道求值自检：期望值逐条手算过。"""

import math

from track import (
    Track, Key, find, pingpong_index, pingpong, posmod, is_equal_approx,
    track_len, CMP_EPSILON,
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


# ---------------------------------------------------------------- _find
ks = [Key(0.0, 0.0), Key(0.5, 10.0), Key(1.0, 20.0)]
ok(find(ks, 0.5) == 1, "命中关键帧返回其下标")
ok(find(ks, 0.75) == 1, "落在 (0.5,1.0) 内返回 1（前一个关键帧）")
ok(find(ks, 1.5) == 2, "超出末帧返回末帧下标")
ok(find(ks, -1.0) == -1, "早于首帧返回 -1")
ok(find(ks, 0.500001) == 1, "APPROX 容差内（差 1e-6 < CMP_EPSILON）视为命中")
ok(find(ks, 0.51) == 1, "差 0.01 > CMP_EPSILON 时不命中，仍返回 1")
ok(find(ks, 0.6, backward=True) == 2, "backward 时返回后一个关键帧")
ok(find(ks, 0.5, backward=True) == 1, "backward 命中时仍返回该帧")
ok(find([], 0.0) == -2, "空轨道返回 -2")
ok(find(ks, 0.75, limit=True, length=1.0) == 1, "limit=True 且落在 [0,length] 内不拦")
# 注意：命中关键帧时 _find 会提前 return，limit 检查根本不会执行
ok(find([Key(-0.5, 0.0), Key(1.0, 1.0)], -0.5, limit=True, length=1.0) == 0,
   "精确命中时提前返回，limit 检查被跳过")
ok(find([Key(-0.5, 0.0), Key(1.0, 1.0)], -0.4, limit=True, length=1.0) == -1,
   "未命中且落到负时间关键帧上 → limit 判越界")
ok(find([Key(0.0, 0.0), Key(2.0, 1.0)], 2.5, limit=True, length=1.0) == -1,
   "未命中且落到超过 length 的关键帧上 → limit 判越界")

# pingpong 下标折成三角波
ok([pingpong_index(i, 5) for i in range(10)] == [0, 1, 2, 3, 4, 4, 3, 2, 1, 0],
   f"pingpong 下标映射应为三角波, 实得 {[pingpong_index(i, 5) for i in range(10)]}")
ok(pingpong_index(0, 3) == 0 and pingpong_index(3, 3) == 2 and pingpong_index(4, 3) == 1,
   "len=3 时 0->0, 3->2, 4->1")
ok(close(pingpong(0.0, 1.0), 0.0) and close(pingpong(0.5, 1.0), 0.5)
   and close(pingpong(1.0, 1.0), 1.0), "pingpong 在 [0,len] 上等于自身")
ok(close(pingpong(1.5, 1.0), 0.5), "pingpong(1.5, 1) = 0.5（折返）")

# ---------------------------------------------------------------- 关键帧查找模式
tr = Track(ks, length=1.0)
ok(tr.find_key(0.5, "EXACT") == 1, "EXACT 命中")
ok(tr.find_key(0.6, "EXACT") == -1, "EXACT 未命中返回 -1")
ok(tr.find_key(0.6, "NEAREST") == 1, "NEAREST 不做命中判定，返回 _find 结果")
ok(tr.find_key(0.5000001, "EXACT") == -1, "EXACT 用 == 判定，容差内也不算命中")
ok(tr.find_key(0.5000001, "APPROX") == 1, "APPROX 用 is_equal_approx，容差内算命中")
ok(tr.find_key(0.51, "APPROX") == -1, "APPROX 超出容差返回 -1")
ok(tr.find_key(5.0, "NEAREST") == 2, "NEAREST 超出末帧返回末帧下标（不 clamp 到 -1）")

# ---------------------------------------------------------------- 超过 length 的关键帧被截断
ks_long = [Key(0.0, 0.0), Key(0.5, 10.0), Key(1.0, 20.0), Key(2.0, 999.0)]
ok(track_len(ks_long, 1.5) == 3, f"len = _find(keys, length)+1 = 3, 实得 {track_len(ks_long, 1.5)}")
t_long = Track(ks_long, length=1.5)
ok(close(t_long.interpolate(1.8), 20.0),
   f"时间 1.8 超过 length=1.5，但 2.0 处的关键帧已被丢弃 → 取到 20, 实得 {t_long.interpolate(1.8)}")
ok(track_len([Key(0.0, 0.0)], 1.0) == 1, "单关键帧轨道 len=1")
ok(close(Track([Key(0.0, 7.0), Key(2.0, 9.0)], length=1.0).interpolate(0.4), 7.0),
   "len==1（末帧超出 length 被截断）时直接返回第一个关键帧的值")

# ---------------------------------------------------------------- LOOP_NONE vs LOOP_LINEAR
k2 = [Key(0.0, 0.0), Key(0.5, 10.0)]
none = Track(k2, length=1.0, loop_mode=Track.LOOP_NONE)
lin = Track(k2, length=1.0, loop_mode=Track.LOOP_LINEAR)
pp = Track(k2, length=1.0, loop_mode=Track.LOOP_PINGPONG)

ok(close(none.interpolate(0.25), 5.0), "LOOP_NONE 段内线性：0.25 → 5")
ok(close(lin.interpolate(0.25), 5.0), "LOOP_LINEAR 段内线性一致：0.25 → 5")
ok(close(none.interpolate(0.75), 10.0),
   f"LOOP_NONE 末段外（idx=maxi，next 被 clamp 回自身）→ c=0 → 保持 10, 实得 {none.interpolate(0.75)}")
ok(close(lin.interpolate(0.75), 5.0),
   f"LOOP_LINEAR 末段外绕回首帧：delta=(1-0.5)+0=0.5, from=0.25 → c=0.5 → 5, 实得 {lin.interpolate(0.75)}")
ok(close(none.interpolate(-0.1), 0.0),
   f"LOOP_NONE 首帧前：is_start_edge 使 delta 保持 0 → c=0 → 0, 实得 {none.interpolate(-0.1)}")
ok(close(lin.interpolate(-0.1), 2.0),
   f"LOOP_LINEAR 首帧前绕回：idx=maxi=1, next=0, delta=0.5, from=0.4 → c=0.8 → lerp(10,0,0.8)=2, 实得 {lin.interpolate(-0.1)}")
ok(close(lin.interpolate(1.0), 0.0), "LOOP_LINEAR 在 t=length 处回到首帧值")
ok(close(none.interpolate(1.0), 10.0),
   "LOOP_NONE 在 t=length 处：idx=maxi 且 is_end_edge → delta 保持 0 → c=0 → 保持末帧值")

# pingpong 只断言在关键帧取值域内（折返分支的时间记账按源码直译）
for t in (0.0, 0.25, 0.5, 0.75, 1.0):
    v = pp.interpolate(t)
    ok(0.0 - 1e-9 <= v <= 10.0 + 1e-9, f"PINGPONG t={t} 的结果应落在 [0,10], 实得 {v}")
ok(close(pp.interpolate(0.5), 10.0), "PINGPONG 在末帧处取到 10")
# 关键帧覆盖 [0, length] 的常规配置：pingpong 才呈现折返（两点配置会退化成平顶）
k3 = [Key(0.0, 0.0), Key(0.5, 10.0), Key(1.0, 20.0)]
pp3 = Track(k3, length=1.0, loop_mode=Track.LOOP_PINGPONG)
ok(close(pp3.interpolate(0.75), 15.0), f"PINGPONG t=0.75 应在 10 与 20 之间取 15, 实得 {pp3.interpolate(0.75)}")
ok(close(pp3.interpolate(0.9), 18.0), f"PINGPONG t=0.9 → c=0.8 → 18, 实得 {pp3.interpolate(0.9)}")
ok(close(pp3.interpolate(0.1), 2.0), f"PINGPONG t=0.1 → 2, 实得 {pp3.interpolate(0.1)}")

# ---------------------------------------------------------------- 插值方式
k3 = [Key(0.0, 0.0), Key(1.0, 100.0)]
ok(close(Track(k3, length=1.0, interpolation=Track.NEAREST).interpolate(0.9), 0.0),
   "NEAREST 取 idx 上的值（0.9 处仍是 0）")
ok(close(Track(k3, length=1.0, interpolation=Track.LINEAR).interpolate(0.9), 90.0),
   "LINEAR 取 90")
ok(close(Track(k3, length=1.0, interpolation=Track.LINEAR,
               update_discrete=True).interpolate(0.9), 0.0),
   "UPDATE_DISCRETE 的轨道按 NEAREST 取值（源码：update_mode == UPDATE_DISCRETE ? NEAREST : interp）")

# 角度插值走最短路并折回 [0, 2π)
ang = [Key(0.0, 0.1), Key(1.0, math.tau - 0.1)]
ta = Track(ang, length=1.0, interpolation=Track.LINEAR_ANGLE)
r = ta.interpolate(0.5)
ok(close(r, 0.0, 1e-9), f"LINEAR_ANGLE 跨 2π 走最短路：0.1 与 2π-0.1 的中点是 0, 实得 {r}")
ok(0.0 <= r < math.tau, "结果经 fposmod 折回 [0, 2π)")
ok(close(ta.interpolate(0.25), 0.05, 1e-9), f"四分之一处应为 0.05, 实得 {ta.interpolate(0.25)}")
naive = (1 - 0.5) * 0.1 + 0.5 * (math.tau - 0.1)
ok(not close(naive, 0.0, 1e-6), f"朴素线性会得到 {naive}（绕远路），角度插值必须避免")

# ---------------------------------------------------------------- transition
kz = [Key(0.0, 0.0, transition=0.0), Key(1.0, 100.0, transition=0.0)]
ok(close(Track(kz, length=1.0).interpolate(0.5), 0.0),
   "transition == 0 时不做插值，直接返回 idx 上的值")
ok(close(Track(kz, length=1.0).interpolate(0.99), 0.0),
   "transition == 0 时 idx 落在首帧（0.99 的 _find 结果是 0）→ 返回 0")
ok(close(Track(kz, length=1.0).interpolate(1.0), 100.0),
   "transition == 0 且精确命中末帧 → 返回 100")
ok(close(Track([Key(0.0, 0.0, transition=1.0), Key(1.0, 100.0, transition=1.0)],
               length=1.0).interpolate(0.5), 50.0), "transition == 1 为普通线性")

# ---------------------------------------------------------------- 边界：空轨道 / 单帧
ok(Track([], length=1.0).interpolate(0.5) is None, "空轨道返回 None（len <= 0）")
ok(close(Track([Key(0.0, 42.0)], length=1.0).interpolate(0.5), 42.0), "单关键帧返回该值")

print(f"\n断言总数: {COUNT}, 失败: {len(FAIL)}")
if FAIL:
    for m in FAIL:
        print("  -", m)
    raise SystemExit(1)
print("全部通过")

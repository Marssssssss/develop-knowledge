"""param.py 自检：五类自动化事件、边界与取消语义。实跑版。"""

import math

from param import (EXP_RAMP, LINEAR_RAMP, SET_TARGET, SET_VALUE,
                   SET_VALUE_CURVE, AudioParam, NotSupportedError, RangeError,
                   adsr)

TOL = 1e-9
_ok = 0
_bad = []


def near(name, got, want, tol=1e-9):
    global _ok
    if abs(got - want) <= tol:
        _ok += 1
    else:
        _bad.append("%s: got %r want %r" % (name, got, want))


def check(name, got, want):
    global _ok
    if got == want:
        _ok += 1
    else:
        _bad.append("%s: got %r want %r" % (name, got, want))


def raises(exc, fn, *args):
    try:
        fn(*args)
    except exc:
        return True
    except Exception:
        return False
    return False


# ------------------------------------------------------------ setValueAtTime
p = AudioParam(0.0)
p.set_value_at_time(0.5, 1.0)
near("事件前取本征值", p.value_at(0.5), 0.0)
near("事件时刻起生效", p.value_at(1.0), 0.5)
near("事件后保持", p.value_at(9.0), 0.5)
check("事件类型", p.events[0].kind, SET_VALUE)

# 同一时刻后插的排在后面（后写覆盖先写）
p2 = AudioParam(0.0)
p2.set_value_at_time(1.0, 2.0)
p2.set_value_at_time(2.0, 2.0)
near("同刻后插者胜", p2.value_at(2.0), 2.0)
check("同刻两事件都保留", len(p2.events), 2)

# ------------------------------------------------------------ 线性斜坡
p3 = AudioParam(0.0)
p3.set_value_at_time(0.0, 0.0)
p3.linear_ramp_to_value_at_time(4.0, 1.0)
p3.linear_ramp_to_value_at_time(0.0, 2.0)
near("线性 0->4 中点", p3.value_at(0.5), 2.0)
near("线性 折点", p3.value_at(1.0), 4.0)
near("线性 4->0 中点", p3.value_at(1.5), 2.0)
near("线性 终点后保持", p3.value_at(3.0), 0.0)
check("事件类型 LinearRamp", p3.events[1].kind, LINEAR_RAMP)
# 无前序事件：等价于在当前时刻插入 setValueAtTime(当前值, currentTime)
p3b = AudioParam(3.0)
p3b.current_time = 1.0
p3b.linear_ramp_to_value_at_time(5.0, 2.0)
near("无前序斜坡从 currentTime 起算", p3b.value_at(1.5), 4.0)

# ------------------------------------------------------------ 指数斜坡
p4 = AudioParam(0.0)
p4.set_value_at_time(1.0, 0.0)
p4.exponential_ramp_to_value_at_time(4.0, 1.0)
near("指数中点为几何平均", p4.value_at(0.5), 2.0)
near("指数终点", p4.value_at(1.0), 4.0)
near("指数终点后保持", p4.value_at(5.0), 4.0)
check("指数不能到 0", raises(RangeError, p4.exponential_ramp_to_value_at_time, 0.0, 2.0), True)
# V0 = 0 -> 整段恒等于 V0
p5 = AudioParam(0.0)
p5.set_value_at_time(0.0, 0.0)
p5.exponential_ramp_to_value_at_time(4.0, 1.0)
near("V0=0 时恒等于 V0", p5.value_at(0.5), 0.0)
# 异号 -> 整段恒等于 V0
p6 = AudioParam(0.0)
p6.set_value_at_time(1.0, 0.0)
p6.exponential_ramp_to_value_at_time(-4.0, 1.0)
near("异号时恒等于 V0", p6.value_at(0.5), 1.0)

# ------------------------------------------------------------ setTargetAtTime
p7 = AudioParam(0.0)
p7.set_value_at_time(0.0, 0.0)
p7.set_target_at_time(1.0, 0.0, 0.1)
near("setTarget 一个时间常数处", p7.value_at(0.1), 1.0 - math.exp(-1.0))
near("setTarget 两个时间常数处", p7.value_at(0.2), 1.0 - math.exp(-2.0))
near("setTarget 起点", p7.value_at(0.0), 0.0)
check("事件类型 SetTarget", p7.events[1].kind, SET_TARGET)
# SetTarget 在下一个事件处结束
p8 = AudioParam(0.0)
p8.set_value_at_time(0.0, 0.0)
p8.set_target_at_time(1.0, 0.0, 0.1)
p8.set_value_at_time(0.5, 0.2)
near("SetTarget 在下一事件前", p8.value_at(0.15), 1.0 - math.exp(-1.5))
near("SetTarget 被下一事件截断", p8.value_at(0.2), 0.5)
near("截断后保持", p8.value_at(0.9), 0.5)

# ------------------------------------------------------------ setValueCurveAtTime
p9 = AudioParam(0.0)
p9.set_value_curve_at_time([0.0, 1.0, 2.0, 3.0], 0.0, 1.0)
near("曲线起点", p9.value_at(0.0), 0.0)
near("曲线 k=1 半程", p9.value_at(0.5), 1.5)      # floor(3*0.5)=1 -> 1..2 的中点
near("曲线 k=0 内插", p9.value_at(1.0 / 6.0), 0.5)
near("曲线末端取 V[N-1]", p9.value_at(1.0), 3.0)
near("曲线结束后保持末值", p9.value_at(2.0), 3.0)
check("曲线事件类型", p9.events[0].kind, SET_VALUE_CURVE)
# 曲线区间内已有事件 -> NotSupportedError
p10 = AudioParam(0.0)
p10.set_value_at_time(1.0, 0.5)
check("曲线内已有事件", raises(NotSupportedError, p10.set_value_curve_at_time,
                               [0.0, 1.0], 0.0, 1.0), True)
# 曲线区间内再排事件 -> NotSupportedError
p11 = AudioParam(0.0)
p11.set_value_curve_at_time([0.0, 1.0], 0.0, 1.0)
check("曲线内再排事件", raises(NotSupportedError, p11.set_value_at_time, 1.0, 0.5), True)
check("曲线端点同刻允许", p11.set_value_at_time(2.0, 1.0) is not None, True)

# ------------------------------------------------------------ 时间参数校验
p12 = AudioParam(0.0)
check("负 startTime -> RangeError", raises(RangeError, p12.set_value_at_time, 1.0, -1.0), True)
check("负 endTime -> RangeError",
      raises(RangeError, p12.linear_ramp_to_value_at_time, 1.0, -2.0), True)
p12.current_time = 2.0
p12.set_value_at_time(7.0, 1.0)
near("小于 currentTime 被钳到 currentTime", p12.events[0].time, 2.0)

# ------------------------------------------------------------ 取消
p13 = AudioParam(0.0)
p13.set_value_at_time(0.0, 0.0)
p13.linear_ramp_to_value_at_time(4.0, 1.0)
p13.linear_ramp_to_value_at_time(0.0, 2.0)
p13.cancel_scheduled_values(1.0)
check("cancelScheduledValues 移除 >=1s 的事件", len(p13.events), 1)
near("取消后回落到前一个事件值", p13.value_at(1.5), 0.0)
near("取消前该点本应是 2.0（对照）", 4.0 * (1.0 - (1.5 - 1.0)), 2.0)

p14 = AudioParam(0.0)
p14.set_value_at_time(0.0, 0.0)
p14.linear_ramp_to_value_at_time(4.0, 1.0)
p14.cancel_and_hold_at_time(0.5)
near("cancelAndHold 保持取消时刻的值", p14.value_at(0.5), 2.0)
near("cancelAndHold 之后恒定", p14.value_at(3.0), 2.0)
check("cancelAndHold 负时间 -> RangeError",
      raises(RangeError, p14.cancel_and_hold_at_time, -1.0), True)

# ------------------------------------------------------------ a-rate / k-rate
pr = AudioParam(0.0)
pr.set_value_at_time(0.0, 0.0)
pr.linear_ramp_to_value_at_time(1.0, 1.0)
block = pr.render_block(0.0, 4, 4.0)      # 0, 0.25, 0.5, 0.75 秒
near("a-rate 逐样本", block[1], 0.25)
near("a-rate 末样本", block[3], 0.75)
pk = AudioParam(0.0)
pk.automation_rate = "k-rate"
pk.set_value_at_time(0.0, 0.0)
pk.linear_ramp_to_value_at_time(1.0, 1.0)
kb = pk.render_block(0.0, 4, 4.0)
check("k-rate 整块取块首值", kb, [0.0, 0.0, 0.0, 0.0])

# ------------------------------------------------------------ ADSR 组合
env = AudioParam(0.0)
adsr(env, peak=1.0, attack=0.1, decay=0.2, sustain_level=0.5, release=0.05,
     start_time=0.0, note_duration=1.0)
near("ADSR 起音峰值", env.value_at(0.1), 1.0)
near("ADSR 延音电平", env.value_at(0.3), 0.5)
near("ADSR 音符结束前仍是延音", env.value_at(1.0), 0.5)
near("ADSR 释音一个时间常数后", env.value_at(1.05), 0.5 * math.exp(-1.0))
check("释音用 setTarget 而非指数斜坡", env.events[-1].kind, SET_TARGET)

print("assertions ok: %d, failed: %d" % (_ok, len(_bad)))
for line in _bad:
    print("FAILED", line)
if _bad:
    raise SystemExit(1)
print("ALL GREEN")

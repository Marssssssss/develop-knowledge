"""音频引擎的参数自动化：W3C Web Audio API `AudioParam` 事件表语义。

实读来源：W3C《Web Audio API 1.1》§1.6.2 "AudioParam Automation"（https://www.w3.org/TR/webaudio-1.1/）
  - 五类事件：SetValue / LinearRampToValue / ExponentialRampToValue / SetTarget / SetValueCurve
  - 自动化事件时间**不按采样率量化**，直接用给定的数值时刻套公式
  - 同一时刻已有事件时，新事件排在其后、排在更晚的事件之前
  - 线性斜坡 v(t) = V0 + (V1-V0)*(t-T0)/(T1-T0)
  - 指数斜坡 v(t) = V0*(V1/V0)^((t-T0)/(T1-T0))；V0 与 V1 异号或 V0=0 时 v(t)=V0；
    value=0 抛 RangeError（指数斜坡到 0 不可能，规范建议改用 setTargetAtTime）
  - setTargetAtTime: v(t) = V1 + (V0-V1)*e^(-(t-T0)/tau)；SetTarget 在下一个事件处结束
  - setValueCurveAtTime: k = floor((N-1)/TD*(t-T0))，在 V[k] 与 V[k+1] 间线性插值；
    结束后隐式 setValueAtTime(V[N-1], T0+TD)
  - 曲线区间 (T0, T0+TD) 内已有事件、或在该区间内再排事件 -> NotSupportedError
  - cancelScheduledValues 取消 >= cancelTime 的事件；cancelAndHoldAtTime 取消 > cancelTime 的
    事件并把曲线截断到 cancelTime（且必须与未截断时输出一致）
  - 时间为负抛 RangeError；小于 currentTime 时钳到 currentTime
"""

import math

TOL = 1e-12


class RangeError(ValueError):
    pass


class NotSupportedError(ValueError):
    pass


SET_VALUE = "SetValue"
LINEAR_RAMP = "LinearRampToValue"
EXP_RAMP = "ExponentialRampToValue"
SET_TARGET = "SetTarget"
SET_VALUE_CURVE = "SetValueCurve"


class Event(object):
    def __init__(self, kind, time, value=None, target=None, tau=None,
                 values=None, duration=None):
        self.kind = kind
        self.time = time
        self.value = value          # SetValue / 斜坡终点 V1
        self.target = target        # SetTarget 的 V1
        self.tau = tau              # SetTarget 时间常数
        self.values = values        # SetValueCurve 的值数组
        self.duration = duration    # SetValueCurve 时长

    def end_time(self):
        if self.kind == SET_VALUE_CURVE:
            return self.time + self.duration
        return self.time


class AudioParam(object):
    def __init__(self, value=0.0, automation_rate="a-rate"):
        self.default_value = value
        self.value = value              # 未被自动化覆盖时的本征值
        self.automation_rate = automation_rate
        self.events = []
        self.current_time = 0.0

    # ------------------------------------------------ 时间参数处理
    def _time(self, t, name):
        if t < 0:
            raise RangeError("%s must not be negative" % name)
        return max(t, self.current_time)   # 小于 currentTime 时钳到 currentTime

    # ------------------------------------------------ 事件插入
    def _insert(self, event):
        # 事件表按时间升序；同一时刻的新事件排在已有事件之后
        idx = 0
        for i, e in enumerate(self.events):
            if e.time <= event.time:
                idx = i + 1
        self.events.insert(idx, event)
        return event

    def _curve_guard(self, start_time, duration=None):
        """曲线区间 (T0, T0+TD) 内已有事件 -> NotSupportedError。"""
        end = start_time + duration if duration is not None else start_time
        for e in self.events:
            if start_time < e.time < end:
                raise NotSupportedError("event inside [%s, %s)" % (start_time, end))
            if e.kind == SET_VALUE_CURVE:
                e_end = e.time + e.duration
                if e.time < start_time < e_end or (duration is not None
                                                   and e.time < end < e_end):
                    raise NotSupportedError("overlaps an existing curve")

    # ------------------------------------------------ 自动化方法
    def set_value_at_time(self, value, start_time):
        t = self._time(start_time, "startTime")
        self._curve_guard(t)
        return self._insert(Event(SET_VALUE, t, value=value))

    def linear_ramp_to_value_at_time(self, value, end_time):
        t = self._time(end_time, "endTime")
        self._curve_guard(t)
        return self._insert(Event(LINEAR_RAMP, t, value=value))

    def exponential_ramp_to_value_at_time(self, value, end_time):
        if value == 0:
            raise RangeError("exponentialRampToValueAtTime value must not be 0")
        t = self._time(end_time, "endTime")
        self._curve_guard(t)
        return self._insert(Event(EXP_RAMP, t, value=value))

    def set_target_at_time(self, target, start_time, time_constant):
        t = self._time(start_time, "startTime")
        self._curve_guard(t)
        return self._insert(Event(SET_TARGET, t, target=target, tau=time_constant))

    def set_value_curve_at_time(self, values, start_time, duration):
        t = self._time(start_time, "startTime")
        self._curve_guard(t, duration)
        return self._insert(Event(SET_VALUE_CURVE, t, values=list(values),
                                  duration=duration))

    # ------------------------------------------------ 取消
    def cancel_scheduled_values(self, cancel_time):
        t = self._time(cancel_time, "cancelTime")
        self.events = [e for e in self.events if e.time < t]

    def cancel_and_hold_at_time(self, cancel_time):
        t = self._time(cancel_time, "cancelTime")
        held = self.value_at(t)          # 取消前该时刻的值
        kept = []
        for e in self.events:
            if e.time >= t:
                # 曲线跨越 cancelTime：截断到 cancelTime，且保持与原曲线相同的取值
                if e.kind == SET_VALUE_CURVE and e.time < t:
                    kept.append(Event(SET_VALUE_CURVE, e.time, values=e.values,
                                      duration=t - e.time))
                continue
            kept.append(e)
        if kept and kept[-1].kind == SET_VALUE_CURVE and \
                kept[-1].time + kept[-1].duration >= t:
            pass                          # 截断后的曲线自身已给出末值
        else:
            kept.append(Event(SET_VALUE, t, value=held))  # 规范：之后取 tc 时刻的常数值
        self.events = kept

    # ------------------------------------------------ 求值
    def _value_before(self, index):
        """事件 index 起点处的值 V0。"""
        if index == 0:
            return self.value
        return self.value_at(self.events[index].time, upto=index)

    def value_at(self, t, upto=None):
        events = self.events if upto is None else self.events[:upto]
        if not events:
            return self.value
        # 斜坡/曲线的区间是 [前一个事件时刻, endTime)，因此 t 落在区间内时应由它们求值
        nxt = None
        for i, e in enumerate(events):
            if e.time > t:
                nxt = i
                break
        if nxt is not None and events[nxt].kind in (LINEAR_RAMP, EXP_RAMP,
                                                    SET_VALUE_CURVE):
            idx = nxt
        else:
            idx = (nxt - 1) if nxt is not None else len(events) - 1
        if idx < 0:
            return self.value
        e = events[idx]
        if e.kind == SET_VALUE:
            return e.value
        if e.kind == SET_VALUE_CURVE:
            n = len(e.values)
            if t >= e.time + e.duration:
                return e.values[n - 1]
            pos = (n - 1) * (t - e.time) / e.duration
            k = int(math.floor(pos))
            if k >= n - 1:
                return e.values[n - 1]
            frac = pos - k
            return e.values[k] + (e.values[k + 1] - e.values[k]) * frac
        if e.kind == SET_TARGET:
            v0 = self._value_before(idx)
            return e.target + (v0 - e.target) * math.exp(-(t - e.time) / e.tau)
        # 两种斜坡：T0/V0 取前一个事件
        v0 = self._value_before(idx)
        # 无前序事件时，规范规定等价于在当前时刻插入 setValueAtTime(当前值, currentTime)
        t0 = self.current_time if idx == 0 else events[idx - 1].time
        if t >= e.time:
            return e.value
        if abs(e.time - t0) < TOL:
            return e.value
        ratio = (t - t0) / (e.time - t0)
        if e.kind == LINEAR_RAMP:
            return v0 + (e.value - v0) * ratio
        # 指数斜坡
        if v0 == 0 or (v0 > 0) != (e.value > 0):
            return v0
        return v0 * (e.value / v0) ** ratio

    # ------------------------------------------------ 渲染
    def render_block(self, start_time, count, sample_rate):
        """按 automationRate 渲染一块：a-rate 逐样本，k-rate 取块首值。"""
        if self.automation_rate == "k-rate":
            head = self.value_at(start_time)
            return [head] * count
        return [self.value_at(start_time + i / sample_rate) for i in range(count)]


def adsr(param, peak, attack, decay, sustain_level, release, start_time,
         note_duration):
    """用规范允许的自动化搭一个 ADSR（release 段用 setTargetAtTime 逼近 0）。"""
    param.set_value_at_time(0.0, start_time)
    param.linear_ramp_to_value_at_time(peak, start_time + attack)
    param.linear_ramp_to_value_at_time(peak * sustain_level,
                                       start_time + attack + decay)
    note_end = start_time + note_duration
    param.set_value_at_time(peak * sustain_level, note_end)
    # 指数斜坡不能到 0：规范建议改用 setTargetAtTime
    param.set_target_at_time(0.0, note_end, release)
    return param

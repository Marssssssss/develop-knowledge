"""演示入口：五类自动化事件的取值、指数 vs 线性、曲线与取消语义。"""

import math

from param import AudioParam, adsr


def line(values):
    return " ".join("%.3f" % v for v in values)


def main():
    print("== 线性 vs 指数斜坡（同为 0.1 -> 4.0，1 秒）==")
    ts = [0.0, 0.25, 0.5, 0.75, 1.0]
    lin = AudioParam(0.0)
    lin.set_value_at_time(0.1, 0.0)
    lin.linear_ramp_to_value_at_time(4.0, 1.0)
    exp = AudioParam(0.0)
    exp.set_value_at_time(0.1, 0.0)
    exp.exponential_ramp_to_value_at_time(4.0, 1.0)
    print("  t      " + " ".join("%6.2f" % t for t in ts))
    print("  linear " + line([lin.value_at(t) for t in ts]))
    print("  exp    " + line([exp.value_at(t) for t in ts]))
    print("  指数中点是两端几何平均 %.4f，线性是算术平均 %.4f"
          % (math.sqrt(0.1 * 4.0), (0.1 + 4.0) / 2))

    print("\n== setTargetAtTime（tau=0.2，目标 1.0）==")
    tgt = AudioParam(0.0)
    tgt.set_value_at_time(0.0, 0.0)
    tgt.set_target_at_time(1.0, 0.0, 0.2)
    for k in range(5):
        t = k * 0.2
        print("  t=%.1f (%.1f tau) -> %.4f" % (t, k, tgt.value_at(t)))
    print("  一个时间常数只走完 %.2f%%，永远到不了目标" % ((1 - math.exp(-1)) * 100))

    print("\n== setValueCurveAtTime([0,1,2,3], 0, 1s)==")
    cur = AudioParam(0.0)
    cur.set_value_curve_at_time([0.0, 1.0, 2.0, 3.0], 0.0, 1.0)
    print("  采样 (N=4): " + line([cur.value_at(i / 6.0) for i in range(7)]))
    print("  结束后保持末值 %.3f -> %.3f" % (cur.value_at(1.0), cur.value_at(5.0)))

    print("\n== ADSR（attack .1 / decay .2 / sustain .5 / release tau .05）==")
    env = AudioParam(0.0)
    adsr(env, 1.0, 0.1, 0.2, 0.5, 0.05, 0.0, 1.0)
    for t in (0.0, 0.05, 0.1, 0.2, 0.3, 0.9, 1.0, 1.05, 1.2):
        print("  t=%.2f -> %.4f" % (t, env.value_at(t)))
    print("  释音段用 setTargetAtTime：指数斜坡不能到 0，这是规范建议的替代写法")

    print("\n== cancelScheduledValues vs cancelAndHoldAtTime ==")
    base = lambda: adsr(AudioParam(0.0), 1.0, 0.1, 0.2, 0.5, 0.05, 0.0, 1.0)
    a = base()
    a.cancel_scheduled_values(0.2)
    print("  取消前 t=0.20 应为 %.4f" % base().value_at(0.2))
    print("  cancel(0.2) 后 t=0.9 -> %.4f（回落到前一个事件值：起音峰值）" % a.value_at(0.9))
    b = base()
    b.cancel_and_hold_at_time(0.2)
    print("  hold(0.2)   后 t=0.9 -> %.4f（保持取消时刻的值）" % b.value_at(0.9))

    print("\n== a-rate / k-rate（4 采样块，48kHz 下的 1 秒斜坡）==")
    pr = AudioParam(0.0)
    pr.set_value_at_time(0.0, 0.0)
    pr.linear_ramp_to_value_at_time(1.0, 1.0)
    print("  a-rate " + line(pr.render_block(0.0, 4, 4.0)))
    pk = AudioParam(0.0)
    pk.automation_rate = "k-rate"
    pk.set_value_at_time(0.0, 0.0)
    pk.linear_ramp_to_value_at_time(1.0, 1.0)
    print("  k-rate " + line(pk.render_block(0.0, 4, 4.0)))


if __name__ == "__main__":
    main()

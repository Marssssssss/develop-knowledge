#!/usr/bin/env python3
"""Android Service:启动 / 绑定 / 前台 —— 教学模型 + 自检(实跑)。

权威依据:AOSP `frameworks/base/core/java/android/app/Service.java` 的类级 javadoc 与常量注释
(见 README「参考资料」):
  * Service **不是进程、也不是线程**;与其他应用对象一样运行在宿主进程的**主线程**上
  * 已启动(started):startService → onCreate + onStartCommand;多次 startService **不嵌套**
    但会产生多次 onStartCommand;stopService / stopSelf 一次即停
  * `stopSelf(startId)` 原文:「Stop the service if the most recent time it was started was
    startId… Returns true if the startId matches the last start request and the service will be
    stopped, else false」,并警告「若用最近的 ID 在更早的 ID 之前调用,服务会立即停止,使用者有
    责任按收到的顺序停止」
  * 已绑定(bound):bindService → onCreate,**不调用 onStartCommand**;客户端从 onBind 拿到
    IBinder;只要连接存在服务就活着(与客户端是否持有引用无关)
  * 同时启动并被绑定:只要「已启动」或「存在一个 BIND_AUTO_CREATE 连接」其一成立就活着,
    两者都不成立时才 onDestroy
  * onStartCommand 返回值:START_STICKY_COMPATIBILITY=0 / START_STICKY=1 /
    START_NOT_STICKY=2 / START_REDELIVER_INTENT=3
  * startForeground(id, notification) 本身**不会**把服务变为已启动状态(必须先 startService);
    API 28+ 需 FOREGROUND_SERVICE 权限;setForeground() 已废弃且实现为 no-op

模型声明:主线程语义用「回调线程标签恒为 main」表达;「重启计划」只复刻 javadoc 明写的三档语义
(是否重建 / 是否重投递 / 是否会收到 null intent),不代表系统真实的进程杀灭时机。

运行: python3 service_state_check.py
"""

from __future__ import annotations

import sys

from service_model import (MAIN_THREAD, START_NOT_STICKY, START_REDELIVER_INTENT, START_STICKY,
                           SecurityException,
                           START_STICKY_COMPATIBILITY, Service, StopWithDeliveredId,
                           StopWithLatestId, restart_plan)


PASS = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS
    if not cond:
        print(f"FAIL  {label}  {detail}")
        raise AssertionError(label)
    PASS += 1
    print(f"ok    {label}" + (f"  [{detail}]" if detail else ""))


# ------------------------------------------------------------------ 场景
def scenario_not_thread_nor_process() -> None:
    s = Service()
    s.enqueue_start("A")
    s.dispatch()
    s.run_blocking("MP3 解码")
    labels = {e.split(" on ")[-1] for e in s.events if " on " in e}
    check("Service 的回调在宿主进程主线程上执行,不是自己新建的线程",
          "onCreate" in s.events and labels == {MAIN_THREAD}, str(s.events))
    check("耗时/阻塞操作必须自己开线程(模型里显式记录 main 上的工作)",
          any(e.endswith(f"on {MAIN_THREAD}") for e in s.events), str(s.events))


def scenario_started_lifecycle() -> None:
    s = Service()
    for intent in ("A", "B", "C"):
        s.enqueue_start(intent)
        s.dispatch()
    check("多次 startService 不嵌套:onCreate 只一次", s.events.count("onCreate") == 1, str(s.events))
    check("多次 startService → 同样次数的 onStartCommand", s.start_ids() == [1, 2, 3],
          str(s.start_ids()))
    check("服务处于已启动状态", s.started and not s.destroyed, f"started={s.started}")
    s.stop_service()
    check("stopService 一次即停并 onDestroy",
          not s.started and s.destroyed and s.events.count("onDestroy") == 1, str(s.events))


def scenario_stop_self_start_id() -> None:
    single = StopWithDeliveredId()
    single.enqueue_start("only")
    single.dispatch()
    check("stopSelf(startId):startId 恰为最近一次 start → 立即停止",
          single.destroyed and single.start_ids() == [1] and single.events.count("onDestroy") == 1,
          str(single.events))

    stale = Service()
    stale.enqueue_start("A")
    stale.enqueue_start("B")
    stale.stop_self(1)
    check("stopSelf(旧的 startId) 是 no-op:仍有更新的 start 请求未处理",
          not stale.destroyed and any("ignored" in e for e in stale.events), str(stale.events))

    deferred = StopWithDeliveredId()
    deferred.enqueue_start("A")
    deferred.enqueue_start("B")
    deferred.dispatch()
    check("先处理 A 时用 startId=1 请求停止 → 不停止(最近一次 start 是 2)",
          not deferred.destroyed and deferred.started and deferred.queue, str(deferred.events))
    deferred.dispatch()
    check("B 处理完(用 startId=2 再请求)后才真正停止",
          deferred.destroyed and deferred.start_ids() == [1, 2], str(deferred.events))

    out_of_order = StopWithLatestId()
    out_of_order.enqueue_start("A")
    out_of_order.enqueue_start("B")
    out_of_order.dispatch()
    check("乱序用最近 ID 请求停止 → 立即停止(官方警告的具体后果)",
          out_of_order.destroyed and out_of_order.start_ids() == [1], str(out_of_order.events))
    check("因此未处理的 B 永远不会被投递", "intent=B" not in " ".join(out_of_order.events),
          str(out_of_order.events))


def scenario_bound_lifecycle() -> None:
    s = Service()
    binder = s.bind("ClientA")
    check("绑定会创建服务并返回 onBind 的 IBinder", binder.endswith(".Binder") and s.created, binder)
    check("绑定流程不调用 onStartCommand", s.start_ids() == [], str(s.events))
    check("仅被绑定不算「已启动」", s.started is False, f"started={s.started}")
    s.unbind("ClientA")
    check("最后一个连接断开 → onUnbind + onDestroy",
          s.destroyed and any(e.startswith("onUnbind") for e in s.events), str(s.events))

    two = Service()
    two.bind("C1")
    two.bind("C2")
    two.unbind("C1")
    check("还有连接时服务保持存活", not two.destroyed and two.connections == 1,
          f"conns={two.connections}")
    two.unbind("C2")
    check("最后一个连接断开才销毁", two.destroyed, str(two.events))
    check("onBind 只在第一个连接时触发一次",
          sum(1 for e in two.events if e.startswith("onBind")) == 1, str(two.events))


def scenario_started_and_bound() -> None:
    s = Service()
    s.enqueue_start("A")
    s.dispatch()
    s.bind("C1")
    s.stop_service()
    check("同时启动并被绑定:停止请求不销毁(仍有连接)", not s.destroyed, str(s.events))
    check("此时已启动标记为假但连接数为 1", s.started is False and s.connections == 1,
          f"started={s.started} conns={s.connections}")
    s.unbind("C1")
    check("连接断开后才 onDestroy", s.destroyed, str(s.events))
    check("绑定前已发生过一次 onStartCommand", s.start_ids() == [1], str(s.start_ids()))


def scenario_return_codes() -> None:
    check("四个常量取值与 AOSP 源码一致",
          (START_STICKY_COMPATIBILITY, START_STICKY, START_NOT_STICKY, START_REDELIVER_INTENT)
          == (0, 1, 2, 3), "0/1/2/3")
    check("START_STICKY:无待投递 intent 时重建并收到 null intent",
          restart_plan(START_STICKY, False) == {"restart": True, "redeliver": False,
                                                "null_intent": True},
          str(restart_plan(START_STICKY, False)))
    check("START_STICKY:有待投递 intent 时不会给 null intent",
          restart_plan(START_STICKY, True)["null_intent"] is False,
          str(restart_plan(START_STICKY, True)))
    check("START_NOT_STICKY:不重建,也不会收到 null intent",
          restart_plan(START_NOT_STICKY, False) == {"restart": False, "redeliver": False,
                                                    "null_intent": False},
          str(restart_plan(START_NOT_STICKY, False)))
    check("START_REDELIVER_INTENT:重建并重投最后一次 intent",
          restart_plan(START_REDELIVER_INTENT, True) == {"restart": True, "redeliver": True,
                                                         "null_intent": False},
          str(restart_plan(START_REDELIVER_INTENT, True)))
    check("START_STICKY_COMPATIBILITY:不保证再收到 onStartCommand",
          restart_plan(START_STICKY_COMPATIBILITY, False)["null_intent"] is False,
          str(restart_plan(START_STICKY_COMPATIBILITY, False)))
    music = Service(default_result=START_STICKY)
    music.enqueue_start("play")
    music.dispatch()
    check("返回值被记录为 onStartCommand 的返回码", music.return_code == START_STICKY,
          str(music.return_code))


def scenario_foreground() -> None:
    s = Service()
    s.foreground_permission = True
    s.start_foreground(101)
    check("startForeground 本身不会把服务变成「已启动」", s.started is False and s.foreground,
          f"started={s.started} foreground={s.foreground}")

    gate = Service()
    try:
        gate.start_foreground(1, api_level=28)
        blocked = False
    except SecurityException as exc:
        blocked = "FOREGROUND_SERVICE" in str(exc)
    check("API 28+ 缺 FOREGROUND_SERVICE 权限时被拒绝", blocked, "SecurityException")

    s2 = Service()
    s2.foreground_permission = True
    s2.enqueue_start("A")
    s2.dispatch()
    s2.start_foreground(202)
    s2.stop_service()
    check("服务销毁时前台通知被撤下",
          s2.destroyed and "cancelNotification(id=202)" in s2.events, str(s2.events))
    check("销毁后前台标记被清除", s2.foreground is False, f"foreground={s2.foreground}")

    s3 = Service()
    s3.set_foreground(True)
    check("setForeground() 是废弃的 no-op(仅记录忽略日志)",
          s3.events == ["setForeground: ignoring old API call"] and s3.foreground is False,
          str(s3.events))


def main() -> int:
    for fn in (scenario_not_thread_nor_process, scenario_started_lifecycle,
               scenario_stop_self_start_id, scenario_bound_lifecycle, scenario_started_and_bound,
               scenario_return_codes, scenario_foreground):
        print(f"\n--- {fn.__name__} ---")
        fn()
    print(f"\nALL PASS: {PASS} assertions")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"\nFAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)

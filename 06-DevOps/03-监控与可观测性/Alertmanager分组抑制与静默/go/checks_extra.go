package main

// checkNotifications 覆盖通知节奏:group_wait 首发、group_interval 检查节拍、
// changed 与 repeat 的判定,以及 repeat 向上取整到 group_interval 的倍数。
func checkNotifications() {
	eng := NewNotificationEngine()
	check("G1 默认 group_wait", near(eng.GroupWait, 30), "")
	check("G2 默认 group_interval", near(eng.GroupInterval, 300), "")
	check("G3 默认 repeat_interval", near(eng.RepeatInterval, 14400), "")
	check("G4 repeat 为倍数时不变", near(eng.EffectiveRepeat(), 14400), "")
	check("G5 非倍数向上取整", near(EffectiveRepeatInterval(400, 300), 600), "")
	check("G6 恰好整倍数", near(EffectiveRepeatInterval(600, 300), 600), "")
	g := NewGroup("g", 0)
	fps := map[string]bool{"fp1": true}
	check("G7 group_wait 前不发", eng.Dispatch(g, 0, fps) == "", "")
	check("G8 差 1 秒仍不发", eng.Dispatch(g, 29, fps) == "", "")
	check("G9 group_wait 到点首发", eng.Dispatch(g, 30, fps) == "first", "")
	check("G10 记录发送时刻", g.HasSent && near(g.LastSent, 30) && len(g.Sends) == 1, "")
	check("G11 group_interval 内不发", eng.Dispatch(g, 100, fps) == "", "")
	check("G12 无变化时不发", eng.Dispatch(g, 330, fps) == "", "")
	fps2 := map[string]bool{"fp1": true, "fp2": true}
	check("G13 新增告警触发 changed", eng.Dispatch(g, 330, fps2) == "changed", "")
	check("G14 距上次未满 group_interval 不发", eng.Dispatch(g, 400, fps2) == "", "")
	check("G15 告警 resolved 也触发 changed", eng.Dispatch(g, 630, fps) == "changed", "")
	check("G16 长时间无变化等 repeat", eng.Dispatch(g, 1000, fps) == "", "")
	check("G17 repeat_interval 到点重发", eng.Dispatch(g, 630+14400, fps) == "repeat", "")
	check("G18 next_check_at 反映节拍", near(eng.NextCheckAt(g), 630+14400+300), "")
	g2 := NewGroup("g2", 0)
	check("G19 group_wait 内全部 resolved 不发通知(天然防抖)",
		eng.Dispatch(g2, 100, map[string]bool{}) == "" && !g2.HasSent, "")
	g3 := NewGroup("g3", 0)
	short := &NotificationEngine{GroupWait: 30, GroupInterval: 300, RepeatInterval: 600}
	short.Dispatch(g3, 30, map[string]bool{"f": true})
	check("G20 自定义 repeat 生效", short.Dispatch(g3, 630, map[string]bool{"f": true}) == "repeat", "")
	check("G21 未到自定义 repeat 不发", short.Dispatch(g3, 630, map[string]bool{"f": true}) == "", "")
	check("G22 发送记录含原因", len(g.Sends) >= 4 && g.Sends[0].Reason == "first", "")
}

package main

// 存储与摄入侧校验（G~J）。由 main.go 调用，共用同一套计数器与 section 变量。

import "fmt"

func runExtraChecks() {
	section = "G"
	ing, errIng := NewIngester(DefaultChunkEncoding, 1572864, 1800, 7200, true)
	noErr("G1 构造 ingester", errIng)
	eqStr("G2 递增时间戳可追加", ing.Push("team-a", apiLabels(), t0, "line0"), "appended")
	eqStr("G2b 第二条", ing.Push("team-a", apiLabels(), t0+sec, "line1"), "appended")
	eqStr("G3 同 ts 同内容 = 重复被静默忽略", ing.Push("team-a", apiLabels(), t0+sec, "line1"), "duplicate")
	eqStr("G4 同 ts 不同内容 = 接受(坑)", ing.Push("team-a", apiLabels(), t0+sec, "other"), "appended")
	eqStr("G5 时间戳回退被拒", ing.Push("team-a", apiLabels(), t0-sec, "back"), "out_of_order")
	stream := ing.Stream("team-a", apiLabels())
	eqInt("G5b 被拒的行没有入账", len(stream.Entries), 3)
	eqInt("G5c 重复计数单独统计", stream.IgnoredDuplicates, 1)
	eqInt("G5d 乱序计数单独统计", stream.RejectedOutOfOrder, 1)
	eqStrs("G6 空闲 30m 触发刷写", stream.FlushReasons(t0+1800*sec), []string{"idle"})
	eqStrs("G6b 未到 30m 不触发", stream.FlushReasons(t0+1799*sec), []string{})
	tri := &Stream{Tenant: "t", Encoding: "gzip", TargetSize: 100, IdlePeriodS: 1800, MaxAgeS: 7200}
	for i := 0; i < 6; i++ {
		tri.Push(t0+int64(i)*sec, repeatRune(100, 'x'))
	}
	eqStrs("G7 三条件可同时成立且顺序固定", tri.FlushReasons(t0+7200*sec), []string{"idle", "size", "age"})
	chunk := tri.Flush("idle")
	eqInt("G8 刷写后 chunk 记录条目数", chunk.Entries, 6)
	eqInt("G8b 刷写后内存清空", len(tri.Entries), 0)
	eqInt("G8c flush 记录进 Flushed", len(tri.Flushed), 1)
	eqBool("G9 对象键带租户前缀(存储层隔离)", ChunkObjectKey("t", apiLabels(), 1, 2, "c")[:2] == "t/", true)
	eqBool("G9b 租户不同则对象键不同",
		ChunkObjectKey("a", apiLabels(), 1, 2, "c") != ChunkObjectKey("b", apiLabels(), 1, 2, "c"), true)
	q3, _ := Quorum(3)
	eqInt("G10 rf=3 时仲裁数 2", q3, 2)
	q5, _ := Quorum(5)
	eqInt("G10b rf=5 时仲裁数 3", q5, 3)
	q2, _ := Quorum(2)
	eqInt("G10c rf=2 时仲裁数 2(两副本都要成功)", q2, 2)
	_, errQ := Quorum(0)
	wantErr("G10d rf=0 报错", errQ)
	walIng, _ := NewIngester(DefaultChunkEncoding, 1572864, 1800, 7200, true)
	for i := 0; i < 3; i++ {
		walIng.Push("t", apiLabels(), t0+int64(i)*sec, fmt.Sprintf("l%d", i))
	}
	eqInt("G11 WAL 记录未刷写数据", len(walIng.WAL.Pending()), 3)
	walIng.Crash()
	eqInt("G11b 崩溃后内存流清空", len(walIng.Streams), 0)
	eqInt("G11c WAL 重放恢复全部条目", walIng.Recover()[RegistryKey("t", SeriesKey(apiLabels()))], 3)
	sdIng, _ := NewIngester(DefaultChunkEncoding, 1572864, 1800, 7200, true)
	for i := 0; i < 3; i++ {
		sdIng.Push("t", apiLabels(), t0+int64(i)*sec, fmt.Sprintf("l%d", i))
	}
	eqInt("G12 优雅关闭会刷写 chunk", len(sdIng.Shutdown()), 1)
	eqInt("G12b 刷写后 WAL 可截断", len(sdIng.WAL.Pending()), 0)
	eqInt("G12c 优雅关闭后无需重放", len(sdIng.Recover()), 0)
	ckIng, _ := NewIngester(DefaultChunkEncoding, 1572864, 1800, 7200, true)
	ckIng.Push("t", apiLabels(), t0, "x")
	eqBool("G13 未到 checkpoint 周期不动", ckIng.WAL.Checkpoint(t0+100*sec), false)
	eqBool("G13b 到 5m 折叠 checkpoint", ckIng.WAL.Checkpoint(t0+300*sec), true)
	eqInt("G13c checkpoint 后无待重放记录", len(ckIng.WAL.Pending()), 0)
	eqStr("G14 chunk_encoding 默认 gzip", DefaultChunkEncoding, "gzip")
	eqBool("G14b 官方最佳实践推荐 snappy(≠ 默认)", DefaultChunkEncoding == "snappy", false)
	eqInt("G15 未压缩上限 256KiB", int(chunkDefaults["chunk_block_size"]), 262144)
	eqInt("G15b 压缩后目标 1.5MiB", int(chunkDefaults["chunk_target_size"]), 1572864)
	eqInt("G15c 存活上限 2h", int(chunkDefaults["max_chunk_age"]), 7200)
	_, errEncoding := NewIngester("brotli", 1572864, 1800, 7200, true)
	wantErr("G16 未知 chunk_encoding 报错", errEncoding)

	section = "H"
	limits := DefaultLimits()
	eqStr("H1 行长等于上限可通过", CheckLine(limits, 262144), "ok")
	eqStr("H2 超限默认拒绝(truncate=false)", CheckLine(limits, 262145), "rejected")
	truncLimits := limits
	truncLimits.MaxLineSizeTruncate = true
	eqStr("H3 开启 truncate 则截断而非拒绝", CheckLine(truncLimits, 262145), "truncated")
	twentyNine := map[string]string{}
	for i := 0; i < 29; i++ {
		twentyNine[fmt.Sprintf("l%d", i)] = "v"
	}
	eqStr("H4 标签数未超限可通过", CheckLabels(limits, twentyNine), "ok")
	thirtyOne := map[string]string{}
	for i := 0; i < 31; i++ {
		thirtyOne[fmt.Sprintf("l%d", i)] = "v"
	}
	eqStr("H5 标签数超限被拒", CheckLabels(limits, thirtyOne), "too_many_labels")
	eqStr("H6 标签名过长被拒", CheckLabels(limits, map[string]string{repeatRune(1025, 'x'): "v"}), "label_name_too_long")
	tight := limits
	tight.MaxGlobalStreamsPerUser = 3
	registry := NewStreamRegistry(1800)
	for i := 0; i < 3; i++ {
		registry.Touch("t", fmt.Sprintf("a%d", i), t0)
	}
	eqInt("H7 已存在的流不占新额度", registry.Admit("t", "a1", tight, t0).Status, 200)
	eqInt("H8 超上限的新流返回 429", registry.Admit("t", "anew", tight, t0).Status, 429)
	eqStr("H8b 超限原因串", registry.Admit("t", "anew", tight, t0).Reason, "stream_limit")
	eqInt("H9 流上限按租户隔离", registry.Admit("t2", "x", tight, t0).Status, 200)
	eqInt("H10 活跃窗口到期后额度释放", registry.Admit("t", "anew", tight, t0+1801*sec).Status, 200)

	section = "I"
	eqStr("I1 tsdb+v13 可启动", SchemaCheck(SchemaStoreTSDB, SchemaV13, true, false), "")
	eqBool("I2 boltdb-shipper 已移除", SchemaCheck("boltdb-shipper", SchemaV13, true, false) != "", true)
	eqBool("I3 非 tsdb 触发配置错误", SchemaCheck("boltdb", SchemaV13, true, false) != "", true)
	eqBool("I4 v12 触发 schema 版本错误", SchemaCheck(SchemaStoreTSDB, "v12", true, false) != "", true)
	eqStr("I5 关掉 structured metadata 后老配置可用", SchemaCheck("boltdb", "v11", false, false), "")
	version13, _ := SchemaVersionNumber("v13")
	version9, _ := SchemaVersionNumber("v9")
	eqBool("I6 schema 版本按数字比较", version13 > version9, true)
	eqBool("I7 tsdb 只接受 24h", ValidateIndexPeriod(SchemaStoreTSDB, "12h"), false)
	eqInt("I8 row_shards 默认 16", DefaultRowShards, 16)

	section = "J"
	longDur, errDur := ParseDuration("1h30m")
	noErr("J1 复合时长解析", errDur)
	eqFloat("J1b 1h30m = 5400s", longDur, 5400)
	shortDur, _ := ParseDuration("5m")
	eqFloat("J2 5m = 300s", shortDur, 300)
	kb, _ := ParseBytes("256KB")
	eqFloat("J3 字节 256KB 是 1024 进制", kb, 262144)
	mb, _ := ParseBytes("1.5MB")
	eqFloat("J4 1.5MB 与 chunk_target_size 对齐", mb, 1572864)
	_, errUnit := ParseDuration("5q")
	wantErr("J5 未知时长单位报错", errUnit)
}

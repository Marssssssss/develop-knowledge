// lsm_check.go — 自检:逐条核对 leveldb / rocksdb 官方文档里的明文规则
//
// 与 lsm_engine.go / lsm_compact.go / lsm_formats.go 同属 package main。
// 断言出处见各文件头注释。
//
// 运行: go run *.go   (或逐一列出:lsm_formats.go lsm_types.go lsm_engine.go lsm_levels.go lsm_compact.go lsm_check.go)
package main

import (
	"fmt"
	"os"
	"strings"
)

var pass, fail int

func check(label string, cond bool, detail ...string) {
	if cond {
		pass++
		fmt.Println("  PASS ", label)
		return
	}
	fail++
	extra := ""
	if len(detail) > 0 {
		extra = detail[0]
	}
	fmt.Println("  FAIL ", label, extra)
}

func smallCfg() LSMConfig {
	c := DefaultLSMConfig()
	c.WriteBufferSize = 200
	c.MaxWriteBufferNumber = 4
	c.Level0FileNumCompactionTrigger = 4
	c.NumLevels = 4
	c.TargetFileSizeBase = 100000
	c.MaxBytesForLevelBase = 100000000
	return c
}

func main() {
	fmt.Println("[1] WAL 日志格式:32KB 块 + 7 字节头部(官方 doc/log_format.md)")
	check("块大小 32768(32KB)", BlockSize == 32768)
	check("头部 4+2+1=7 字节", RecordHeader == 4+2+1)
	check("类型编号 FULL=1 FIRST=2 MIDDLE=3 LAST=4",
		Full == 1 && First == 2 && Middle == 3 && Last == 4)

	fmt.Println("[2] 官方示例:A(1000) / B(97270) / C(8000)")
	w := NewLogWriter()
	fragA := w.Append(1000)
	fragB := w.Append(97270)
	fragC := w.Append(8000)
	check("A 是一条 FULL 记录", len(fragA) == 1 && fragA[0].Label() == "FULL")
	labels := []string{}
	for _, f := range fragB {
		labels = append(labels, f.Label())
	}
	check("B 被切成 FIRST/MIDDLE/LAST 三段",
		strings.Join(labels, "/") == "FIRST/MIDDLE/LAST", strings.Join(labels, "/"))
	check("B 的 FIRST 占满第一块剩余空间",
		fragB[0].PhysLen == BlockSize-fragA[0].PhysLen)
	check("B 的 MIDDLE 独占整块", fragB[1].PhysLen == BlockSize)
	check("C 是下一条 FULL 记录", len(fragC) == 1 && fragC[0].Label() == "FULL")
	check("第三块空出 6 字节当 trailer(官方原文)", w.TrailerBytes == 6,
		fmt.Sprint(w.TrailerBytes))
	check("C 落在第 4 个块(下标 3)", fragC[0].Block == 3, fmt.Sprint(fragC[0].Block))
	lens := ReadLog(w)
	check("用户记录可原样还原", fmt.Sprint(lens) == "[1000 97270 8000]", fmt.Sprint(lens))

	fmt.Println("[3] 两个边界规则")
	w2 := NewLogWriter()
	w2.Offset = BlockSize - 7
	f2 := w2.Append(5)
	check("剩余恰好 7 字节 → 写零字节数据的 FIRST 填满该块(官方 Aside)",
		len(f2) == 2 && f2[0].Label() == "FIRST" && f2[0].DataLen == 0 && f2[0].PhysLen == 7)
	check("随后在下一块写 LAST", f2[1].Label() == "LAST" && f2[1].Block == 1)
	startsOK := true
	w3 := NewLogWriter()
	for _, n := range []int{100, 900, 40000, 20} {
		for _, f := range w3.Append(n) {
			if BlockSize-f.Offset < RecordHeader {
				startsOK = false
			}
		}
	}
	check("任何记录都不会从块最后 6 字节内开始", startsOK)

	fmt.Println("[4] varint 与 BlockHandle")
	check("150 → 0x96 0x01(protobuf 同款 varint)",
		fmt.Sprintf("%x", EncodeVarint(150)) == "9601", fmt.Sprintf("%x", EncodeVarint(150)))
	roundOK := true
	for _, v := range []uint64{0, 1, 127, 128, 300, 16383, 16384, 1 << 40} {
		got, pos, err := DecodeVarint(EncodeVarint(v), 0)
		if err != nil || got != v || pos != len(EncodeVarint(v)) {
			roundOK = false
		}
	}
	check("varint 往返一致且解码位置正确", roundOK)
	_, _, err := DecodeVarint([]byte{0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff}, 0)
	check("超过 10 字节的 varint 视为损坏", err != nil)

	fmt.Println("[5] SSTable footer(官方 doc/table_format.md)")
	check("footer 定长 48 字节(2*20 + 8)", FooterSize == 48)
	check("magic == 0xdb4775248b80fb57", FooterMagicHex() == "0xdb4775248b80fb57", FooterMagicHex())
	f, err := EncodeFooter(BlockHandle{100, 20}, BlockHandle{200, 30})
	check("footer 长度为 48", err == nil && len(f) == 48)
	meta, idx, err := DecodeFooter(f)
	check("metaindex / index handle 往返一致",
		err == nil && meta.Offset == 100 && meta.Size == 20 && idx.Offset == 200 && idx.Size == 30)
	_, _, err = DecodeFooter(make([]byte, FooterSize))
	check("magic 不符时报错", err != nil)
	lay := TableLayout([]int{1000, 2000}, []int{50}, 64)
	check("布局顺序 data → meta → metaindex → index → footer",
		lay["data_offset"] == 0 && lay["meta_offset"] == 3000 &&
			lay["index_offset"] > lay["metaindex_offset"] && lay["metaindex_offset"] > lay["meta_offset"])
	check("footer 起于 file_size - 48(官方原文)",
		lay["footer_starts_at"] == lay["file_size"]-FooterSize)

	fmt.Println("[6] filter 元块:base 2KB")
	check("base 常量是 2048", FilterBase == 2048)
	check("偏移 0 与 2047 落第 0 个 filter",
		FilterIndexForOffset(0, FilterBase) == 0 && FilterIndexForOffset(2047, FilterBase) == 0)
	check("偏移 2048 落第 1 个 filter", FilterIndexForOffset(2048, FilterBase) == 1)
	fl := FilterBlockLayout(3, FilterBase)
	check("块尾顺序:偏移数组 → 数组起点 → lg(base)",
		fl["trailer_positions"] == 4*3+5 && fl["array_anchor"] == 4)
	check("lg(base) == 11", fl["lg_base_value"] == 11)

	fmt.Println("[7] 层级目标:静态(LevelDB 10^L MB)")
	c7 := DefaultLSMConfig()
	c7.MaxBytesForLevelBase = 10 * MB
	e7 := NewLSMEngine(c7)
	check("L1 目标 = max_bytes_for_level_base", e7.LevelTarget(1) == 10*MB)
	check("L2 目标 100MB / L3 目标 1000MB",
		e7.LevelTarget(2) == 100*MB && e7.LevelTarget(3) == 1000*MB)
	check("multiplier 默认 10", e7.Cfg.MaxBytesForLevelMultiplier == 10)

	fmt.Println("[8] 层级目标:动态层级(官方示例 base=1GB / 末层 276GB)")
	GB := 1024 * MB
	// 口径说明:wiki 示例写 num_levels=6 而层级标号为 L1..L6;本项目按代码口径把
	// num_levels 解释为"含 L0 的总层数",故取 num_levels=7 → 非 0 层正好是 L1..L6。
	t := DynamicTargetsFrom(map[int]int{6: 276 * GB}, 7, 1*GB, 10)
	check("末层(L6)目标 = 末层实际大小 276GB", t[6] == 276*GB)
	check("逐层除以 10:L5=27.6GB L4=2.76GB L3=0.276GB",
		t[6]/10 == t[5] && t[5]/10 == t[4] && t[4]/10 == t[3])
	check("低于 base/multiplier(0.1GB)的层保持空 → L1=L2=0", t[1] == 0 && t[2] == 0)
	seq := fmt.Sprintf("%.3f/%.3f/%.3f/%.3f/%.3f",
		float64(t[1])/float64(GB), float64(t[2])/float64(GB),
		float64(t[3])/float64(GB), float64(t[4])/float64(GB), float64(t[5])/float64(GB))
	check("官方示例的 L1..L5 = 0 / 0 / 0.276 / 2.76 / 27.6 GB",
		seq == "0.000/0.000/0.276/2.760/27.600", seq)
	total := 0
	for _, v := range t {
		total += v
	}
	ratio := float64(t[6]) / float64(total)
	check("保证约 90% 数据落在末层(官方原文)", ratio > 0.87 && ratio < 0.93,
		fmt.Sprintf("%.4f", ratio))

	fmt.Println("[9] 写停顿三层判定(官方 wiki/Write-Stalls.md)")
	c9 := DefaultLSMConfig()
	c9.MaxWriteBufferNumber = 5
	e9 := NewLSMEngine(c9)
	for i := 0; i < 4; i++ {
		e9.Immutables = append(e9.Immutables, &MemTable{})
	}
	state, why := e9.WriteStallState()
	check("max=5 且有 4 个不可变 memtable → stall(提前一个)",
		state == "stall" && why == "too_many_immutable_memtables", state+"/"+why)
	e9.Immutables = append(e9.Immutables, &MemTable{})
	state, why = e9.WriteStallState()
	check("达到 max_write_buffer_number → 完全停止",
		state == "stop" && why == "too_many_immutable_memtables", state+"/"+why)
	c9b := DefaultLSMConfig()
	c9b.MaxWriteBufferNumber = 2
	e9b := NewLSMEngine(c9b)
	e9b.Immutables = append(e9b.Immutables, &MemTable{})
	state, _ = e9b.WriteStallState()
	check("max=2 时 1 个不可变不 stall(不适用 >3 的提前规则)", state == "ok", state)
	c9c := DefaultLSMConfig()
	c9c.Level0SlowdownWritesTrigger = 4
	c9c.Level0StopWritesTrigger = 20
	e9c := NewLSMEngine(c9c)
	for i := 0; i < 4; i++ {
		e9c.Levels[0] = append(e9c.Levels[0], &SSTable{})
	}
	state, why = e9c.WriteStallState()
	check("L0 达到 slowdown 触发数 → stall",
		state == "stall" && why == "too_many_level0_files", state+"/"+why)
	for i := 0; i < 16; i++ {
		e9c.Levels[0] = append(e9c.Levels[0], &SSTable{})
	}
	state, why = e9c.WriteStallState()
	check("L0 达到 stop 触发数 → 停止",
		state == "stop" && why == "too_many_level0_files", state+"/"+why)
	e9c.Levels[0] = []*SSTable{}
	e9c.PendingCompactionBytes = e9c.Cfg.SoftPendingCompactionBytes
	state, _ = e9c.WriteStallState()
	check("待压缩字节达软限 → stall", state == "stall", state)
	e9c.PendingCompactionBytes = e9c.Cfg.HardPendingCompactionBytes
	state, _ = e9c.WriteStallState()
	check("待压缩字节达硬限 → 停止", state == "stop", state)

	fmt.Println("[10] 写路径与删除标记")
	e10 := NewLSMEngine(DefaultLSMConfig())
	if err := e10.Put("k1", "v1"); err != nil {
		check("put 不应报错", false, err.Error())
	}
	v, ok := e10.Get("k1")
	check("memtable 立即可读", ok && v == "v1")
	e10.Put("k1", "v2")
	v, _ = e10.Get("k1")
	check("同一 key 后写覆盖前写", v == "v2")
	e10.Delete("k1")
	_, ok = e10.Get("k1")
	check("删除标记遮住 memtable 里的旧值", !ok)
	e10.Put("k2", "v2")
	v, ok = e10.Get("k2")
	check("删除不影响其他 key", ok && v == "v2")

	fmt.Println("[11] flush 与 L0 → L1 compaction")
	e11 := NewLSMEngine(smallCfg())
	for i := 0; i < 40; i++ {
		e11.Put(fmt.Sprintf("k%03d", i), strings.Repeat("v", 40))
	}
	check("memtable 满后自动 flush 出文件",
		len(e11.Levels[0]) > 0 || len(e11.Levels[1]) > 0, e11.LevelSummary())
	check("L0 攒够触发数后压到 L1", len(e11.Levels[1]) > 0, e11.LevelSummary())
	allOK := true
	for i := 0; i < 40; i++ {
		v, ok := e11.Get(fmt.Sprintf("k%03d", i))
		if !ok || v != strings.Repeat("v", 40) {
			allOK = false
		}
	}
	check("所有 key 仍可读到", allOK)
	check("写放大 >= 1(压缩会重写数据)", e11.WriteAmplification() >= 1.0,
		fmt.Sprintf("%.2f", e11.WriteAmplification()))

	fmt.Println("[12] 得分与挑层")
	c12 := DefaultLSMConfig()
	c12.Level0FileNumCompactionTrigger = 4
	c12.MaxBytesForLevelBase = 100
	c12.NumLevels = 4
	e12 := NewLSMEngine(c12)
	for i := 0; i < 3; i++ {
		e12.Levels[0] = append(e12.Levels[0], &SSTable{})
	}
	check("L0 未达触发数 → 得分为 0(官方原文)", e12.CompactionScores()[0] == 0.0)
	e12.Levels[0] = append(e12.Levels[0], &SSTable{})
	check("L0 达触发数 → 得分 >= 1", e12.CompactionScores()[0] >= 1.0)
	lvl, ok := e12.PickLevel()
	check("pick_level 取最高分那一层", ok && lvl == 0)
	e12.Levels[1] = []*SSTable{{Entries: []Entry{{Key: "k", Value: strings.Repeat("y", 200)}}}}
	s1 := e12.CompactionScores()[1]
	check("L1 超过目标 → L1 得分 > 1", s1 > 1.0, fmt.Sprintf("%.2f", s1))
	lvl, ok = e12.PickLevel()
	check("两层都超时取分高者", ok && lvl == 1, fmt.Sprint(lvl))

	fmt.Println("[13] 删除标记的丢弃条件(LevelDB doc/impl.md 原文)")
	e13 := NewLSMEngine(DefaultLSMConfig())
	check("更深层无文件覆盖该 key → 可丢删除标记", e13.CanDropDelete("k1", 1))
	e13.Levels[3] = []*SSTable{NewSSTable(99, 3, []Entry{{Key: "k1", Seq: 1, Value: "x"}, {Key: "k9", Seq: 2, Value: "y"}})}
	check("更深层(L3)有文件覆盖该 key → 不可丢", !e13.CanDropDelete("k1", 1))
	check("范围不覆盖时仍可丢", e13.CanDropDelete("z9", 1))
	ents := []Entry{{Key: "k1", Seq: 3, Kind: DeleteKind}, {Key: "k1", Seq: 1, Value: "old"}}
	kept := CompactEntries(ents, func(string) bool { return false })
	check("压缩只保留每个 key 的最新版本", len(kept) == 1 && kept[0].Seq == 3)
	check("允许丢删除标记时,标记被移除", len(CompactEntries(ents, func(string) bool { return true })) == 0)
	dup := []Entry{{Key: "a", Seq: 2, Value: "new"}, {Key: "a", Seq: 1, Value: "old"}}
	check("被弃的旧值不写盘,写放大因此下降", len(CompactEntries(dup, func(string) bool { return false })) == 1)

	fmt.Println("")
	fmt.Printf("断言总数 %d,失败 %d\n", pass+fail, fail)
	if fail > 0 {
		os.Exit(1)
	}
	fmt.Println("全部通过")
}

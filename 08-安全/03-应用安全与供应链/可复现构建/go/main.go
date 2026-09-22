// 可复现构建演示：同一份源码在两台机器上构建，看每一处阻力需要什么手段消掉。
package main

import "fmt"

const sde = int64(1600000000)

func sources(mtime int64, path string, uid int) []Entry {
	return []Entry{
		{Name: "src/main.c", Data: "assert(__FILE__ \"" + path + "\")", Mtime: mtime,
			Mode: 0o644, UID: uid, GID: 1000, Uname: "alice", Gname: "alice"},
		{Name: "src/util.c", Data: "// built in " + path, Mtime: mtime + 5,
			Mode: 0o644, UID: uid, GID: 1000, Uname: "alice", Gname: "alice"},
		{Name: "README.md", Data: "docs", Mtime: mtime - 100,
			Mode: 0o644, UID: uid, GID: 1000, Uname: "alice", Gname: "alice"},
	}
}

func demo() {
	fmt.Println("可复现构建：两机器构建同一份源码")
	env := map[string]string{"SOURCE_DATE_EPOCH": "1600000000"}
	rawA, _ := Build(sources(1600100000, "/home/alice/proj", 1000), env, 0, 0o022,
		"/home/alice/proj", "C", false)
	rawB, _ := Build(sources(1700200000, "/home/bob/proj", 2000), env, 0, 0o002,
		"/home/bob/proj", "C", false)
	fmt.Printf("  原始产物 : %s...\n           : %s...\n  一致? %v\n",
		rawA[:16], rawB[:16], rawA == rawB)

	normA, _ := Build(sources(1600100000, "/home/alice/proj", 1000), env, 0, 0o022,
		"/home/alice/proj", "C", true)
	normB, _ := Build(sources(1700200000, "/home/bob/proj", 2000), env, 0, 0o022,
		"/home/bob/proj", "C", true)
	fmt.Printf("  归一化后 : %s...\n           : %s...\n  一致? %v（umask 已统一为 022）\n",
		normA[:16], normB[:16], normA == normB)

	fmt.Println()
	fmt.Println("SOURCE_DATE_EPOCH 的作用")
	bt, _ := BuildTime(env, 1700000000)
	fmt.Printf("  构建时间            : %d（墙上时钟是 %d）\n", bt, int64(1700000000))
	fmt.Printf("  时间戳钳制（上界）   : %d -> %d\n", sde+86400, ClampMtime(sde+86400, sde))
	fmt.Printf("  早于 SDE 的时间戳   : %d -> %d（保留）\n", sde-86400, ClampMtime(sde-86400, sde))
	fmt.Printf("  ZIP 下界 1980-01-01 : %d -> %d\n", 0, ZipDatetime(0, sde))
	fmt.Printf("  ZipEpochMin         : %d = %s\n", ZipEpochMin, FormatDate(ZipEpochMin, 0))

	fmt.Println()
	// 刻意挑一个跨日的时刻：UTC 当天 23:30
	near := sde - sde%86400 + 23*3600 + 1800
	fmt.Println("时区会改变格式化出来的日期（UTC 当天 23:30 这一刻）")
	for _, off := range []int64{0, 2 * 3600, -8 * 3600} {
		fmt.Printf("  TZ 偏移 %+6d 秒 -> %s\n", off, FormatDate(near, off))
	}

	fmt.Println()
	fmt.Println("区域设置改变目录遍历顺序")
	names := []string{"B.txt", "a.txt", "_x.txt", "C.txt"}
	fmt.Printf("  LC_ALL=C          : %v\n", SortNames(names, "C"))
	fmt.Printf("  LC_ALL=en_US.UTF-8: %v\n", SortNames(names, "en_US.UTF-8"))

	fmt.Println()
	fmt.Println("umask 归一化管不了，只能靠固定环境")
	for _, u := range []int{0o022, 0o002, 0o077} {
		fmt.Printf("  umask %04o -> mode 0o666 落成 %04o\n", u, ApplyUmask(0o666, u))
	}

	fmt.Println()
	fmt.Println("畸形 SOURCE_DATE_EPOCH 应当让构建失败")
	_, err := Build(sources(1600100000, "/x", 1000),
		map[string]string{"SOURCE_DATE_EPOCH": "not-a-number"}, 0, 0o022, "/x", "C", true)
	fmt.Printf("  %v\n", err)
}

func main() {
	demo()
}

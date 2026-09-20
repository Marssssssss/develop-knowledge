// anti_selfcheck.go — 与 selfcheck_anti.py 同判据的 Go 自检入口。
package main

import "fmt"

var passed int

func check2(cond bool, label string) {
	if !cond {
		panic("FAILED: " + label)
	}
	passed++
}

func selfcheck() {
	// 三层 API
	check2(len(layers) == 3, "三个 API 层")
	hasJava, hasNative, hasSyscall := false, false, false
	for _, l := range checkLayer {
		switch l {
		case layerJava:
			hasJava = true
		case layerNative:
			hasNative = true
		case layerSyscall:
			hasSyscall = true
		}
	}
	check2(hasJava && hasNative && hasSyscall, "三个层都有检查")

	// Frida 默认端口与敏感 API 清单
	check2(fridaDefaultPort == 27047, "Frida 默认监听端口")
	check2(len(sensitiveHookTargets) == 12, "MASTG-TEST-0354 列出 12 个敏感 API")

	// 干净设备
	clean := Device{ForkedPID: -1}
	for name := range checkLayer {
		check2(!check(clean, name), "干净设备上检查不响: "+name)
	}

	// 触发条件
	check2(check(Device{DebuggerAttached: true}, "debug_is_debugger_connected"), "调试器附加")
	check2(check(Device{Debuggable: true}, "app_info_flag_debuggable"), "FLAG_DEBUGGABLE")
	check2(check(Device{SuBinary: true}, "root_su_binary"), "存在 su")
	check2(check(Device{RootPackages: []string{"com.topjohnwu.magisk"}}, "root_package_present"), "root 包")
	check2(check(Device{ThirdPartyStores: []string{"Sileo", "Zebra"}}, "jailbreak_third_party_store"), "第三方商店")
	check2(check(Device{TracerPID: 1234}, "native_tracer_pid"), "TracerPid 非 0")
	check2(check(Device{Maps: []string{"/data/local/tmp/frida-agent.so"}}, "native_frida_maps"), "maps 里有 frida-agent")
	check2(check(Device{FridaPortOpen: true}, "native_frida_port"), "27047 端口开放")
	check2(check(Device{JailbreakFiles: []string{"/bin/bash"}}, "native_jailbreak_files"), "越狱特征文件")
	check2(check(Device{Hooked: []string{"SecItemCopyMatching"}}, "hook_secitem_copy_matching"), "SecItemCopyMatching 被 hook")
	check2(check(Device{Hooked: []string{"CCCrypt"}}, "hook_cccrypt"), "CCCrypt 被 hook")
	check2(check(Device{TracerPID: 99}, "syscall_ptrace_traceme"), "已被附加 → TRACEME 失败")
	check2(check(Device{ForkedPID: 4321}, "syscall_fork_denied"), "fork 成功")
	check2(!check(Device{ForkedPID: -1}, "syscall_fork_denied"), "fork 失败 → 沙箱内")

	hostile := Device{TracerPID: 7, DebuggerAttached: true, SuBinary: true,
		Maps: []string{"frida-agent.so"}, FridaPortOpen: true}

	// 组合策略
	names := []string{"debug_is_debugger_connected", "native_tracer_pid", "syscall_ptrace_traceme"}
	detAny := Detector{Names: names, Mode: "any"}
	detAll := Detector{Names: names, Mode: "all"}
	check2(detAny.verdict(clean) == false, "干净设备上 any 模式不报")
	check2(detAny.verdict(Device{TracerPID: 7}), "any 模式一条即报")
	check2(detAll.verdict(Device{TracerPID: 7}) == false, "all 模式只响一条不报")
	check2(detAll.verdict(Device{TracerPID: 7, DebuggerAttached: true}), "all 模式全响才报")
	check2(len(detAny.layers()) == 3, "三条检查覆盖三个层")

	// 同一根因派生的多条检查不是独立信号
	check2(len(detAny.fired(Device{TracerPID: 7})) == 2, "一个根因点亮两条检查")

	// 绕过：只 hook Java 层不够
	b1 := Bypass{Det: detAny}
	b1.patchLayer(layerJava)
	check2(len(b1.remainingLayers()) == 2, "hook Java 层后还剩两层")
	check2(b1.verdict(hostile), "只 hook Java 层 → 仍然被检出")

	b2 := Bypass{Det: detAny}
	b2.patchLayer(layerJava)
	b2.patchLayer(layerNative)
	check2(len(b2.remainingLayers()) == 1, "再 hook native 层后只剩系统调用层")
	check2(b2.verdict(hostile), "系统调用层没覆盖 → 仍然被检出")

	b3 := Bypass{Det: detAny}
	for _, l := range layers {
		b3.patchLayer(l)
	}
	check2(len(b3.remainingLayers()) == 0, "三层全被覆盖")
	check2(!b3.verdict(hostile), "三层全 hook 掉 → 绕过成功")

	// 只 patch 一个点：其余点照样报（MASTG：检测手段要分散）
	bs := Bypass{Det: detAny}
	bs.patch("native_tracer_pid")
	check2(bs.verdict(hostile), "只 patch 一个点仍然被检出")

	fmt.Printf("PASS %d 项断言全部通过\n", passed)
}

// anti_debug.go — 反调试 / Root / 越狱检测的分层模型(与 anti_debug.py 同构)。
// 判据取自 OWASP MASTG: MASTG-TEST-0046 的三层 API 与绕过手段、
// MASTG-TEST-0354 的敏感 API 清单、MASTG-TEST-0240/0241 的越狱检测形态。
package main

import (
	"fmt"
	"sort"
)

// 三个 API 层(MASTG-TEST-0046 有效性判据原文: Java、native library
// functions、assembler/system calls)
const (
	layerJava    = "java"
	layerNative  = "native"
	layerSyscall = "syscall"
)

var layers = []string{layerJava, layerNative, layerSyscall}

// 检查名 -> 所在层
var checkLayer = map[string]string{
	"debug_is_debugger_connected":  layerJava,
	"app_info_flag_debuggable":     layerJava,
	"root_su_binary":               layerJava,
	"root_package_present":         layerJava,
	"jailbreak_third_party_store":  layerJava,
	"hook_secitem_copy_matching":   layerJava,
	"native_tracer_pid":            layerNative,
	"native_frida_maps":            layerNative,
	"native_frida_port":            layerNative,
	"native_jailbreak_files":       layerNative,
	"hook_cccrypt":                 layerNative,
	"syscall_ptrace_traceme":       layerSyscall,
	"syscall_fork_denied":          layerSyscall,
}

// MASTG-TEST-0354 列出的敏感 API
var sensitiveHookTargets = []string{
	"SecItemCopyMatching", "SecItemAdd", "SecItemUpdate",
	"SecKeyCreateSignature", "SecKeyCreateDecryptedData", "CCCrypt",
	"LAContext.evaluatePolicy", "LAContext.evaluateAccessControl",
	"URLSession.dataTask", "URLSession.uploadTask",
	"URLSession.downloadTask", "URLSessionTask.resume",
}

const fridaDefaultPort = 27047

// Device: 被检测的环境
type Device struct {
	TracerPID         int
	Debuggable        bool
	DebuggerAttached  bool
	SuBinary          bool
	RootPackages      []string
	FridaPortOpen     bool
	Maps              []string
	JailbreakFiles    []string
	ThirdPartyStores  []string
	Hooked            []string
	ForkedPID         int // -1 表示 fork 失败(iOS 沙箱内常见)
}

func contains(hay []string, needle string) bool {
	for _, h := range hay {
		if h == needle {
			return true
		}
	}
	return false
}

// check: 单个检查的实现, 返回 true 表示"检出异常"
func check(d Device, name string) bool {
	switch name {
	case "debug_is_debugger_connected":
		return d.DebuggerAttached
	case "app_info_flag_debuggable":
		return d.Debuggable
	case "root_su_binary":
		return d.SuBinary
	case "root_package_present":
		return len(d.RootPackages) > 0
	case "jailbreak_third_party_store":
		return len(d.ThirdPartyStores) > 0
	case "hook_secitem_copy_matching":
		return contains(d.Hooked, "SecItemCopyMatching")
	case "native_tracer_pid":
		return d.TracerPID != 0
	case "native_frida_maps":
		for _, m := range d.Maps {
			if len(m) >= 5 && (m[:5] == "frida" || m[:6] == "gadget") {
				return true
			}
		}
		return false
	case "native_frida_port":
		return d.FridaPortOpen
	case "native_jailbreak_files":
		return len(d.JailbreakFiles) > 0
	case "hook_cccrypt":
		return contains(d.Hooked, "CCCrypt")
	case "syscall_ptrace_traceme":
		// 已被附加时再附加会失败
		return d.TracerPID != 0 || d.DebuggerAttached
	case "syscall_fork_denied":
		return d.ForkedPID > 0
	}
	panic("未知检查 " + name)
}

// Detector: 一组检查 + 组合策略(any / all / threshold)
type Detector struct {
	Names     []string
	Mode      string
	Threshold int
}

func (dt Detector) signals(d Device) map[string]bool {
	out := map[string]bool{}
	for _, n := range dt.Names {
		out[n] = check(d, n)
	}
	return out
}

func (dt Detector) fired(d Device) []string {
	out := []string{}
	for _, n := range dt.Names {
		if check(d, n) {
			out = append(out, n)
		}
	}
	return out
}

func (dt Detector) verdict(d Device) bool {
	hit := 0
	for _, v := range dt.signals(d) {
		if v {
			hit++
		}
	}
	switch dt.Mode {
	case "any":
		return hit > 0
	case "all":
		return hit == len(dt.Names)
	default:
		return hit >= dt.Threshold
	}
}

func (dt Detector) layers() []string {
	seen := map[string]bool{}
	out := []string{}
	for _, n := range dt.Names {
		l := checkLayer[n]
		if !seen[l] {
			seen[l] = true
			out = append(out, l)
		}
	}
	sort.Strings(out)
	return out
}

// Bypass: 攻击者侧 —— patch/hook 与改环境
type Bypass struct {
	Det       Detector
	Patched   map[string]bool
	Overrides map[string]interface{}
}

func (b *Bypass) patch(name string) {
	if b.Patched == nil {
		b.Patched = map[string]bool{}
	}
	b.Patched[name] = true
}

func (b *Bypass) patchLayer(layer string) {
	for n, l := range checkLayer {
		if l == layer {
			b.patch(n)
		}
	}
}

// remainingLayers: 还没被覆盖掉的层 —— 决定绕过是否成功
func (b *Bypass) remainingLayers() []string {
	seen := map[string]bool{}
	out := []string{}
	for _, n := range b.Det.Names {
		if b.Patched[n] {
			continue
		}
		l := checkLayer[n]
		if !seen[l] {
			seen[l] = true
			out = append(out, l)
		}
	}
	sort.Strings(out)
	return out
}

func (b *Bypass) signals(d Device) map[string]bool {
	out := map[string]bool{}
	for _, n := range b.Det.Names {
		if b.Patched[n] {
			out[n] = false
		} else {
			out[n] = check(d, n)
		}
	}
	return out
}

func (b *Bypass) verdict(d Device) bool {
	hit := 0
	for _, v := range b.signals(d) {
		if v {
			hit++
		}
	}
	switch b.Det.Mode {
	case "any":
		return hit > 0
	case "all":
		return hit == len(b.Det.Names)
	default:
		return hit >= b.Det.Threshold
	}
}

func main() { selfcheck() }

// Package tauriacl 复刻 tauri-apps/tauri 的 IPC 权限判定：
//   crates/tauri-utils/src/acl/resolved.rs  —— 命令键归一化
//   crates/tauri-utils/src/acl/mod.rs       —— ExecutionContext / RemoteUrlPattern
//   crates/tauri/src/ipc/authority.rs       —— resolve_access（deny 优先）
// 无本机 Go 工具链，仅人工审查 + 括号配平校验。
package tauriacl

import "strings"

// AppACLKey 见 tauri-utils/src/acl/mod.rs:50。
const AppACLKey = "__app-acl__"

// CommandKey 对应 resolved.rs 里的三类写法。
func CommandKey(pluginKey, command string) string {
	switch {
	case pluginKey == AppACLKey:
		return command
	case strings.HasPrefix(pluginKey, "core:"):
		return "plugin:" + strings.TrimPrefix(pluginKey, "core:") + "|" + command
	default:
		return "plugin:" + pluginKey + "|" + command
	}
}

// RemoteUrlPattern 简化实现，规则取自 acl/mod.rs 的三个单测。
type RemoteUrlPattern struct {
	Scheme string
	Host   string
	Path   string // 空串表示模式里没写路径
}

// Parse 解析形如 scheme://host/path 的模式。
func Parse(pattern string) RemoteUrlPattern {
	p := RemoteUrlPattern{}
	parts := strings.SplitN(pattern, "://", 2)
	p.Scheme = parts[0]
	rest := parts[1]
	if i := strings.Index(rest, "/"); i >= 0 {
		p.Host = rest[:i]
		p.Path = "/" + rest[i+1:]
	} else {
		p.Host = rest
	}
	return p
}

// Test 按 scheme / host / path 三段判定。
func (p RemoteUrlPattern) Test(url string) bool {
	parts := strings.SplitN(url, "://", 2)
	if len(parts) != 2 {
		return false
	}
	uScheme, rest := parts[0], parts[1]
	uPath := "/"
	uHost := rest
	if i := strings.Index(rest, "/"); i >= 0 {
		uHost = rest[:i]
		uPath = "/" + rest[i+1:]
	}
	if p.Scheme != "*" && p.Scheme != uScheme {
		return false
	}
	switch {
	case p.Host == "*":
	case strings.HasPrefix(p.Host, "*."):
		suffix := p.Host[1:] // ".tauri.app"
		if !strings.HasSuffix(uHost, suffix) || uHost == strings.TrimPrefix(suffix, ".") {
			return false
		}
	default:
		if p.Host != uHost {
			return false
		}
	}
	if p.Path == "" {
		return true // 模式没写路径 -> 任意路径
	}
	if strings.HasSuffix(p.Path, "/*") {
		return strings.HasPrefix(uPath, strings.TrimSuffix(p.Path, "*"))
	}
	return uPath == p.Path
}

// ExecutionContext 是能力声明里的执行上下文。
type ExecutionContext struct {
	Local   bool
	Pattern RemoteUrlPattern
}

// Origin 是这次 IPC 调用的来源。
type Origin struct {
	Local bool
	URL   string
}

// Matches 对应 authority.rs:58。
func (o Origin) Matches(c ExecutionContext) bool {
	if o.Local {
		return c.Local
	}
	if c.Local {
		return false
	}
	return c.Pattern.Test(o.URL)
}

// ResolvedCommand 对应 tauri-utils::acl::resolved::ResolvedCommand。
type ResolvedCommand struct {
	Command    string
	Capability string
	Permission string
	Context    ExecutionContext
	Windows    []string
	Webviews   []string
}

// RuntimeAuthority 对应 RuntimeAuthority 的两张表。
type RuntimeAuthority struct {
	Allowed map[string][]ResolvedCommand
	Denied  map[string][]ResolvedCommand
}

// NewRuntimeAuthority 构造空表。
func NewRuntimeAuthority() *RuntimeAuthority {
	return &RuntimeAuthority{Allowed: map[string][]ResolvedCommand{},
		Denied: map[string][]ResolvedCommand{}}
}

// Allow 登记一条允许规则。
func (a *RuntimeAuthority) Allow(rc ResolvedCommand) {
	a.Allowed[rc.Command] = append(a.Allowed[rc.Command], rc)
}

// Deny 登记一条拒绝规则。
func (a *RuntimeAuthority) Deny(rc ResolvedCommand) {
	a.Denied[rc.Command] = append(a.Denied[rc.Command], rc)
}

func anyLabel(labels []string, target string) bool {
	for _, l := range labels {
		if l == target {
			return true
		}
	}
	return false
}

// ResolveAccess 对应 authority.rs:439：deny 只看 origin，allow 还要看 window/webview。
func (a *RuntimeAuthority) ResolveAccess(command, window, webview string, origin Origin) []ResolvedCommand {
	if denied, ok := a.Denied[command]; ok {
		for _, rc := range denied {
			if origin.Matches(rc.Context) {
				return nil
			}
		}
	}
	var out []ResolvedCommand
	for _, rc := range a.Allowed[command] {
		if !origin.Matches(rc.Context) {
			continue
		}
		if anyLabel(rc.Webviews, webview) || anyLabel(rc.Windows, window) {
			out = append(out, rc)
		}
	}
	if len(out) == 0 {
		return nil
	}
	return out
}

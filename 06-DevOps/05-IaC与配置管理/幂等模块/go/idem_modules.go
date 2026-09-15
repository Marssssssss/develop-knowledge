// Ansible 幂等模块最小 Go 镜像
//
// 来源:Ansible 官方 docs.ansible.com/ansible/devel/playbooks_intro.html
//  "Modules that behave this way are 'idempotent'."
//  "Repeating the task does not change the final state"
//
// Go 版实现 file/package/service 三个最小幂等模块,验证连跑 N 次 changed 数下降

package main

import "fmt"

// -----------------------------------------------------------------------------
// Host — 模拟文件系统 + 包 + 服务
// -----------------------------------------------------------------------------

type FileInfo struct {
	Mode    uint32
	Owner   string
	Group   string
	Content string
}

type ServiceState struct {
	Running bool
	Enabled bool
}

type Host struct {
	Files    map[string]*FileInfo
	Packages map[string]bool
	Services map[string]*ServiceState
}

func NewHost() *Host {
	return &Host{
		Files: map[string]*FileInfo{
			"/etc/nginx/nginx.conf": {Mode: 0644, Owner: "root", Group: "root", Content: "user www-data;\n"},
			"/var/log/app.log":      {Mode: 0600, Owner: "app", Group: "app", Content: ""},
		},
		Packages: map[string]bool{"nginx": true, "curl": true, "vim": true},
		Services: map[string]*ServiceState{
			"nginx": {Running: true, Enabled: true},
			"sshd":  {Running: true, Enabled: true},
			"redis": {Running: false, Enabled: false},
		},
	}
}

// -----------------------------------------------------------------------------
// Module result — 对应 Ansible result dict
// -----------------------------------------------------------------------------

type ModuleResult struct {
	Changed bool                   `json:"changed"`
	Failed  bool                   `json:"failed"`
	Msg     string                 `json:"msg"`
	Diff    map[string]interface{} `json:"diff"`
}

// -----------------------------------------------------------------------------
// Module: file
// -----------------------------------------------------------------------------

func ModuleFile(h *Host, params map[string]interface{}, checkMode bool) ModuleResult {
	path := params["path"].(string)
	state, _ := params["state"].(string) // file/absent
	desiredMode, _ := params["mode"].(uint32)
	desiredOwner, _ := params["owner"].(string)
	desiredGroup, _ := params["group"].(string)
	desiredContent, _ := params["_content"].(string)

	result := ModuleResult{Diff: map[string]interface{}{}}

	if state == "absent" {
		if _, exists := h.Files[path]; exists {
			result.Changed = true
			result.Diff = map[string]interface{}{"before": "exists", "after": nil}
			if !checkMode {
				delete(h.Files, path)
			}
			result.Msg = "file removed"
		} else {
			result.Msg = "file already absent"
		}
		return result
	}

	cur, exists := h.Files[path]
	if !exists {
		// new file
		newF := &FileInfo{Mode: desiredMode, Owner: desiredOwner, Group: desiredGroup, Content: desiredContent}
		result.Changed = true
		result.Diff = map[string]interface{}{"before": nil, "after": newF}
		if !checkMode {
			h.Files[path] = newF
		}
		result.Msg = "file created"
		return result
	}

	// existing: per-field diff
	diffs := map[string]interface{}{}
	if cur.Mode != desiredMode && desiredMode != 0 {
		diffs["mode"] = map[string]interface{}{"before": cur.Mode, "after": desiredMode}
	}
	if cur.Owner != desiredOwner && desiredOwner != "" {
		diffs["owner"] = map[string]interface{}{"before": cur.Owner, "after": desiredOwner}
	}
	if cur.Group != desiredGroup && desiredGroup != "" {
		diffs["group"] = map[string]interface{}{"before": cur.Group, "after": desiredGroup}
	}
	if cur.Content != desiredContent && desiredContent != "" {
		diffs["content"] = map[string]interface{}{"before": cur.Content, "after": desiredContent}
	}
	if len(diffs) > 0 {
		result.Changed = true
		result.Diff = map[string]interface{}{"before": diffKeys(diffs, "before"), "after": diffKeys(diffs, "after")}
		if !checkMode {
			for k, v := range diffs {
				m := v.(map[string]interface{})
				switch k {
				case "mode":
					cur.Mode = m["after"].(uint32)
				case "owner":
					cur.Owner = m["after"].(string)
				case "group":
					cur.Group = m["after"].(string)
				case "content":
					cur.Content = m["after"].(string)
				}
			}
		}
		result.Msg = "file updated"
	} else {
		result.Msg = "file already in desired state"
	}
	return result
}

// diffKeys: 把 "diff.before.X" 提取为一个新 map
func diffKeys(d map[string]interface{}, which string) map[string]interface{} {
	out := map[string]interface{}{}
	for k, v := range d {
		if m, ok := v.(map[string]interface{}); ok { out[k] = m[which] }
	}
	return out
}

// -----------------------------------------------------------------------------
// Module: package
// -----------------------------------------------------------------------------

func ModulePackage(h *Host, params map[string]interface{}, _ bool) ModuleResult {
	name := params["name"].(string)
	state, _ := params["state"].(string)
	result := ModuleResult{Diff: map[string]interface{}{}}
	installed, exists := h.Packages[name]
	if state == "present" {
		if exists && installed {
			result.Msg = fmt.Sprintf("package %s already installed", name)
		} else {
			result.Changed = true
			result.Diff = map[string]interface{}{"before": "absent", "after": "present"}
			h.Packages[name] = true
			result.Msg = fmt.Sprintf("installed package %s", name)
		}
	} else if state == "absent" {
		if !exists || !installed {
			result.Msg = fmt.Sprintf("package %s already absent", name)
		} else {
			result.Changed = true
			result.Diff = map[string]interface{}{"before": "present", "after": "absent"}
			delete(h.Packages, name)
			result.Msg = fmt.Sprintf("removed package %s", name)
		}
	}
	return result
}

// -----------------------------------------------------------------------------
// Module: service
// -----------------------------------------------------------------------------

func ModuleService(h *Host, params map[string]interface{}, checkMode bool) ModuleResult {
	name := params["name"].(string)
	desiredState, _ := params["state"].(string) // started/stopped
	desiredEnabled, hasEnabled := params["enabled"].(bool)

	result := ModuleResult{Diff: map[string]interface{}{}}
	cur, exists := h.Services[name]
	if !exists {
		result.Failed = true
		result.Msg = fmt.Sprintf("service %s does not exist", name)
		return result
	}
	diffs := map[string]interface{}{}
	desiredRunning := desiredState == "started" || desiredState == "running"
	if cur.Running != desiredRunning {
		diffs["state"] = map[string]interface{}{"before": cur.Running, "after": desiredRunning}
	}
	if hasEnabled && cur.Enabled != desiredEnabled {
		diffs["enabled"] = map[string]interface{}{"before": cur.Enabled, "after": desiredEnabled}
	}
	if len(diffs) > 0 {
		result.Changed = true
		result.Diff = map[string]interface{}{"before": diffKeys(diffs, "before"), "after": diffKeys(diffs, "after")}
		if !checkMode {
			for k, v := range diffs {
				m := v.(map[string]interface{})
				switch k {
				case "state":
					cur.Running = m["after"].(bool)
				case "enabled":
					cur.Enabled = m["after"].(bool)
				}
			}
		}
		result.Msg = fmt.Sprintf("service %s updated", name)
	} else {
		result.Msg = fmt.Sprintf("service %s already in desired state", name)
	}
	return result
}

// -----------------------------------------------------------------------------
// Run a playbook once
// -----------------------------------------------------------------------------

type ModuleCall struct {
	Label  string
	Result ModuleResult
}

func RunPlaybook(h *Host) []ModuleCall {
	calls := []ModuleCall{}
	calls = append(calls, ModuleCall{"file: nginx.conf mode=0o640",
		ModuleFile(h, map[string]interface{}{
			"path": "/etc/nginx/nginx.conf", "mode": uint32(0640),
			"owner": "root", "group": "root"}, false)})
	calls = append(calls, ModuleCall{"file: /tmp/x.txt new content='hello'",
		ModuleFile(h, map[string]interface{}{
			"path": "/tmp/x.txt", "mode": uint32(0755),
			"owner": "root", "group": "root",
			"_content": "hello"}, false)})
	calls = append(calls, ModuleCall{"file: /etc/missing.conf absent",
		ModuleFile(h, map[string]interface{}{
			"path": "/etc/missing.conf", "state": "absent"}, false)})
	calls = append(calls, ModuleCall{"package: jq installed",
		ModulePackage(h, map[string]interface{}{
			"name": "jq", "state": "present"}, false)})
	calls = append(calls, ModuleCall{"service: redis started+enabled",
		ModuleService(h, map[string]interface{}{
			"name": "redis", "state": "started", "enabled": true}, false)})
	calls = append(calls, ModuleCall{"service: nginx started (already)",
		ModuleService(h, map[string]interface{}{
			"name": "nginx", "state": "started", "enabled": true}, false)})
	return calls
}

func printPlaybook(label string, calls []ModuleCall) {
	fmt.Printf("--- %s ---\n", label)
	changed := 0
	for _, c := range calls {
		flag := "ok"
		if c.Result.Changed {
			flag = "CHANGED"
			changed++
		}
		fmt.Printf("  [%7s] %-50s  msg=%q\n", flag, c.Label, c.Result.Msg)
	}
	fmt.Printf("  → changed = %d/%d\n\n", changed, len(calls))
}

func main() {
	fmt.Println("=== Ansible 幂等模块 demo (Go 版) ===\n")
	h := NewHost()
	printPlaybook("第 1 次执行:期望全部触发", RunPlaybook(h))
	r2 := RunPlaybook(h)
	printPlaybook("第 2 次执行:期望 0 changed", r2)
	r3 := RunPlaybook(h)
	printPlaybook("第 3 次执行:继续幂等", r3)

	c2, c3 := 0, 0
	for _, c := range r2 { if c.Result.Changed { c2++ } }
	for _, c := range r3 { if c.Result.Changed { c3++ } }
	fmt.Println("--- 结论 ---")
	fmt.Printf("  2nd pass changed = %d (期望 0)\n", c2)
	fmt.Printf("  3rd pass changed = %d (期望 0)\n", c3)
	if c2 == 0 && c3 == 0 { fmt.Println("  ✓ idempotency 验证通过") }
}

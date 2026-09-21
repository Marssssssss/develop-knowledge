package main

import (
	"fmt"

	"tauriacl"
)

func main() {
	fmt.Println("== 命令键归一化 ==")
	fmt.Println("  app   :", tauriacl.CommandKey(tauriacl.AppACLKey, "do_thing"))
	fmt.Println("  core  :", tauriacl.CommandKey("core:event", "emit"))
	fmt.Println("  plugin:", tauriacl.CommandKey("fs", "read_file"))

	fmt.Println("== URL 模式 ==")
	p := tauriacl.Parse("http://*.tauri.app")
	fmt.Printf("  http://*.tauri.app: tauri.app=%v api.tauri.app=%v\n",
		p.Test("http://tauri.app/path"), p.Test("http://api.tauri.app/path"))
	p2 := tauriacl.Parse("*://localhost")
	fmt.Printf("  *://localhost: custom=%v\n", p2.Test("custom://localhost/path"))

	fmt.Println("== resolve_access ==")
	auth := tauriacl.NewRuntimeAuthority()
	auth.Allow(tauriacl.ResolvedCommand{
		Command:    tauriacl.CommandKey("fs", "read_file"),
		Capability: "desktop", Permission: "fs:default",
		Context: tauriacl.ExecutionContext{Local: true},
		Windows:  []string{"main"},
	})
	local := tauriacl.Origin{Local: true}
	fmt.Println("  窗口 main    :", auth.ResolveAccess("plugin:fs|read_file", "main", "main", local))
	fmt.Println("  窗口 settings:", auth.ResolveAccess("plugin:fs|read_file", "settings", "settings", local))

	auth.Deny(tauriacl.ResolvedCommand{
		Command: "plugin:fs|read_file", Capability: "locked", Permission: "fs:deny",
		Context: tauriacl.ExecutionContext{Local: true},
	})
	fmt.Println("  deny 之后    :", auth.ResolveAccess("plugin:fs|read_file", "main", "main", local))
}

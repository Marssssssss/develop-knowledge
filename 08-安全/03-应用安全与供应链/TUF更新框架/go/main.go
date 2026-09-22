// TUF 演示：把四类攻击塞进工作流，看各自在哪一步被拦下。
package main

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
)

const (
	now    = int64(1700000000)
	future = now + 86400
	past   = now - 1
)

func makeRoot(version int, expires int64) *Metadata {
	return &Metadata{
		Role: rootFile, Version: version, Expires: expires,
		Roles: map[string]*Role{
			rootFile:      {Keyids: []string{"r1", "r2"}, Threshold: 2},
			timestampFile: {Keyids: []string{"t1"}, Threshold: 1},
			snapshotFile:  {Keyids: []string{"s1"}, Threshold: 1},
			targetsFile:   {Keyids: []string{"g1"}, Threshold: 1},
		},
	}
}

func sign(md *Metadata, keyids ...string) *Metadata {
	md.Signatures = keyids
	return md
}

func run(title string, fn func() (bool, string)) {
	oked, why := fn()
	flag := "PASS"
	if !oked {
		flag = "FAIL"
	}
	fmt.Printf("  [%s] %-40s %s\n", flag, title, why)
}

func demo() {
	fmt.Println("TUF 客户端工作流：每一步挡一类攻击")

	fmt.Println()
	fmt.Println("1) root 更新")
	c := NewClient(makeRoot(1, future), now)
	run("版本 +1 且新旧 root 双阈值签名",
		func() (bool, string) { return c.UpdateRoot(sign(makeRoot(2, future), "r1", "r2"), true) })
	c = NewClient(makeRoot(1, future), now)
	run("版本跳到 3（回滚）",
		func() (bool, string) { return c.UpdateRoot(sign(makeRoot(3, future), "r1", "r2"), true) })
	c = NewClient(makeRoot(1, future), now)
	run("只用新钥匙签名（任意软件攻击）",
		func() (bool, string) { return c.UpdateRoot(sign(makeRoot(2, future), "r9"), true) })
	c = NewClient(makeRoot(1, future), now)
	run("同一把钥匙签两次凑阈值 2",
		func() (bool, string) { return c.UpdateRoot(sign(makeRoot(2, future), "r1", "r1"), true) })
	c = NewClient(makeRoot(1, future), now)
	run("root 已过期（冻结）",
		func() (bool, string) { return c.UpdateRoot(sign(makeRoot(2, past), "r1", "r2"), true) })

	fmt.Println()
	fmt.Println("2) timestamp 更新")
	c = NewClient(makeRoot(1, future), now)
	c.UpdateTimestamp(sign(&Metadata{Role: timestampFile, Version: 3, Expires: future}, "t1"))
	run("版本 4 递增", func() (bool, string) {
		return c.UpdateTimestamp(sign(&Metadata{Role: timestampFile, Version: 4, Expires: future}, "t1"))
	})
	same := NewClient(makeRoot(1, future), now)
	same.UpdateTimestamp(sign(&Metadata{Role: timestampFile, Version: 3, Expires: future}, "t1"))
	run("版本仍是 3（正常中止）", func() (bool, string) {
		return same.UpdateTimestamp(sign(&Metadata{Role: timestampFile, Version: 3, Expires: future}, "t1"))
	})

	fmt.Println()
	fmt.Println("3) snapshot 更新（timestamp 登记 version=4 + sha256）")
	body := []byte(`{"signed":"snapshot v4"}`)
	sum := sha256.Sum256(body)
	digest := hex.EncodeToString(sum[:])
	newTs := func() *Metadata {
		return &Metadata{Role: timestampFile, Version: 1, Expires: future,
			Meta: map[string]MetaRef{snapshotFile: {Version: 4,
				Hashes: map[string]string{"sha256": digest}}}}
	}
	c = NewClient(makeRoot(1, future), now)
	c.UpdateTimestamp(sign(newTs(), "t1"))
	run("内容与哈希一致", func() (bool, string) {
		return c.UpdateSnapshot(sign(&Metadata{Role: snapshotFile, Version: 4, Expires: future}, "s1"), body)
	})
	c = NewClient(makeRoot(1, future), now)
	c.UpdateTimestamp(sign(newTs(), "t1"))
	run("内容被换（混合搭配）", func() (bool, string) {
		return c.UpdateSnapshot(sign(&Metadata{Role: snapshotFile, Version: 4, Expires: future}, "s1"), []byte("evil"))
	})

	fmt.Println()
	fmt.Println("4) 委派搜索")
	md := func(name string, targets []string, delegations []Delegation) *Metadata {
		m := &Metadata{Role: name, Version: 1, Expires: future, Targets: map[string]bool{}}
		for _, t := range targets {
			m.Targets[t] = true
		}
		m.Delegations = delegations
		return m
	}
	graph := map[string]*Metadata{
		targetsFile: md(targetsFile, nil, []Delegation{
			{Name: "A", Paths: []string{"a/*"}},
			{Name: "B", Paths: []string{"*"}},
		}),
		"A": md("A", []string{"a/1"}, nil),
		"B": md("B", []string{"a/2", "b/1"}, nil),
	}
	for _, p := range []string{"a/1", "a/2", "b/1", "c/1"} {
		found := SearchTarget(graph, targetsFile, p, 32)
		if found == "" {
			found = "<nil>"
		}
		fmt.Printf("  %-6s -> %s\n", p, found)
	}
	term := map[string]*Metadata{
		targetsFile: md(targetsFile, nil, []Delegation{
			{Name: "A", Paths: []string{"*"}, Terminating: true},
			{Name: "B", Paths: []string{"*"}},
		}),
		"A": md("A", nil, nil),
		"B": md("B", []string{"x"}, nil),
	}
	fmt.Printf("  terminating 委派挡住后面的 B: %q\n", SearchTarget(term, targetsFile, "x", 32))

	fmt.Println()
	fmt.Println("5) consistent snapshot 命名")
	fmt.Printf("  root v42   -> %s\n", ConsistentMetadataName("root.json", 42))
	fmt.Printf("  foo.tar.gz -> %v\n", ConsistentTargetNames("foo.tar.gz", []string{"ab", "cd"}))
}

func main() {
	demo()
}

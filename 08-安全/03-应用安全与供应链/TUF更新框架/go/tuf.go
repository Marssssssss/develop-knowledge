// TUF（The Update Framework）客户端工作流模型。
//
// 转写 / 对照 theupdateframework/specification 的 tuf-spec.md：
//   5.3 详细客户端工作流（更新 root / timestamp / snapshot / targets）
//   5.6.7 委派的先序深度优先搜索
//   6 Consistent snapshots 的文件命名
// 模型口径：签名不做密码学验算，只统计「有效签名的 keyid」——
// 规范反复强调每个 KEYID 只能贡献一次，同一把钥匙签两遍不算两个。
package main

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
)

const (
	rootFile      = "root.json"
	snapshotFile  = "snapshot.json"
	timestampFile = "timestamp.json"
	targetsFile   = "targets.json"

	rollback      = "rollback attack"
	freeze        = "freeze attack"
	mixAndMatch   = "mix-and-match attack"
	arbitrary     = "arbitrary software attack"
	normalAbort   = "normal (same version)"
	versionMismatch = "version mismatch with timestamp"
)

// Role 是 root.json 里声明的一个角色：一组 keyid + 阈值
type Role struct {
	Keyids    []string
	Threshold int
}

// MetaRef 是 snapshot / timestamp 里对下游元数据的引用
type MetaRef struct {
	Version int
	Hashes  map[string]string
}

// Delegation 是 targets.json 里的一条委派
type Delegation struct {
	Name             string
	Paths            []string
	PathHashPrefixes []string
	Terminating      bool
}

// Metadata 是一份已下载、待校验的元数据
type Metadata struct {
	Role       string
	Version    int
	Expires    int64
	Signatures []string
	Meta       map[string]MetaRef
	Targets    map[string]bool
	Delegations []Delegation
	Roles      map[string]*Role
}

func (m *Metadata) ref(name string) (MetaRef, bool) {
	r, ok := m.Meta[name]
	return r, ok
}

// UniqueValid 去重后的有效签名数
func UniqueValid(md *Metadata, role *Role) int {
	if role == nil {
		return 0
	}
	allowed := map[string]bool{}
	for _, k := range role.Keyids {
		allowed[k] = true
	}
	seen := map[string]bool{}
	for _, k := range md.Signatures {
		if allowed[k] {
			seen[k] = true
		}
	}
	return len(seen)
}

// VerifySignatures 阈值校验：去重后的 keyid 数 >= threshold
func VerifySignatures(md *Metadata, role *Role) bool {
	if role == nil {
		return false
	}
	return UniqueValid(md, role) >= role.Threshold
}

// Client 一次更新流程的状态机；FixedTime 是规范 5.1 的固定更新起始时间
type Client struct {
	Root       *Metadata
	FixedTime  int64
	Timestamp  *Metadata
	Snapshot   *Metadata
	Targets    map[string]*Metadata
}

// NewClient 构造客户端
func NewClient(root *Metadata, now int64) *Client {
	return &Client{Root: root, FixedTime: now, Targets: map[string]*Metadata{}}
}

// UpdateRoot 规范 5.3。final=false 表示链条里的中间 root：
// 5.3.6 明确说中间 root 的过期时间「还不重要」，等 5.3.10 走到末尾再统一检查。
func (c *Client) UpdateRoot(newRoot *Metadata, final bool) (bool, string) {
	oldRoles := c.Root.Roles
	newRoles := newRoot.Roles
	if !VerifySignatures(newRoot, oldRoles[rootFile]) {
		return false, arbitrary + " (旧 root 阈值不足)"
	}
	if !VerifySignatures(newRoot, newRoles[rootFile]) {
		return false, arbitrary + " (新 root 阈值不足)"
	}
	if newRoot.Version != c.Root.Version+1 {
		return false, rollback
	}
	c.Root = newRoot
	if final && newRoot.Expires <= c.FixedTime {
		return false, freeze
	}
	rotated := false
	for _, name := range []string{timestampFile, snapshotFile} {
		var before, after []string
		if oldRoles[name] != nil {
			before = oldRoles[name].Keyids
		}
		if newRoles[name] != nil {
			after = newRoles[name].Keyids
		}
		if !sameKeyids(before, after) {
			rotated = true
		}
	}
	if rotated {
		c.Timestamp = nil
		c.Snapshot = nil
	}
	return true, fmt.Sprintf("ok (rotated=%v)", rotated)
}

func sameKeyids(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	sa := map[string]int{}
	for _, k := range a {
		sa[k]++
	}
	for _, k := range b {
		sa[k]--
	}
	for _, v := range sa {
		if v != 0 {
			return false
		}
	}
	return true
}

// UpdateRootChain 规范 5.3.2-5.3.9：一路下载 N+1 直到拿不到，末尾一次性查过期
func (c *Client) UpdateRootChain(candidates []*Metadata) (bool, string) {
	last := "ok"
	for i, cand := range candidates {
		oked, why := c.UpdateRoot(cand, i == len(candidates)-1)
		if !oked {
			return false, why
		}
		last = why
	}
	return true, last
}

// UpdateTimestamp 规范 5.4
func (c *Client) UpdateTimestamp(newTs *Metadata) (bool, string) {
	role := c.Root.Roles[timestampFile]
	if !VerifySignatures(newTs, role) {
		return false, arbitrary
	}
	if trusted := c.Timestamp; trusted != nil {
		if newTs.Version < trusted.Version {
			return false, rollback
		}
		if newTs.Version == trusted.Version {
			return false, normalAbort
		}
		oldSnap, ok1 := trusted.ref(snapshotFile)
		newSnap, ok2 := newTs.ref(snapshotFile)
		if ok1 && ok2 && newSnap.Version < oldSnap.Version {
			return false, rollback + " (snapshot 版本倒退)"
		}
	}
	if newTs.Expires <= c.FixedTime {
		return false, freeze
	}
	c.Timestamp = newTs
	return true, "ok"
}

// UpdateSnapshot 规范 5.5。raw 用于比对 timestamp 登记的哈希；nil 表示调用方没提供。
func (c *Client) UpdateSnapshot(newSnap *Metadata, raw []byte) (bool, string) {
	role := c.Root.Roles[snapshotFile]
	if c.Timestamp != nil {
		if ref, ok := c.Timestamp.ref(snapshotFile); ok {
			// 先用哈希挡混合搭配攻击（验签之前，规范的刻意顺序）
			if raw != nil {
				if want, ok2 := ref.Hashes["sha256"]; ok2 {
					sum := sha256.Sum256(raw)
					if hex.EncodeToString(sum[:]) != want {
						return false, mixAndMatch
					}
				}
			}
			if newSnap.Version != ref.Version {
				return false, versionMismatch
			}
		}
	}
	if !VerifySignatures(newSnap, role) {
		return false, arbitrary
	}
	if newSnap.Expires <= c.FixedTime {
		return false, freeze
	}
	if trusted := c.Snapshot; trusted != nil {
		for name, entry := range trusted.Meta {
			got, ok := newSnap.Meta[name]
			if !ok {
				return false, rollback + " (" + name + " 被移除)"
			}
			if got.Version < entry.Version {
				return false, rollback + " (" + name + " 版本倒退)"
			}
		}
	}
	c.Snapshot = newSnap
	return true, "ok"
}

// UpdateTargets 规范 5.6
func (c *Client) UpdateTargets(name string, md *Metadata) (bool, string) {
	if !VerifySignatures(md, c.Root.Roles[targetsFile]) {
		return false, arbitrary
	}
	if md.Expires <= c.FixedTime {
		return false, freeze
	}
	c.Targets[name] = md
	return true, "ok"
}

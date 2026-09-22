// TUF 委派搜索与 consistent snapshot 命名。
//
// 规范 5.6.7：先序深度优先搜索，访问过的角色跳过（防环），
// 命中 terminating 委派就停止；规范 6：一致快照的文件命名规则。
package main

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"strings"
)

func globCover(pattern, path string) bool {
	if strings.HasSuffix(pattern, "*") {
		return strings.HasPrefix(path, strings.TrimSuffix(pattern, "*"))
	}
	if strings.HasSuffix(pattern, "/") {
		return strings.HasPrefix(path, pattern)
	}
	return pattern == path
}

func delegationCovers(d Delegation, path string) bool {
	if len(d.Paths) > 0 {
		for _, p := range d.Paths {
			if globCover(p, path) {
				return true
			}
		}
		return false
	}
	if len(d.PathHashPrefixes) > 0 {
		sum := sha256.Sum256([]byte(path))
		digest := hex.EncodeToString(sum[:])
		for _, p := range d.PathHashPrefixes {
			if strings.HasPrefix(digest, p) {
				return true
			}
		}
		return false
	}
	return true
}

// SearchTarget 规范 5.6.7 的先序深度优先搜索：
// 访问过的角色跳过（防环），命中 terminating 委派就停止，角色数超预算就放弃。
func SearchTarget(graph map[string]*Metadata, rootName, path string, maxRoles int) string {
	visited := map[string]bool{}
	budget := maxRoles
	var walk func(string) string
	walk = func(name string) string {
		if visited[name] {
			return ""
		}
		visited[name] = true
		budget--
		if budget < 0 {
			return ""
		}
		md, ok := graph[name]
		if !ok {
			return ""
		}
		if md.Targets[path] {
			return name
		}
		for _, d := range md.Delegations {
			if !delegationCovers(d, path) {
				continue
			}
			if found := walk(d.Name); found != "" {
				return found
			}
			if d.Terminating {
				return ""
			}
		}
		return ""
	}
	return walk(rootName)
}

// ConsistentMetadataName 元数据：VERSION_NUMBER.FILENAME.EXT
func ConsistentMetadataName(name string, version int) string {
	return fmt.Sprintf("%d.%s", version, name)
}

// ConsistentTargetNames 目标文件：HASH.FILENAME.EXT，每个哈希一份拷贝
func ConsistentTargetNames(name string, hashes []string) []string {
	out := make([]string, 0, len(hashes))
	for _, h := range hashes {
		out = append(out, h+"."+name)
	}
	return out
}

// TimestampName timestamp 是唯一一个不带版本前缀也要写的（客户端靠它起手）
func TimestampName() string {
	return timestampFile
}

// 制品晋升闸门与跨仓库搬运(Go 对照实现)。
//
// 晋升的本质: 把目标 channel 的 tag 指向同一个 digest。规范要求仓库逐字节保存
// manifest, 所以"digest 不变"就是"制品没被改过"的证据 —— 晋升只改引用, 不重建。
//
// 闸门全是工程策略(规范只规定 digest 与 tag 的语义):
//  1. 来源 tag 必须能解析成 SemVer(latest/prod 这类通道名不行);
//  2. allowPrerelease 决定预发布能否进该 channel;
//  3. currentVersion(来自发布记录, 不是 tag 名)必须低于待晋升版本, 否则拒绝降级;
//  4. 目标 tag 已指向同一 digest 时是幂等。
package main

import (
	"encoding/json"
	"fmt"
)

// Policy 是晋升闸门的策略输入。
type Policy struct {
	Channel         string
	AllowPrerelease bool
	AllowDowngrade  bool
	CurrentVersion  string
}

// Decision 是一次晋升判定/执行的结果。
type Decision struct {
	OK              bool
	Reason          string
	Digest          string
	Src             string
	Dst             string
	BytesUnchanged  bool
	DigestUnchanged bool
}

func normalizePolicy(p Policy) Policy {
	if p.Channel == "" {
		p.Channel = "prod"
	}
	return p
}

func parseVersionTag(tag string) (Version, bool) {
	v, err := ParseVersion(tag)
	return v, err == nil
}

// PromotionDecision 判定"某个已存在的制品能否被打上目标 channel 的 tag"。
func PromotionDecision(reg *Registry, name, srcRef, dstTag string, pol Policy) (Decision, error) {
	pol = normalizePolicy(pol)
	out := Decision{Src: srcRef, Dst: dstTag}

	digest, ok := reg.Resolve(name, srcRef)
	if !ok {
		out.Reason = fmt.Sprintf("来源 %s 在仓库 %s 中不存在 -> 404", srcRef, name)
		return out, nil
	}
	out.Digest = digest

	srcVer, ok := parseVersionTag(srcRef)
	if !ok {
		out.Reason = "来源 tag 不是语义化版本, 无法做版本闸门: " + srcRef
		return out, nil
	}
	if srcVer.IsPrerelease() && !pol.AllowPrerelease {
		out.Reason = fmt.Sprintf("channel=%s 不接受预发布版本: %s", pol.Channel, srcVer)
		return out, nil
	}

	if cur, ok := reg.Resolve(name, dstTag); ok && cur == digest {
		out.Reason = "该 digest 已经挂在 " + dstTag + " 上 -> 幂等, 无需动作"
		return out, nil
	}
	if pol.CurrentVersion != "" && !pol.AllowDowngrade {
		curVer, ok := parseVersionTag(pol.CurrentVersion)
		if !ok {
			return out, fmt.Errorf("policy.CurrentVersion 不是合法版本: %q", pol.CurrentVersion)
		}
		if curVer.Compare(srcVer) >= 0 {
			out.Reason = fmt.Sprintf("%s 通道当前版本 %s 不低于待晋升的 %s -> 拒绝降级",
				pol.Channel, pol.CurrentVersion, srcVer)
			return out, nil
		}
	}
	out.OK = true
	out.Reason = fmt.Sprintf("闸门通过: %s -> %s (digest 不变 %s)", srcVer, dstTag, short(digest))
	return out, nil
}

// Promote 执行晋升: 只移动 tag 指针, 并回报字节与摘要都未变。
func Promote(reg *Registry, name, srcRef, dstTag string, pol Policy) (Decision, error) {
	d, err := PromotionDecision(reg, name, srcRef, dstTag, pol)
	if err != nil || !d.OK {
		return d, err
	}
	repo := reg.Repos[name]
	before := repo.Manifests[d.Digest]
	if !reg.SetTag(name, dstTag, d.Digest) {
		d.OK = false
		d.Reason = "设置 tag 失败"
		return d, nil
	}
	after := repo.Manifests[d.Digest]
	d.BytesUnchanged = string(before) == string(after)
	d.DigestUnchanged = MustDigest(after) == d.Digest
	return d, nil
}

// CopyAcross 跨仓库搬运: 按 digest 复制 manifest 与 blobs, digest 保持不变。
func CopyAcross(reg *Registry, srcName, srcRef, dstName, dstTag string) (Decision, error) {
	out := Decision{Src: srcName + "/" + srcRef, Dst: dstName}
	digest, ok := reg.Resolve(srcName, srcRef)
	if !ok {
		out.Reason = "来源不存在"
		return out, nil
	}
	src := reg.Repos[srcName]
	data := src.Manifests[digest]
	parsed := map[string]interface{}{}
	if err := json.Unmarshal(data, &parsed); err != nil {
		out.Reason = "manifest 不是合法 JSON"
		return out, nil
	}
	dst, err := reg.repo(dstName, true)
	if err != nil {
		return out, err
	}
	for _, d := range referencedDigests(parsed) {
		blob, ok := reg.Blobs[d]
		if !ok {
			out.Reason = "缺少 blob " + d
			return out, nil
		}
		dst.Blobs[d] = true
		if _, ok := reg.Blobs[d]; !ok {
			reg.Blobs[d] = blob
		}
	}
	cp := make([]byte, len(data))
	copy(cp, data)
	dst.Manifests[digest] = cp
	if dstTag != "" {
		dst.Tags[dstTag] = digest
	}
	out.OK = true
	out.Digest = digest
	out.Reason = "digest 不变 = 同一制品(内容寻址)"
	return out, nil
}

func short(digest string) string {
	if len(digest) > 19 {
		return digest[:19]
	}
	return digest
}

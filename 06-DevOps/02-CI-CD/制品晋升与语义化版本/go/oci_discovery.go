// 内容发现与 referrers(Go 对照实现)。仓库模型与 digest 计算在 oci_registry.go。
//
// 权威依据: OCI Distribution Specification(spec.md)的 Content Discovery 章节 ——
// tags 必须按 ASCIIbetical 序返回、n/last 分页与 Link: rel="next"、n=0 返回空列表且
// 不带 Link;referrers API 支持的仓库不得返回 404, 不支持时客户端回退 referrers tag。
package main

import (
	"encoding/json"
	"fmt"
	"sort"
)

// ListOptions 是 tags/list 的分页参数(Go 没有可选参数, 用结构体表达)。
type ListOptions struct {
	N     int
	Paged bool
	Last  string
}

// TagsList 对应 GET /v2/<name>/tags/list: tags 必须按 ASCIIbetical 序返回。
func (r *Registry) TagsList(name string, opts ListOptions) Result {
	repo, err := r.repo(name, false)
	if err != nil {
		return errResult(400, "NAME_INVALID")
	}
	if repo == nil {
		return Result{Status: 404, Headers: map[string]string{}}
	}
	tags := []string{}
	for t := range repo.Tags {
		tags = append(tags, t)
	}
	sort.Strings(tags)
	if opts.Last != "" {
		kept := []string{}
		for _, t := range tags {
			if t > opts.Last {
				kept = append(kept, t)
			}
		}
		tags = kept
	}
	headers := map[string]string{}
	if opts.Paged {
		if opts.N == 0 {
			return Result{Status: 200, Headers: headers,
				Raw: mustJSON(map[string]interface{}{"name": name, "tags": []string{}})}
		}
		if len(tags) > opts.N {
			headers["Link"] = fmt.Sprintf("</v2/%s/tags/list?n=%d&last=%s>; rel=\"next\"",
				name, opts.N, tags[opts.N-1])
			tags = tags[:opts.N]
		}
	}
	return Result{Status: 200, Headers: headers,
		Raw: mustJSON(map[string]interface{}{"name": name, "tags": tags})}
}

// Referrers 对应 GET /v2/<name>/referrers/<digest>;不支持时返回 404,
// tagFallback 为 true 时改为回退到 Referrers Tag 方案(客户端行为)。
func (r *Registry) Referrers(name, subject string, tagFallback bool) Result {
	repo, err := r.repo(name, false)
	if err != nil {
		return errResult(400, "NAME_INVALID")
	}
	if repo == nil {
		return Result{Status: 404, Headers: map[string]string{}}
	}
	if ValidateDigest(subject) == "invalid" {
		return errResult(400, "DIGEST_INVALID")
	}
	if !r.SupportsReferrers {
		if !tagFallback {
			return Result{Status: 404, Headers: map[string]string{}}
		}
		got := r.Get(name, ReferrersTag(subject))
		if got.Status != 200 {
			return Result{Status: got.Status, Headers: map[string]string{}}
		}
		return Result{Status: 200, Headers: map[string]string{"Content-Type": indexMediaType},
			Raw: got.Raw}
	}
	descriptors := []map[string]interface{}{}
	for digest, data := range repo.Manifests {
		parsed := map[string]interface{}{}
		if err := json.Unmarshal(data, &parsed); err != nil {
			continue
		}
		if subjectOf(parsed) != subject {
			continue
		}
		desc := map[string]interface{}{
			"mediaType": mediaTypeOf(parsed, manifestMediaType),
			"digest":    digest, "size": len(data)}
		if at := artifactTypeOf(parsed); at != "" {
			desc["artifactType"] = at
		}
		if ann, ok := parsed["annotations"].(map[string]interface{}); ok {
			desc["annotations"] = ann
		}
		descriptors = append(descriptors, desc)
	}
	sort.Slice(descriptors, func(i, j int) bool {
		return descriptors[i]["digest"].(string) < descriptors[j]["digest"].(string)
	})
	return Result{Status: 200, Headers: map[string]string{"Content-Type": indexMediaType},
		Raw: mustJSON(map[string]interface{}{"schemaVersion": 2,
			"mediaType": indexMediaType, "manifests": descriptors})}
}

// referencedDigests 只取 config 与 layers 的 digest;subject 不算(规范允许先推 referrer)。
func referencedDigests(parsed map[string]interface{}) []string {
	out := []string{}
	if cfg, ok := parsed["config"].(map[string]interface{}); ok {
		if d, ok := cfg["digest"].(string); ok && d != "" {
			out = append(out, d)
		}
	}
	if layers, ok := parsed["layers"].([]interface{}); ok {
		for _, l := range layers {
			if lm, ok := l.(map[string]interface{}); ok {
				if d, ok := lm["digest"].(string); ok && d != "" {
					out = append(out, d)
				}
			}
		}
	}
	return out
}

func subjectOf(parsed map[string]interface{}) string {
	if s, ok := parsed["subject"].(map[string]interface{}); ok {
		if d, ok := s["digest"].(string); ok {
			return d
		}
	}
	return ""
}

func mediaTypeOf(parsed map[string]interface{}, fallback string) string {
	if m, ok := parsed["mediaType"].(string); ok && m != "" {
		return m
	}
	return fallback
}

// artifactTypeOf: 缺失时用 config 的 mediaType 兜底(规范对 manifest 的要求)。
func artifactTypeOf(parsed map[string]interface{}) string {
	if a, ok := parsed["artifactType"].(string); ok && a != "" {
		return a
	}
	if cfg, ok := parsed["config"].(map[string]interface{}); ok {
		if m, ok := cfg["mediaType"].(string); ok {
			return m
		}
	}
	return ""
}

func errResult(status int, code string) Result {
	return Result{Status: status, Headers: map[string]string{"error": code}}
}

func mustJSON(v interface{}) []byte {
	b, err := json.Marshal(v)
	if err != nil {
		panic(err)
	}
	return b
}

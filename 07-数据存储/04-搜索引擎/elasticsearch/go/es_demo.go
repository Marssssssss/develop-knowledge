// Elasticsearch 倒排索引与近实时搜索（NRT）演示。
//
// 只用 Go 标准库（net/http + encoding/json）演示 Elasticsearch 的核心机制，
// 与 python/es_demo.py 执行完全相同的 7 步流程：
//  1. 删除旧索引（幂等复跑）
//  2. 创建索引 + 显式 mapping（title: text, tag: keyword）
//  3. 索引 3 篇文档
//  4. 立即搜索 -> 0 命中（NRT：写入默认 1 秒后才可搜索）
//  5. 手动 refresh -> 立即可搜索
//  6. 再次搜索 -> 命中；term 查询 keyword 字段做对照
//  7. _analyze 观察分词器产生的 token（即倒排表中的 term）
//
// 前置条件：本机 9200 端口有一个未开启安全认证的 Elasticsearch。
package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"
)

const (
	base  = "http://localhost:9200"
	index = "demo-es"
)

type doc struct {
	ID    string `json:"id"`
	Title string `json:"title"`
	Tag   string `json:"tag"`
}

var docs = []doc{
	{ID: "1", Title: "Elasticsearch makes text searchable", Tag: "search"},
	{ID: "2", Title: "The quick brown fox", Tag: "story"},
	{ID: "3", Title: "Quick search with the inverted index", Tag: "search"},
}

// esRequest 执行一次 REST 请求。404 时返回 {"status":404}，其余非 2xx 视为错误。
func esRequest(method, path string, body any) map[string]any {
	var reader io.Reader
	if body != nil {
		raw, err := json.Marshal(body)
		if err != nil {
			panic(err)
		}
		reader = bytes.NewReader(raw)
	}
	req, err := http.NewRequest(method, base+path, reader)
	if err != nil {
		panic(err)
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := httpClient.Do(req)
	if err != nil {
		panic(err)
	}
	defer resp.Body.Close()

	raw, _ := io.ReadAll(resp.Body)
	var out map[string]any
	if err := json.Unmarshal(raw, &out); err != nil {
		panic(fmt.Sprintf("%s %s: 非 JSON 响应: %s", method, path, raw))
	}
	if resp.StatusCode == 404 {
		out["status"] = 404.0
		return out
	}
	if resp.StatusCode >= 300 {
		panic(fmt.Sprintf("HTTP %d on %s %s: %s", resp.StatusCode, method, path, raw))
	}
	return out
}

// searchHits 从 _search 响应中取出命中数与文档标题列表。
func searchHits(resp map[string]any) (float64, []string) {
	hits, _ := resp["hits"].(map[string]any)
	if hits == nil {
		return 0, nil
	}
	total, _, _ := num(hits["total"])
	var titles []string
	if arr, ok := hits["hits"].([]any); ok {
		for _, item := range arr {
			h, _ := item.(map[string]any)
			src, _ := h["_source"].(map[string]any)
			title, _ := src["title"].(string)
			titles = append(titles, title)
		}
	}
	return total, titles
}

// num 从 any（可能是 float64 或 map[string]any{"value": n}）里取数值。
func num(v any) (float64, bool) {
	switch t := v.(type) {
	case float64:
		return t, true
	case map[string]any:
		if f, ok := t["value"].(float64); ok {
			return f, true
		}
	}
	return 0, false
}

func main() {
	// 1. 删除旧索引（忽略 404，便于反复运行）
	esRequest(http.MethodDelete, "/"+index, nil)
	fmt.Printf("[1] 已清理旧索引 %s\n", index)

	// 2. 创建索引 + 显式 mapping：title 分词进倒排索引，tag 整串精确匹配
	esRequest(http.MethodPut, "/"+index, map[string]any{
		"mappings": map[string]any{
			"properties": map[string]any{
				"title": map[string]any{"type": "text"},
				"tag":   map[string]any{"type": "keyword"},
			},
		},
	})
	fmt.Printf("[2] 已创建索引 %s（title: text / tag: keyword）\n", index)

	// 3. 索引 3 篇文档
	client := &http.Client{Timeout: 10 * time.Second}
	_ = client
	for _, d := range docs {
		esRequest(http.MethodPut, fmt.Sprintf("/%s/_doc/%s", index, d.ID),
			map[string]any{"title": d.Title, "tag": d.Tag})
	}
	fmt.Printf("[3] 已索引 %d 篇文档\n", len(docs))

	// 4. 立即搜索 —— 预期 0 命中（NRT：默认 1 秒 refresh 间隔）
	before := esRequest(http.MethodGet, fmt.Sprintf("/%s/_search", index),
		map[string]any{"query": map[string]any{"match": map[string]any{"title": "search"}}})
	n, _ := searchHits(before)
	fmt.Printf("[4] refresh 前搜索 \"search\": %.0f 命中（写入还在内存缓冲区，不可见）\n", n)

	// 5. 手动 refresh：把缓冲区写成新 segment 并打开
	esRequest(http.MethodPost, fmt.Sprintf("/%s/_refresh", index), nil)
	fmt.Println("[5] 已手动 refresh（内存缓冲区 -> 新 segment，可搜索）")

	// 6. 再次搜索 —— match 会对查询文本先分词
	after := esRequest(http.MethodGet, fmt.Sprintf("/%s/_search", index),
		map[string]any{"query": map[string]any{"match": map[string]any{"title": "search"}}})
	n2, titles := searchHits(after)
	fmt.Printf("[6] refresh 后搜索 \"search\": %.0f 命中 -> %v\n", n2, titles)

	// 6b. 对照：term 查询 keyword 字段（整串精确匹配）
	byTag := esRequest(http.MethodGet, fmt.Sprintf("/%s/_search", index),
		map[string]any{"query": map[string]any{"term": map[string]any{"tag": "search"}}})
	n3, titles2 := searchHits(byTag)
	fmt.Printf("    term 精确匹配 tag=\"search\": %.0f 命中 -> %v\n", n3, titles2)

	// 7. _analyze：观察标准分词器产生的 token（它们就是倒排表里的 term）
	analyzed := esRequest(http.MethodGet, fmt.Sprintf("/%s/_analyze", index),
		map[string]any{"field": "title", "text": "Elasticsearch Makes Text Searchable"})
	var terms []string
	if toks, ok := analyzed["tokens"].([]any); ok {
		for _, t := range toks {
			m, _ := t.(map[string]any)
			s, _ := m["token"].(string)
			terms = append(terms, s)
		}
	}
	fmt.Printf("[7] _analyze 分词结果: %v（注意全部小写化、逐词切分）\n", terms)
}

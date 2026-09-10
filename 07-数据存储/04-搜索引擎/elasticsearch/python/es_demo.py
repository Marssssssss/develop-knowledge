#!/usr/bin/env python3
"""Elasticsearch 倒排索引与近实时搜索（NRT）演示。

只用 Python 标准库（urllib + json）演示 Elasticsearch 的核心机制：
  1. 删除旧索引（幂等复跑）
  2. 创建索引 + 显式 mapping（title: text, tag: keyword）
  3. 索引 3 篇文档
  4. 立即搜索 -> 0 命中（NRT：写入默认 1 秒后才可搜索）
  5. 手动 refresh -> 立即可搜索
  6. 再次搜索 -> 命中，match 查询会对查询文本先分词
  7. _analyze 观察分词器产生的 token（即倒排表中的 term）

前置条件：本机 9200 端口有一个未开启安全认证的 Elasticsearch，例如
  docker run -d --name es -p 9200:9200 \
    -e discovery.type=single-node -e xpack.security.enabled=false \
    docker.elastic.co/elasticsearch/elasticsearch:8.15.0
"""

import json
import sys
import urllib.error
import urllib.request

BASE = "http://localhost:9200"
INDEX = "demo-es"

DOCS = [
    {"id": "1", "title": "Elasticsearch makes text searchable", "tag": "search"},
    {"id": "2", "title": "The quick brown fox", "tag": "story"},
    {"id": "3", "title": "Quick search with the inverted index", "tag": "search"},
]


def http(method, path, body=None):
    """执行一次 REST 请求，返回解析后的 JSON。404 时返回 {"status": 404}。"""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        if err.code == 404:
            return {"status": 404}
        print(f"HTTP {err.code} on {method} {path}: {err.read().decode()}", file=sys.stderr)
        raise


def hits_of(resp):
    """从 _search 响应中取出 (命中数, [文档标题列表])。"""
    hits = resp.get("hits", {})
    return hits.get("total", {}).get("value", 0), [
        h["_source"].get("title", "") for h in hits.get("hits", [])
    ]


def main():
    # 1. 删除旧索引（忽略 404，便于反复运行）
    http("DELETE", f"/{INDEX}")
    print(f"[1] 已清理旧索引 {INDEX}")

    # 2. 创建索引 + 显式 mapping：
    #    - title: text  -> 分词后进倒排索引，支持全文检索
    #    - tag:   keyword -> 整串作为一个 term，精确匹配/聚合
    http("PUT", f"/{INDEX}", {"mappings": {"properties": {
        "title": {"type": "text"},
        "tag": {"type": "keyword"},
    }}})
    print(f"[2] 已创建索引 {INDEX}（title: text / tag: keyword）")

    # 3. 索引 3 篇文档（PUT _doc/<id> 显式指定 _id）
    for doc in DOCS:
        http("PUT", f"/{INDEX}/_doc/{doc['id']}",
             {"title": doc["title"], "tag": doc["tag"]})
    print(f"[3] 已索引 {len(DOCS)} 篇文档")

    # 4. 立即搜索 —— 预期 0 命中（NRT：默认 1 秒 refresh 间隔）
    before = http("GET", f"/{INDEX}/_search",
                  {"query": {"match": {"title": "search"}}})
    n, _ = hits_of(before)
    print(f"[4] refresh 前搜索 \"search\": {n} 命中（写入还在内存缓冲区，不可见）")

    # 5. 手动 refresh：把缓冲区写成新 segment 并打开（生产环境不要频繁这么做）
    http("POST", f"/{INDEX}/_refresh")
    print("[5] 已手动 refresh（内存缓冲区 -> 新 segment，可搜索）")

    # 6. 再次搜索 —— match 会对查询文本先分词，"search" -> term "search"
    after = http("GET", f"/{INDEX}/_search",
                 {"query": {"match": {"title": "search"}}})
    n, titles = hits_of(after)
    print(f"[6] refresh 后搜索 \"search\": {n} 命中 -> {titles}")

    # 6b. 对照：term 查询 keyword 字段（整串精确匹配）
    by_tag = http("GET", f"/{INDEX}/_search",
                  {"query": {"term": {"tag": "search"}}})
    n2, titles2 = hits_of(by_tag)
    print(f"    term 精确匹配 tag=\"search\": {n2} 命中 -> {titles2}")

    # 7. _analyze：观察标准分词器产生的 token（它们就是倒排表里的 term）
    tokens = http("GET", f"/{INDEX}/_analyze",
                  {"field": "title",
                   "text": "Elasticsearch Makes Text Searchable"})
    terms = [t["token"] for t in tokens.get("tokens", [])]
    print(f"[7] _analyze 分词结果: {terms}（注意全部小写化、逐词切分）")


if __name__ == "__main__":
    main()

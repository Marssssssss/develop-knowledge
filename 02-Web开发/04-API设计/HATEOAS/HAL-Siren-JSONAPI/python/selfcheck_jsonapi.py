"""606 自检：JSON:API v1.1 文档结构与查询参数族。"""

from harness import check, expect_errors

from jsonapi import (
    is_legal_member_name,
    media_type_params,
    negotiate_content_type,
    validate_document,
)
from jsonapi_query import (
    apply_fields,
    apply_sort,
    check_query_params,
    family_of,
    include_paths,
    is_valid_family_name,
    pagination_links,
    parse_fields,
    parse_include,
    parse_sort,
    validate_query_param,
)

DOC = {
    "data": {
        "type": "articles",
        "id": "1",
        "attributes": {"title": "JSON:API paints my bikeshed!"},
        "relationships": {
            "author": {"links": {"self": "/articles/1/relationships/author",
                                 "related": "/articles/1/author"},
                       "data": {"type": "people", "id": "9"}},
            "comments": {"data": [{"type": "comments", "id": "5"},
                                  {"type": "comments", "id": "12"}]},
        },
    },
    "included": [
        {"type": "people", "id": "9", "attributes": {"name": "Dan"}},
        {"type": "comments", "id": "5", "attributes": {"body": "first"}},
        {"type": "comments", "id": "12", "attributes": {"body": "second"}},
    ],
}

check(validate_document(DOC) == [], "JSON:API 规范复合文档应合法")
check(validate_document({"data": None}) == [], "JSON:API 单资源 primary data 可为 null")
check(validate_document({"data": []}) == [], "JSON:API 集合为空时是 []")
check(validate_document({"meta": {"a": 1}}) == [], "JSON:API 只有 meta 也算合法")
expect_errors(validate_document({}), ["MUST 至少含 data / errors / meta"], label="JSON:API 空文档")
expect_errors(validate_document({"data": None, "errors": [{"status": "500"}]}),
              ["data 与 errors MUST NOT 共存"], label="JSON:API data/errors 互斥")
expect_errors(validate_document({"errors": [{"status": "500"}], "included": []}),
              ["included MUST NOT 出现"], label="JSON:API 无 data 不得有 included")
expect_errors(validate_document({"data": {"type": "a"}}), ["缺 id"], label="JSON:API 缺 id")
expect_errors(validate_document({"data": {"id": "1"}}), ["缺 REQUIRED 的 type"],
              label="JSON:API 缺 type")
expect_errors(validate_document({"data": {"type": "a", "id": "1",
                                          "attributes": {"x": 1},
                                          "relationships": {"x": {"data": None}}}}),
              ["同时是 attribute 与 relationship"], label="JSON:API 字段命名空间")
expect_errors(validate_document({"data": {"type": "a", "id": "1",
                                          "attributes": {"id": "x"}}}),
              ["字段不得名为"], label="JSON:API 字段不得叫 id")
expect_errors(validate_document({"data": {"type": "a", "id": "1",
                                          "relationships": {"r": {}}}}),
              ["MUST 至少含 links / data / meta 之一"], label="JSON:API relationship object")
expect_errors(validate_document({"data": {"type": "a", "id": "1",
                                          "relationships": {"r": {"data": 3}}}}),
              ["linkage 形状非法"], label="JSON:API linkage 形状")
expect_errors(validate_document({"errors": [{}]}), ["MUST 至少含一个成员"],
              label="JSON:API error object")
expect_errors(validate_document({"errors": [{"status": 500}]}),
              ["status: MUST 是字符串"], label="JSON:API status 是字符串")
expect_errors(validate_document({"errors": [{"detail": "x", "source": {}}]}),
              ["SHOULD 含 pointer/parameter/header"], label="JSON:API source 成员")

check(validate_document({"data": {"type": "a", "lid": "tmp-1"}}) == [],
      "JSON:API 客户端新建资源用 lid 代替 id")
expect_errors(validate_document({"data": {"type": "a", "id": 1}}),
              ["id: MUST 是字符串"], label="JSON:API id 必须是字符串")
expect_errors(validate_document({"data": {"type": "a.b", "id": "1"}}),
              ["值 MUST 满足成员名约束"], label="JSON:API type 值满足成员名约束")

check(media_type_params("application/vnd.api+json;ext=\"https://x\";profile=\"https://y\"")[1]
      == ["ext", "profile"], "JSON:API 参数名解析")
check(negotiate_content_type("application/vnd.api+json") == ("ok", 200),
      "JSON:API 裸媒体类型 OK")
check(negotiate_content_type("application/vnd.api+json;ext=\"https://jsonapi.org/ext/atomic\"")
      == ("ok", 200), "JSON:API ext 参数允许")
check(negotiate_content_type("application/vnd.api+json;profile=\"https://p\"") == ("ok", 200),
      "JSON:API profile 参数允许")
check(negotiate_content_type("application/vnd.api+json; charset=\"utf-8\"")
      == ("unsupported-param", 415), "JSON:API 带其他参数 → 415")
check(negotiate_content_type("application/json") == ("not-jsonapi", 200),
      "JSON:API 非本媒体类型不由本节处理")

check(is_legal_member_name("first-name"), "JSON:API 中间连字符合法")
check(is_legal_member_name("first_name"), "JSON:API 中间下划线合法")
check(is_legal_member_name("first name"), "JSON:API 中间空格合法（不推荐）")
check(not is_legal_member_name("-first"), "JSON:API 连字符不得开头")
check(not is_legal_member_name("first-"), "JSON:API 连字符不得结尾")
check(not is_legal_member_name("first.name"), "JSON:API 点号是保留字符")
check(not is_legal_member_name("first+name"), "JSON:API 加号是保留字符")
check(not is_legal_member_name("a[b]"), "JSON:API 方括号是保留字符")
check(not is_legal_member_name(""), "JSON:API 空成员名非法")
check(is_legal_member_name("@member"), "JSON:API @ 可作首字符")
check(not is_legal_member_name("a@b"), "JSON:API @ 不得出现在非首位")
check(is_legal_member_name("类型"), "JSON:API U+0080 以上允许")

check(parse_include("") == [], "JSON:API 空 include 表示不返回相关资源")
check(include_paths("comments.author,ratings") == [["comments", "author"], ["ratings"]],
      "JSON:API include 逗号+点双层分隔")
check(include_paths("comments") == [["comments"]], "JSON:API 单路径")

FS = parse_fields({"fields[articles]": "title,body", "fields[people]": "name"})
check(FS == {"articles": ["title", "body"], "people": ["name"]}, "JSON:API fields[TYPE] 解析")
check(parse_fields({"fields[articles]": ""}) == {"articles": []}, "JSON:API 空字段集")
PRUNED = apply_fields(DOC["data"], {"articles": ["title"]})
check("relationships" not in PRUNED, "JSON:API 未请求的关系被裁掉")
check(PRUNED["attributes"] == {"title": "JSON:API paints my bikeshed!"}, "JSON:API 保留请求字段")
check(apply_fields(DOC["data"], {"other": []})["id"] == "1",
      "JSON:API 未指定该 type 时原样返回")
check(apply_fields(DOC["data"], {"articles": []}) == {"type": "articles", "id": "1"},
      "JSON:API 空字段集只留 type/id")

check(parse_sort("age") == [("age", False)], "JSON:API sort 默认升序")
check(parse_sort("-created,title") == [("created", True), ("title", False)],
      "JSON:API 减号前缀为降序且按序")
ROWS = [{"type": "o", "id": "1", "attributes": {"total": 30, "status": "b"}},
        {"type": "o", "id": "2", "attributes": {"total": 30, "status": "a"}},
        {"type": "o", "id": "3", "attributes": {"total": 10, "status": "c"}}]
GETF = lambda r, f: r["attributes"][f]  # noqa: E731
check([r["id"] for r in apply_sort(ROWS, "-total,status", GETF)] == ["2", "1", "3"],
      "JSON:API 先按 total 降序再按 status 升序")
check([r["id"] for r in apply_sort(ROWS, "total", GETF)] == ["3", "1", "2"],
      "JSON:API 升序时同值保持原相对顺序（稳定排序）")

check(pagination_links({"first": "/a", "next": "/b", "self": "/c"})
      == {"first": "/a", "next": "/b"}, "JSON:API 只保留四个分页键")

check(family_of("filter[author.status]") == ("filter", ["author.status"]),
      "JSON:API 参数族基名与方括号内容")
check(family_of("filter[x][y]") == ("filter", ["x", "y"]), "JSON:API 嵌套方括号")
check(is_valid_family_name("filter"), "JSON:API 裸基名合法")
check(is_valid_family_name("filter[x]"), "JSON:API 单级方括号合法")
check(is_valid_family_name("filter[author.status]"), "JSON:API 点分隔列表合法")
check(not is_valid_family_name("filter[_]"), "JSON:API filter[_] 非法（_ 不是合法成员名）")

check(validate_query_param("myParam"), "JSON:API 含大写即合法自定义参数")
check(not validate_query_param("myparam"), "JSON:API 纯 a-z 参数名被规范保留")
check(validate_query_param("my-param"), "JSON:API 含连字符且首尾合法即通过")
check(check_query_params(["include", "sort", "fields[articles]", "page[number]",
                          "filter[x]", "myParam"]) == [], "JSON:API 合法参数全部放行")
check(check_query_params(["myparam"]) == ["myparam"], "JSON:API 纯 a-z 自定义参数 → 400")
check(check_query_params(["include", "myparam"]) == ["myparam"],
      "JSON:API 非法参数隔离出来，不影响规范参数")

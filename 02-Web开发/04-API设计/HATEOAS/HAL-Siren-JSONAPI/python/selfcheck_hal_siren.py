"""606 自检：HAL（draft-kelly-json-hal-08）与 Siren。"""

from harness import check, expect_errors

from hal import (
    as_link_list,
    curies,
    embedded_for,
    expand_rel,
    expand_template,
    is_templated,
    is_uri_template,
    link_by_name,
    links_for,
    state_properties,
    traverse,
    validate_resource,
)
from siren import (
    action_by_name,
    action_request,
    is_embedded_link,
    partition_entities,
    validate_entity,
)

ORDER = {
    "_links": {
        "self": {"href": "/orders/523"},
        "warehouse": {"href": "/warehouse/56"},
        "invoice": {"href": "/invoices/873"},
    },
    "currency": "USD",
    "status": "shipped",
    "total": 10.20,
}

check(validate_resource(ORDER) == [], "HAL §3 §6 示例文档应合法")
check(sorted(state_properties(ORDER)) == ["currency", "status", "total"],
      "HAL §4 除 _links/_embedded 外皆为状态")
check(validate_resource({"_links": {"x": 1}}) != [], "HAL _links 值非 Link Object 应报错")
expect_errors(validate_resource({"_links": {"x": {}}}), ["href"], label="HAL §5.1 href REQUIRED")
expect_errors(validate_resource({"_links": {"x": {"href": "/a{?b}", "templated": "yes"}}}),
              ["templated"], label="HAL §5.2 templated 必须是布尔")
expect_errors(validate_resource({"_links": {"x": {"href": "/orders{?id}"}}}),
              ["URI Template"], label="HAL §5.2 模板却未标 templated")
check(validate_resource({"_links": {"x": {"href": "/orders{?id}", "templated": True}}}) == [],
      "HAL §5.2 标了 templated 的模板链接合法")

check(is_uri_template("/orders{?id}") and not is_uri_template("/orders/1"),
      "HAL 模板识别只认花括号")
check(is_templated({"href": "/orders{?id}", "templated": True}), "HAL templated true")
check(not is_templated({"href": "/orders{?id}"}), "HAL templated 缺省视为 false")
check(not is_templated({"href": "/orders{?id}", "templated": "true"}),
      "HAL templated 非 true 一律视为 false")
check(expand_template("/orders{?id}", {"id": 42}) == "/orders?id=42", "HAL {?id} 展开")
check(expand_template("/orders{?id}", {}) == "/orders", "HAL {?id} 无变量时省去")

check(len(as_link_list({"href": "/a"})) == 1, "HAL 单对象归一")
check(len(as_link_list([{"href": "/a"}, {"href": "/b"}])) == 2, "HAL 数组归一")
check(as_link_list("nope") == [], "HAL 非法形状归一为空")

CURIE_DOC = {
    "_links": {
        "self": {"href": "/orders"},
        "curies": [{"name": "acme",
                    "href": "http://docs.acme.com/relations/{rel}",
                    "templated": True}],
        "acme:widgets": {"href": "/widgets"},
    }
}
check(curies(CURIE_DOC) == {"acme": "http://docs.acme.com/relations/{rel}"},
      "HAL §8.2 curies 建表")
check(expand_rel(CURIE_DOC, "acme:widgets") == "http://docs.acme.com/relations/widgets",
      "HAL §8.2 CURIE 展开")
check(expand_rel(CURIE_DOC, "self") == "self", "HAL 未带前缀的关系名原样返回")
check(expand_rel(CURIE_DOC, "zz:widgets") == "zz:widgets", "HAL 未声明前缀不展开")
FULL = "http://docs.acme.com/relations/widgets"
check([l["href"] for l in links_for(CURIE_DOC, FULL)] == ["/widgets"],
      "HAL 用全 URI 也能取到 CURIE 链接")
check([l["href"] for l in links_for(CURIE_DOC, "acme:widgets")] == ["/widgets"],
      "HAL 用短名也能取到")

NAMED = {"_links": {"search": [{"href": "/s?q=a", "name": "alpha"},
                               {"href": "/s?q=b", "name": "beta"}]}}
check(link_by_name(NAMED, "search", "beta")["href"] == "/s?q=b", "HAL §5.5 name 作次级键")
check(link_by_name(NAMED, "search", "gamma") is None, "HAL name 未命中返回 None")

BEFORE = {"_links": {"self": {"href": "/blog-post"},
                     "author": {"href": "/people/alan-watts"}}}
AFTER = {
    "_links": {"self": {"href": "/blog-post"},
               "author": {"href": "/people/alan-watts"}},
    "_embedded": {"author": {"_links": {"self": {"href": "/people/alan-watts"}},
                             "name": "Alan Watts"}},
}
SEEN = []


def fake_fetch(href):
    SEEN.append(href)
    return {"fetched": href}


check(traverse(BEFORE, "author")[0] == "link", "HAL §8.3 无嵌入时走链接")
check(traverse(BEFORE, "author", fetch=fake_fetch) == ("link", {"fetched": "/people/alan-watts"}, 1),
      "HAL §8.3 走链接记 1 次请求")
check(SEEN == ["/people/alan-watts"], "HAL §8.3 fetch 只被调一次")
check(traverse(AFTER, "author", fetch=fake_fetch) == ("embedded", embedded_for(AFTER, "author"), 0),
      "HAL §8.3 有嵌入时零请求")
check(embedded_for(AFTER, "author")["name"] == "Alan Watts", "HAL §8.3 嵌入资源可读")
check(traverse(BEFORE, "missing") == ("none", None, 0), "HAL 关系不存在返回 none")
check(validate_resource({"_embedded": {"author": {"name": "x"}}}) == [],
      "HAL 嵌入资源本身也要是合法 Resource Object")
check(validate_resource({"_embedded": {"author": 3}}) != [],
      "HAL _embedded 值非资源应报错")
check(validate_resource("not-an-object") != [], "HAL 根非对象应报错")

SIREN_ORDER = {
    "class": ["order"],
    "properties": {"orderNumber": 42, "itemCount": 3, "status": "pending"},
    "entities": [
        {"class": ["info", "customer"], "rel": ["http://x.io/rels/customer"],
         "properties": {"customerId": "pj123", "name": "Peter Joseph"},
         "links": [{"rel": ["self"], "href": "http://api.x.io/customers/pj123"}]},
        {"class": ["items", "collection"], "rel": ["http://x.io/rels/order-items"],
         "href": "http://api.x.io/orders/42/items"},
    ],
    "actions": [
        {"name": "add-item", "title": "Add Item", "method": "POST",
         "href": "http://api.x.io/orders/42/items",
         "type": "application/x-www-form-urlencoded",
         "fields": [{"name": "orderNumber", "type": "hidden", "value": "42"},
                    {"name": "productCode", "type": "text"},
                    {"name": "quantity", "type": "number"}]},
    ],
    "links": [{"rel": ["self"], "href": "http://api.x.io/orders/42"},
              {"rel": ["previous"], "href": "http://api.x.io/orders/41"},
              {"rel": ["next"], "href": "http://api.x.io/orders/43"}],
}

check(validate_entity(SIREN_ORDER) == [], "Siren 规范示例应合法")
LINKS, REPS = partition_entities(SIREN_ORDER)
check(len(LINKS) == 1 and len(REPS) == 1, "Siren 两种子实体各一")
check(all(is_embedded_link(x) for x in LINKS), "Siren 含 href 即嵌入链接")
check(not any(is_embedded_link(x) for x in REPS), "Siren 无 href 即嵌入表示")
check(all("rel" in r for r in REPS), "Siren 嵌入表示 MUST 含 rel")

expect_errors(validate_entity({"class": "order"}), ["class: MUST 是字符串数组"],
              label="Siren class 必须是数组")
expect_errors(validate_entity({"links": [{"rel": "self", "href": "/x"}]}),
              ["rel: MUST 是非空字符串数组"], label="Siren link.rel 必须是数组")
expect_errors(validate_entity({"links": [{"rel": ["self"]}]}), ["href"],
              label="Siren link.href Required")
expect_errors(validate_entity({"entities": [{"href": "/x"}]}), ["rel"],
              label="Siren 子实体缺 rel")
expect_errors(validate_entity({"actions": [{"name": "a", "href": "/x"},
                                           {"name": "a", "href": "/y"}]}),
              ["动作名 MUST 在实体内唯一"], label="Siren 动作名唯一")
expect_errors(validate_entity({"actions": [{"name": "a", "href": "/x",
                                            "fields": [{"name": "f", "type": "datex"}]}]}),
              ["不在 HTML5 input type 集合内"], label="Siren 字段 type 取值域")
expect_errors(validate_entity({"actions": [{"href": "/x"}]}), ["name: 必须是字符串"],
              label="Siren action.name Required")
expect_errors(validate_entity({"actions": [{"name": "a", "href": "/x",
                                            "fields": [{"name": "f"}, {"name": "f"}]}]}),
              ["字段名 MUST 在动作内唯一"], label="Siren 字段名唯一")

ADD_ITEM = action_by_name(SIREN_ORDER, "add-item")
check(ADD_ITEM is not None, "Siren 按名字取动作")
METHOD, HREF, CTYPE, BODY = action_request(ADD_ITEM, {"productCode": "X-1", "quantity": 2})
check(METHOD == "POST", "Siren action.method")
check(HREF == "http://api.x.io/orders/42/items", "Siren action.href")
check(CTYPE == "application/x-www-form-urlencoded", "Siren 有 fields 且未给 type 时的默认")
check(BODY == "orderNumber=42&productCode=X-1&quantity=2",
      "Siren hidden 字段参与序列化（实得 %r）" % BODY)

IMPLICIT = {"name": "go", "href": "/go", "fields": [{"name": "q"}]}
M2, _H2, C2, B2 = action_request(IMPLICIT, {"q": "hi"})
check(M2 == "GET", "Siren method 省略按 GET")
check(C2 == "application/x-www-form-urlencoded", "Siren type 省略且有 fields 时取表单默认")
check(B2 == "q=hi", "Siren 无 value 的字段只序列化传入值")
_M3, _H3, C3, _B3 = action_request({"name": "g", "href": "/g"})
check(C3 is None, "Siren 无 fields 且未给 type 时本 demo 不臆造默认 Content-Type")

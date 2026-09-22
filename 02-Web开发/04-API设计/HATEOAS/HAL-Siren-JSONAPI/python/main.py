"""606 HATEOAS 三种表示形态对照 —— 演示入口。

同一份「订单 + 客户 + 动作」领域，分别用 HAL / Siren / JSON:API 表达，
把三种形态各自"必须靠约定才能表达出来"的那部分跑一遍。
"""

import json

from hal import (
    HAL_MEDIA_TYPE,
    curies,
    embedded_for,
    expand_rel,
    expand_template,
    is_templated,
    is_uri_template,
    links_for,
    state_properties,
    traverse,
    validate_resource,
)
from siren import (
    SIREN_MEDIA_TYPE,
    action_request,
    is_embedded_link,
    partition_entities,
    validate_entity,
)
from jsonapi import JSONAPI_MEDIA_TYPE, negotiate_content_type, validate_document
from jsonapi_query import (
    apply_fields,
    apply_sort,
    check_query_params,
    include_paths,
    is_legal_member_name,
    parse_fields,
    parse_sort,
)

ORDER_HAL = {
    "_links": {
        "self": {"href": "/orders/523"},
        "warehouse": {"href": "/warehouse/56"},
        "invoice": {"href": "/invoices/873"},
        "ea:find": {"href": "/orders{?id}", "templated": True},
        "curies": [
            {"name": "ea", "href": "http://docs.example.com/relations/{rel}",
             "templated": True}
        ],
    },
    "currency": "USD",
    "status": "shipped",
    "total": 10.20,
}

ORDER_SIREN = {
    "class": ["order"],
    "properties": {"orderNumber": 523, "status": "shipped", "total": 10.20},
    "entities": [
        {"class": ["info", "customer"],
         "rel": ["http://x.io/rels/customer"],
         "properties": {"customerId": "pj123", "name": "Peter Joseph"},
         "links": [{"rel": ["self"], "href": "http://api.x.io/customers/pj123"}]},
        {"class": ["items", "collection"],
         "rel": ["http://x.io/rels/order-items"],
         "href": "http://api.x.io/orders/523/items"},
    ],
    "actions": [
        {"name": "add-item", "title": "Add Item", "method": "POST",
         "href": "http://api.x.io/orders/523/items",
         "type": "application/x-www-form-urlencoded",
         "fields": [
             {"name": "orderNumber", "type": "hidden", "value": "523"},
             {"name": "productCode", "type": "text"},
             {"name": "quantity", "type": "number"},
         ]}
    ],
    "links": [{"rel": ["self"], "href": "http://api.x.io/orders/523"}],
}

ORDER_JSONAPI = {
    "data": {
        "type": "orders",
        "id": "523",
        "attributes": {"status": "shipped", "total": 10.20},
        "relationships": {
            "customer": {"data": {"type": "customers", "id": "pj123"}},
            "items": {"data": [{"type": "items", "id": "1"}, {"type": "items", "id": "2"}]},
        },
    },
    "included": [
        {"type": "customers", "id": "pj123", "attributes": {"name": "Peter Joseph"}},
        {"type": "items", "id": "1", "attributes": {"sku": "A"}},
        {"type": "items", "id": "2", "attributes": {"sku": "B"}},
    ],
}


def main():
    print("== HAL (%s) ==" % HAL_MEDIA_TYPE)
    print("  校验:", validate_resource(ORDER_HAL) or "OK")
    print("  状态属性:", sorted(state_properties(ORDER_HAL)))
    print("  CURIE 映射:", curies(ORDER_HAL))
    print("  ea:find 展开:", expand_rel(ORDER_HAL, "ea:find"))
    find = links_for(ORDER_HAL, "http://docs.example.com/relations/find")[0]
    print("  模板链接:", find["href"], "templated =", is_templated(find),
          "是 URI Template =", is_uri_template(find["href"]))
    print("  {?id} 展开:", expand_template(find["href"], {"id": 523}))
    print("  warehouse 走链接:", traverse(ORDER_HAL, "warehouse"))

    cached = dict(ORDER_HAL)
    cached["_embedded"] = {"warehouse": {"_links": {"self": {"href": "/warehouse/56"}},
                                         "city": "Shanghai"}}
    print("  嵌入后同 rel:", traverse(cached, "warehouse"),
          "（来源应为 embedded，请求数为 0）")
    print("  嵌入资源:", embedded_for(cached, "warehouse")["city"])

    print()
    print("== Siren (%s) ==" % SIREN_MEDIA_TYPE)
    print("  校验:", validate_entity(ORDER_SIREN) or "OK")
    links, reps = partition_entities(ORDER_SIREN)
    print("  嵌入链接:", [is_embedded_link(x) for x in links],
          "嵌入表示 rel:", [r["rel"] for r in reps])
    method, href, ctype, body = action_request(
        ORDER_SIREN["actions"][0], {"productCode": "X-1", "quantity": 2})
    print("  add-item →", method, href)
    print("    Content-Type:", ctype)
    print("    body:", body)

    print()
    print("== JSON:API (%s) ==" % JSONAPI_MEDIA_TYPE)
    print("  校验:", validate_document(ORDER_JSONAPI) or "OK")
    print("  Content-Type 协商:", negotiate_content_type("application/vnd.api+json"),
          negotiate_content_type("application/vnd.api+json; charset=utf-8"))
    print("  include=comments.author,ratings →", include_paths("comments.author,ratings"))
    fs = parse_fields({"fields[orders]": "status,total"})
    print("  fields[orders]=status,total →", fs, "裁剪后:",
          json.dumps(apply_fields(ORDER_JSONAPI["data"], fs), ensure_ascii=False))
    rows = [{"type": "orders", "id": "1", "attributes": {"total": 30, "status": "a"}},
            {"type": "orders", "id": "2", "attributes": {"total": 30, "status": "b"}},
            {"type": "orders", "id": "3", "attributes": {"total": 10, "status": "c"}}]
    ordered = apply_sort(rows, "-total,status",
                         lambda r, f: r["attributes"][f])
    print("  sort=-total,status →", [r["id"] for r in ordered])
    print("  成员名 first-name / -first / first.name:",
          [is_legal_member_name(x) for x in ("first-name", "-first", "first.name")])
    print("  非法查询参数:", check_query_params(["include", "page[number]", "filter[x]",
                                          "myParam", "myparam"]))
    print("  parse_sort('-created,title') =", parse_sort("-created,title"))


if __name__ == "__main__":
    main()

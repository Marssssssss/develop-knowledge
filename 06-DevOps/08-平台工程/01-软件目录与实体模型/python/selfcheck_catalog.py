"""自检：Backstage 软件目录实体模型（catalog.py）。

每条断言都按「误报集 + 漏报集」成对构造：既验证合法样本被接受，也验证非法样本被拒绝，
避免只写「应然 TRUTH」式的恒真断言。
"""

from catalog import (
    Entity, EntityRef, RefError, WELL_KNOWN_RELATIONS,
    check_envelope, deduce_relations, is_reserved_key,
    parse_entity_ref, valid_annotation_value, valid_key, valid_label_value,
    valid_name, valid_namespace, valid_tag,
)


def kinds_of(entities):
    counts = {}
    for e in entities:
        counts[e.kind] = counts.get(e.kind, 0) + 1
    return counts

PASS = 0
FAIL = []


def check(label, got, expect):
    global PASS
    if got == expect:
        PASS += 1
    else:
        FAIL.append(f"{label}: got {got!r}, expect {expect!r}")


def raises(label, fn, *a, **kw):
    global PASS
    try:
        fn(*a, **kw)
    except RefError:
        PASS += 1
    except Exception as e:  # 其它异常类型说明实现走错分支
        FAIL.append(f"{label}: raised {type(e).__name__} instead of RefError")
        return
    else:
        FAIL.append(f"{label}: expected RefError but no error raised")


# ------------------------------------------------------- name / namespace / tag
check("name 允许 hyphen", valid_name("artist-web"), True)
check("name 允许下划线与点", valid_name("CircleciBuildsDumpV2_avro_gcs"), True)
check("name 空串", valid_name(""), False)
check("name 63 上界", valid_name("a" * 63), True)
check("name 64 越界", valid_name("a" * 64), False)
check("name 前导分隔符", valid_name("-lead"), False)
check("name 尾随分隔符", valid_name("trail-"), False)
check("name 连续分隔符", valid_name("double--dash"), False)
check("name 混合分隔符合法", valid_name("dot.and_under"), True)
check("name 含空格", valid_name("has space"), False)

check("namespace 允许 hyphen", valid_namespace("tracking-services"), True)
check("namespace 单段", valid_namespace("payment"), True)
check("namespace 拒绝下划线", valid_namespace("under_score"), False)
check("namespace 拒绝点号", valid_namespace("dot.sep"), False)
check("namespace 尾随 hyphen", valid_namespace("trail-"), False)
check("namespace 64 越界", valid_namespace("a" * 64), False)

check("tag java", valid_tag("java"), True)
check("tag 强制小写", valid_tag("Java"), False)
check("tag 允许加号", valid_tag("c++"), True)
check("tag 允许井号", valid_tag("c#"), True)
check("tag 允许冒号", valid_tag("gpu:l4"), True)
check("tag 允许 hyphen 分隔", valid_tag("go-lang"), True)
check("tag 拒绝下划线", valid_tag("bad_tag"), False)

# -------------------------------------------------------------------- 键与值
check("label 键带域名前缀", valid_key("example.com/custom"), True)
check("backstage 保留前缀仍是合法键", valid_key("backstage.io/managed-by-location"), True)
check("前缀非小写域名", valid_key("NotDomain/custom"), False)
check("无前缀的裸名合法", valid_key("simplename"), True)
check("前缀含空格非法", valid_key("bad prefix/x"), False)
check("name 部分 64 越界", valid_key("example.com/" + "a" * 64), False)
check("保留前缀识别", is_reserved_key("backstage.io/x"), True)
check("非保留前缀", is_reserved_key("example.com/x"), False)
check("label 值走 name 规则", valid_label_value("ValueStuff"), True)
check("label 值 64 越界", valid_label_value("a" * 64), False)
check("annotation 值无长度上限", valid_annotation_value("x" * 5000), True)

# ------------------------------------------------------------------ 实体引用
def ref(ref_str, kind="Component", ns="default"):
    return parse_entity_ref(ref_str, default_kind=kind, default_namespace=ns)


check("默认 kind 生效", ref("artist-relations-team", "Group").kind, "Group")
check("默认 namespace 是 default", ref("x").namespace, "default")
check("字符串形式小写 kind", str(ref("artist-relations-team", "Group")),
      "group:default/artist-relations-team")
check("全限定引用", str(ref("group:default/dev.infra", "Group")), "group:default/dev.infra")
check("显式 kind 覆盖默认", ref("user:ghe/alice", "Group").kind, "User")
check("resource 前缀", ref("resource:default/artists-db").kind, "Resource")
check("只写 namespace", ref("default/artists-db").namespace, "default")
check("kind 大小写不敏感", ref("Group:Default/x", "Group").namespace, "default")
check("自定义默认 namespace", ref("alice", "User", "ghe").namespace, "ghe")
raises("非法 name 抛 RefError", ref, "..bad")
raises("未知 kind 抛 RefError", ref, "unknownkind:x/y")
check("api 引用", ref("api:default/a", "API").kind, "API")

# ---------------------------------------------------------------------- 关系
comp = Entity("Component", "artist-web", "default", spec={
    "owner": "artist-relations-team",
    "system": "public-websites",
    "providesApis": ["artist-api"],
    "dependsOn": ["resource:default/artists-db"],
})
rels = deduce_relations(comp)
triples = {(str(s), t, str(g)) for s, t, g in rels}
check("四条 spec 字段共 8 行关系", len(rels), 8)
check("ownedBy 正向", ("component:default/artist-web", "ownedBy",
                      "group:default/artist-relations-team") in triples, True)
check("ownerOf 反向", ("group:default/artist-relations-team", "ownerOf",
                       "component:default/artist-web") in triples, True)
check("partOf 正向", ("component:default/artist-web", "partOf",
                      "system:default/public-websites") in triples, True)
check("providesApi 正向", ("component:default/artist-web", "providesApi",
                           "api:default/artist-api") in triples, True)
check("apiProvidedBy 反向", ("api:default/artist-api", "apiProvidedBy",
                             "component:default/artist-web") in triples, True)
check("dependsOn 指向 Resource", ("component:default/artist-web", "dependsOn",
                                   "resource:default/artists-db") in triples, True)
check("dependencyOf 反向", ("resource:default/artists-db", "dependencyOf",
                            "component:default/artist-web") in triples, True)

# 反向对称性：每一条正向关系都能在集合里找到配对
missing = 0
for s, t, g in rels:
    pair = None
    for p in WELL_KNOWN_RELATIONS.values():
        if t == p.forward:
            pair, other = p, p.reverse
        elif t == p.reverse:
            pair, other = p, p.forward
    if pair is None or (str(g), other, str(s)) not in triples:
        missing += 1
check("每条关系都有配对反向", missing, 0)

# 命名空间继承：非 default 命名空间的实体，其引用默认落在同一命名空间
comp2 = Entity("Component", "svc", "ghe", spec={"owner": "team-a"})
check("引用继承实体命名空间",
      deduce_relations(comp2)[0][2].namespace, "ghe")

raises("dependsOn 不能指向 API",
       deduce_relations,
       Entity("Component", "c", "default", spec={"dependsOn": ["api:default/x"]}))
raises("providesApis 不能指向 Component",
       deduce_relations,
       Entity("Component", "c", "default", spec={"providesApis": ["component:default/x"]}))
raises("Group 不能用 spec.memberOf（memberOf 的 from 端是 User）",
       deduce_relations,
       Entity("Group", "g", "default", spec={"memberOf": ["other-group"]}))
user = Entity("User", "alice", "default", spec={"memberOf": ["dev-infra"]})
check("User 的 memberOf 生成 hasMember 反向",
      ("group:default/dev-infra", "hasMember", "user:default/alice")
      in {(str(s), t, str(g)) for s, t, g in deduce_relations(user)}, True)
dep = Entity("Component", "lookup", "default", spec={"dependencyOf": ["artist-web"]})
check("dependencyOf 字段写出反向关系",
      {(str(s), t, str(g)) for s, t, g in deduce_relations(dep)},
      {("component:default/lookup", "dependencyOf", "component:default/artist-web"),
       ("component:default/artist-web", "dependsOn", "component:default/lookup")})

# -------------------------------------------------------------------- 信封校验
good = {
    "apiVersion": "backstage.io/v1alpha1",
    "kind": "Component",
    "metadata": {
        "name": "artist-web",
        "namespace": "default",
        "tags": ["java"],
        "labels": {"example.com/custom": "custom_label_value"},
        "annotations": {"example.com/service-discovery": "artistweb"},
        "links": [{"url": "https://admin.example-org.com", "title": "Admin"}],
    },
    "spec": {"type": "website", "lifecycle": "production"},
}
check("合法信封无错误", check_envelope(good), [])
check("缺 name 报错", any("metadata.name invalid" in e for e in
      check_envelope({"apiVersion": "backstage.io/v1alpha1", "metadata": {}})), True)
check("apiVersion 错误报错", check_envelope({"apiVersion": "v1", "metadata": {"name": "a"}}),
      ["apiVersion must be backstage.io/v1alpha1"])
check("描述符里写 relations 被拒",
      any("relations" in e for e in check_envelope({**good, "relations": []})), True)
check("metadata.uid 被拒",
      any("uid" in e for e in check_envelope(
          {**good, "metadata": {**good["metadata"], "uid": "abc"}})), True)
check("非法 tag 报错",
      any("tag" in e for e in check_envelope(
          {**good, "metadata": {**good["metadata"], "tags": ["Bad_Tag"]}})), True)
check("label 值过长报错",
      any("label value" in e for e in check_envelope(
          {**good, "metadata": {**good["metadata"], "labels": {"example.com/k": "a" * 64}}})), True)
check("link 缺 url 报错",
      any("url" in e for e in check_envelope(
          {**good, "metadata": {**good["metadata"], "links": [{"title": "x"}]}})), True)

# -------------------------------------------------------------------- 统计
check("kind 统计", kinds_of([comp, comp2, user]),
      {"Component": 2, "User": 1})

if FAIL:
    print(f"FAILED {len(FAIL)} / {PASS + len(FAIL)}")
    for f in FAIL:
        print("  -", f)
    raise SystemExit(1)
print(f"catalog selfcheck: {PASS} assertions passed")

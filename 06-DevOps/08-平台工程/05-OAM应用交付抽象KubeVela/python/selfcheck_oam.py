"""自检：KubeVela OAM 应用模型与定义修订（oam.py）。成对构造误报集/漏报集。"""

from oam import (
    Application, Component, Cue, DefinitionReference, DefinitionRevision, OamError,
    Policy, Schematic, Terraform, Trait, Workflow, expose_source_values, next_revision,
    parameter_value_type, revision_hash,
)

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
    except OamError:
        PASS += 1
    except Exception as e:
        FAIL.append(f"{label}: raised {type(e).__name__} instead of OamError")
    else:
        FAIL.append(f"{label}: expected OamError")


# ------------------------------------------------------------------ 参数类型
check("布尔归类为 boolean", parameter_value_type(True), "boolean")
check("整数归类为 number", parameter_value_type(3), "number")
check("浮点归类为 number", parameter_value_type(1.5), "number")
check("字符串归类为 string", parameter_value_type("x"), "string")
raises("None 无对应类型", parameter_value_type, None)

# -------------------------------------------------------------------- 原理图
check("cue.template 必填", Cue("").validate(), ["schematic.cue.template is required"])
check("cue 合法", Cue("output: {}").validate(), [])
check("terraform 默认 hcl", Terraform("resource {}").type, "hcl")
check("terraform 非法 type", Terraform("x", "yaml").validate(),
      ["schematic.terraform.type must be one of ('hcl', 'json', 'remote')"])
check("terraform 缺 configuration", Terraform("").validate(),
      ["schematic.terraform.configuration is required"])
check("空 schematic 报错", Schematic().validate(),
      ["schematic must define at least one of cue / terraform"])
check("cue 与 terraform 都合法", Schematic(Cue("t"), Terraform("c")).validate(), [])

# -------------------------------------------------------------------- 应用
def app(**over):
    comps = over.pop("components", [Component("web", "webservice", {"image": "nginx"},
                                              traits=[Trait("scaler", {"replicas": 2}),
                                                      Trait("gateway", {"port": 80})])])
    return Application(components=comps, **over)


check("合法应用", app().validate(), [])
check("components 必填", Application([]).validate(), ["spec.components is required"])
check("组件名唯一", app(components=[Component("a", "webservice"),
                                   Component("a", "worker")]).validate(),
      ["component names must be unique"])
check("组件缺 type", app(components=[Component("a", "")]).validate(),
      ["each component requires name and type"])
check("dependsOn 指向未知组件", app(components=[
    Component("a", "webservice", depends_on=["zzz"])]).validate(),
      ["component 'a' dependsOn unknown 'zzz'"])
check("dependsOn 指向自己", app(components=[
    Component("a", "webservice", depends_on=["a"])]).validate(),
      ["component 'a' dependsOn unknown 'a'"])
check("策略缺 type", app(policies=[Policy("")]).validate(), ["each policy requires type"])

# traits 是数组：顺序被保留，且不会像 map 那样去重/乱序
manifest = app().components[0].to_manifest()
check("traits 按声明顺序保留", [t["type"] for t in manifest["traits"]],
      ["scaler", "gateway"])
check("ReplicaKey 不进清单", "replicaKey" in manifest, False)
replica = Component("web", "webservice", replica_key="p80")
check("ReplicaKey 即使赋值也不出现", "replicaKey" in replica.to_manifest(), False)
check("scope 是映射", Component("web", "webservice",
                                scopes={"health": "my-scope"}).to_manifest()["scopes"],
      {"health": "my-scope"})

# -------------------------------------------------- workflow：渲染而不下发
plain = app()
applied, revision = plain.render()
check("无 workflow 时直接下发组件", len(applied), 1)
check("无 workflow 时 revision 里也有组件", len(revision["components"]), 1)
check("无 workflow 时不写 workflow 字段", "workflow" in revision, False)
wf = app(workflow=Workflow(ref="deploy-flow", steps=[{"name": "apply"}]))
check("has_workflow 为真", wf.has_workflow, True)
applied_wf, revision_wf = wf.render()
check("有 workflow 时不下发资源", applied_wf, [])
check("有 workflow 时渲染结果进 AppRevision", revision_wf["workflow"]["ref"], "deploy-flow")
check("有 workflow 时组件仍在 revision 里", len(revision_wf["components"]), 1)

# ------------------------------------------------------------ 定义引用与修订
ref = DefinitionReference("webservice")
check("未指定 version 取第一个", ref.resolve_version(["v1", "v2"]), "v1")
check("指定 version 生效", DefinitionReference("webservice", "v2").resolve_version(["v1", "v2"]), "v2")
raises("指定不存在的 version", DefinitionReference("webservice", "v3").resolve_version, ["v1"])
raises("定义无版本", DefinitionReference("webservice").resolve_version, [])

spec_a = {"schematic": {"cue": {"template": "output: {}"}}}
hash_a = revision_hash(spec_a)
check("hash 稳定", revision_hash(dict(spec_a)), hash_a)
check("spec 变了 hash 变", revision_hash({"schematic": {"cue": {"template": "output: {x: 1}"}}}) != hash_a, True)
check("键顺序不影响 hash", revision_hash({"a": 1, "b": 2}), revision_hash({"b": 2, "a": 1}))
check("首个修订号是 1", next_revision(None, spec_a), (1, hash_a))
check("spec 未变不递增", next_revision(DefinitionRevision("webservice-v1", 1, "Component",
                                                         "componentDefinition", spec_a), spec_a),
      (1, hash_a))
check("spec 变化递增", next_revision(DefinitionRevision("webservice-v1", 1, "Component",
                                                        "componentDefinition", spec_a),
                                     {"schematic": {"cue": {"template": "output: {y: 2}"}}})[0], 2)

check("Component 修订合法", DefinitionRevision(
    "webservice-v1", 1, "Component", "componentDefinition", spec_a).validate(), [])
check("Trait 修订的快照字段", DefinitionRevision(
    "scaler-v1", 1, "Trait", "traitDefinition").validate(), [])
check("快照字段与类型不匹配", DefinitionRevision(
    "webservice-v1", 1, "Component", "traitDefinition").validate(),
      ["Component revisions must fill 'componentDefinition', got 'traitDefinition'"])
check("非法 definitionType", DefinitionRevision(
    "x-v1", 1, "Widget", "widgetDefinition").validate(),
      ["definitionType must be one of ('Component', 'Trait', 'Policy', 'WorkflowStep', 'Source')"])
check("revision 从 1 起", DefinitionRevision(
    "webservice-v0", 0, "Component", "componentDefinition").validate(),
      ["revision must be >= 1"])
check("五种定义类型齐全", len(("Component", "Trait", "Policy", "WorkflowStep", "Source")), 5)

# ------------------------------------------------------------ 源状态可见性
consumed = {"registry": "harbor.example.org", "token": "abc",
            "nested": {"token": "deep", "user": "u"}}
check("未设置即暴露", expose_source_values(None, consumed), consumed)
check("空策略也是暴露", expose_source_values({}, consumed), consumed)
check("显式 False 全打码", expose_source_values({"exposeConsumedValues": False}, consumed),
      {k: "***" for k in consumed})
check("maskPaths 顶层打码", expose_source_values({"maskPaths": ["token"]}, consumed)["token"],
      "***")
check("maskPaths 嵌套打码", expose_source_values({"maskPaths": ["nested.token"]}, consumed)["nested"],
      {"token": "***", "user": "u"})
check("maskPaths 不存在的路径无副作用",
      expose_source_values({"maskPaths": ["nope.x"]}, consumed)["registry"],
      "harbor.example.org")

if FAIL:
    print(f"FAILED {len(FAIL)} / {PASS + len(FAIL)}")
    for f in FAIL:
        print("  -", f)
    raise SystemExit(1)
print(f"oam selfcheck: {PASS} assertions passed")

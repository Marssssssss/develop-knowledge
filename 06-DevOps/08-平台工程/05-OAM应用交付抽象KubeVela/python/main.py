"""演示：一个 Application 从「组件 + 运维特征」到渲染/下发的两种命运。

运行：python python/main.py
"""

from oam import (
    Application, Component, Cue, DefinitionReference, DefinitionRevision, Policy,
    Schematic, Terraform, Trait, Workflow, expose_source_values, next_revision,
    parameter_value_type,
)


def main() -> None:
    comp = Component(
        name="web",
        type="webservice",
        properties={"image": "nginx:1.25", "port": 80},
        traits=[Trait("scaler", {"replicas": 2}), Trait("gateway", {"port": 80})],
    )
    plain = Application(components=[comp],
                        policies=[Policy("health", name="ops")])

    print("== 无 workflow：直接下发 ==")
    applied, revision = plain.render()
    print("  下发资源数：", len(applied))
    print("  组件清单  ：", applied[0])
    print("  revision 里有 workflow 吗：", "workflow" in revision)

    print()
    print("== 有 workflow：只渲染进 AppRevision ==")
    wf_app = Application(components=[comp], workflow=Workflow(ref="deploy-flow"))
    applied_wf, revision_wf = wf_app.render()
    print("  下发资源数：", len(applied_wf))
    print("  revision.workflow：", revision_wf["workflow"])

    print()
    print("== 定义修订 ==")
    spec_v1 = {"schematic": {"cue": {"template": "output: {}"}}}
    rev1_no, hash1 = next_revision(None, spec_v1)
    rev1 = DefinitionRevision("webservice-v1", rev1_no, "Component",
                              "componentDefinition", spec_v1)
    print(f"  v1: revision={rev1.revision} hash={rev1.hash} 校验={rev1.validate() or 'OK'}")
    spec_v2 = {"schematic": {"cue": {"template": "output: { replicas: parameter.replicas }"}}}
    rev2_no, hash2 = next_revision(rev1, spec_v2)
    print(f"  v2: revision={rev2_no} hash={hash2}（hash 变了才递增）")
    print("  沿用旧 spec 不递增：", next_revision(rev1, spec_v1)[0])
    print("  Trait 修订的快照字段校验：", DefinitionRevision(
        "scaler-v1", 1, "Trait", "traitDefinition").validate() or "OK")
    print("  快照字段填错的报错：", DefinitionRevision(
        "scaler-v1", 1, "Trait", "componentDefinition").validate())

    print()
    print("== 其它口径 ==")
    print("  DefinitionReference 未指定 version：",
          DefinitionReference("webservice").resolve_version(["v1", "v2", "v3"]))
    print("  schematic.terraform.type 默认：", Terraform("resource {}").type)
    print("  参数类型归类：", [parameter_value_type(v) for v in (True, 3, "x")])
    consumed = {"registry": "harbor.example.org", "token": "abc",
                "nested": {"token": "deep", "user": "u"}}
    print("  源状态（未设置即暴露）：", expose_source_values(None, consumed))
    print("  源状态（maskPaths 打码）：",
          expose_source_values({"maskPaths": ["token", "nested.token"]}, consumed))


if __name__ == "__main__":
    main()

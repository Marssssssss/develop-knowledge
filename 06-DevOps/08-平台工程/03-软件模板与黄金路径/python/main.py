"""演示：一条黄金路径模板从参数到「建仓 + 注册实体」的执行过程。

运行：python python/main.py
"""

from scaffold import (
    Action, Template, execute, parse_duration, render_nunjucks, render_outputs,
    render_typed,
)

TEMPLATE = Template(
    api_version="scaffolder.backstage.io/v1beta3",
    name="v1beta3-demo",
    spec={
        "owner": "group:default/platform",
        "type": "service",
        "parameters": [{
            "title": "Fill in some steps",
            "required": ["name"],
            "properties": {
                "name": {"type": "string"},
                "enabledDB": {"type": "boolean", "default": False},
            },
        }],
        "steps": [
            {"id": "fetchBase", "name": "Fetch Base", "action": "fetch:template",
             "input": {"url": "./template", "values": {"name": "${{ parameters.name }}"}}},
            {"id": "publish", "name": "Publish", "action": "publish:github",
             "input": {"repoUrl": "github.com?repo=${{ parameters.name }}"}},
            {"id": "register", "name": "Register", "action": "catalog:register",
             "input": {"repoContentsUrl": "${{ steps['publish'].output.repoContentsUrl }}",
                       "catalogInfoPath": "/catalog-info.yaml"}},
        ],
        "output": {
            "links": [
                {"title": "Repository",
                 "url": "${{ steps['publish'].output.remoteUrl }}"},
                {"if": "${{ parameters.enabledDB }}", "title": "CI Dashboard",
                 "url": "https://ci.example.com/${{ parameters.name }}"},
            ],
        },
    },
    annotations={"backstage.io/time-saved": "PT4H"},
)

ACTIONS = {
    "fetch:template": Action("fetch:template", lambda i: {"files": ["catalog-info.yaml"]}),
    "publish:github": Action(
        "publish:github",
        lambda i: {"remoteUrl": i["repoUrl"],
                   "repoContentsUrl": i["repoUrl"] + "/blob/main"},
        input_schema={"required": ["repoUrl"]},
    ),
    "catalog:register": Action(
        "catalog:register",
        lambda i: {"entityRef": "component:default/catalog-info"},
        input_schema={"required": ["repoContentsUrl"]},
    ),
}


def main() -> None:
    ctx, results = execute(TEMPLATE, {"name": "artist-web"}, ACTIONS)
    print("== 步骤执行 ==")
    for r in results:
        print(f"  {r.id:10} {r.status:10} output={r.output}")

    print()
    print("== 两种语法 ==")
    content = "echo 'Hi my name is {{ values.name }}'"
    print(f"  模板正文（{{{{ values.x }}}}）: {render_nunjucks(content, {'values': {'name': 'artist-web'}})}")
    print(f"  ${{{{ }}}} 保留类型: {render_typed('${{ parameters.enabledDB }}', ctx)!r}"
          f"（bool 而不是 'True'）")

    print()
    print("== 输出卡片 ==")
    for link in render_outputs(TEMPLATE, ctx)["links"]:
        print(f"  {link['title']:14} {link['url']}")

    saved = TEMPLATE.annotations["backstage.io/time-saved"]
    print()
    print(f"== 该模板节省时间 {saved} = {parse_duration(saved) / 3600:.0f} 小时 ==")


if __name__ == "__main__":
    main()

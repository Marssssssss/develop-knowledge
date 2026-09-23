"""自检：Backstage Scaffolder 软件模板（scaffold.py）。成对构造误报集/漏报集。"""

from scaffold import (
    Action, StepResult, Template, TemplateError, execute, generate_example_output,
    lookup, parse_duration, render_input, render_nunjucks, render_outputs,
    render_typed, validate_input, validate_template,
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
    except TemplateError:
        PASS += 1
    except Exception as e:
        FAIL.append(f"{label}: raised {type(e).__name__} instead of TemplateError")
    else:
        FAIL.append(f"{label}: expected TemplateError")


# ------------------------------------------------------------------ 上下文取值
ctx = {
    "parameters": {"name": "artist-web", "enabledDB": True, "replicas": 3},
    "steps": {"publish": {"output": {"remoteUrl": "https://github.com/x/y",
                                     "repoContentsUrl": "https://github.com/x/y/blob/main"}}},
    "values": {"name": "artist-web"},
}
check("点号路径", lookup(ctx, "parameters.name"), "artist-web")
check("下标写法与点号等价",
      lookup(ctx, "steps['publish'].output.remoteUrl"),
      lookup(ctx, "steps.publish.output.remoteUrl"))
raises("未定义变量报错", lookup, ctx, "parameters.nope")
raises("对标量继续下钻报错", lookup, ctx, "parameters.name.length")

# ---------------------------------------------------------------------- 渲染
check("整串表达式保留类型", render_typed("${{ parameters.enabledDB }}", ctx), True)
check("整串表达式数字保留类型", render_typed("${{ parameters.replicas }}", ctx), 3)
check("混排退化成字符串", render_typed("repo-${{ parameters.name }}", ctx), "repo-artist-web")
check("Nunjucks 布尔渲染成 True", render_nunjucks("{{ parameters.enabledDB }}", ctx), "True")
check("Nunjucks 插入 name", render_nunjucks("Hi {{ parameters.name }}!", ctx),
      "Hi artist-web!")
check("values 上下文（fetch:template 传入）", render_nunjucks("{{ values.name }}", ctx),
      "artist-web")
check("嵌套 input 递归渲染", render_input(
    {"url": "./template", "values": {"name": "${{ parameters.name }}",
                                     "db": "${{ parameters.enabledDB }}"}}, ctx),
    {"url": "./template", "values": {"name": "artist-web", "db": True}})
check("列表元素也渲染", render_input(["${{ parameters.name }}", "plain"], ctx),
      ["artist-web", "plain"])

# -------------------------------------------------------------------- 动作
publish = Action(
    id="publish:github",
    handler=lambda i: {"remoteUrl": i["repoUrl"],
                       "repoContentsUrl": i["repoUrl"] + "/blob/main"},
    input_schema={"required": ["repoUrl"]},
    output_schema={"properties": {"remoteUrl": {"type": "string"},
                                  "repoContentsUrl": {"type": "string"}}},
)
register = Action(
    id="catalog:register",
    handler=lambda i: {"entityRef": f"component:default/{i['catalogInfoPath'].strip('/').replace('.yaml', '')}"},
    input_schema={"required": ["repoContentsUrl"]},
)
no_dry = Action(id="publish:github", handler=publish.handler,
                input_schema=publish.input_schema, output_schema=publish.output_schema,
                supports_dry_run=False)
check("示例输出按 schema 生成", generate_example_output(publish.output_schema),
      {"remoteUrl": "<string>", "repoContentsUrl": "<string>"})
check("无 schema 时示例输出为空", generate_example_output(None), {})
check("缺必填入参被拒", validate_input(publish, {}), ["action publish:github requires input 'repoUrl'"])
check("必填入参齐全", validate_input(publish, {"repoUrl": "github.com?repo=x"}), [])

# -------------------------------------------------------------------- 模板校验
def make_tpl(**over):
    spec = {
        "owner": "group:default/platform",
        "type": "service",
        "parameters": [{"title": "Fill in", "required": ["name"],
                        "properties": {"name": {"type": "string"},
                                       "enabledDB": {"type": "boolean", "default": False}}}],
        "steps": [
            {"id": "publish", "name": "Publish", "action": "publish:github",
             "input": {"repoUrl": "github.com?repo=${{ parameters.name }}"}},
            {"id": "register", "name": "Register", "action": "catalog:register",
             "input": {"repoContentsUrl": "${{ steps['publish'].output.repoContentsUrl }}",
                       "catalogInfoPath": "/catalog-info.yaml"}},
        ],
    }
    spec.update(over.pop("spec", {}))
    return Template(api_version=over.pop("api_version", "backstage.io/v1beta2"),
                    name=over.pop("name", "v1beta2-demo"), spec=spec,
                    annotations=over.pop("annotations", {}), **over)


check("合法模板无错误", validate_template(make_tpl()), [])
check("两个 apiVersion 都接受", validate_template(
    make_tpl(api_version="scaffolder.backstage.io/v1beta3")), [])
check("错误 apiVersion 被拒", validate_template(make_tpl(api_version="backstage.io/v1")),
      ["apiVersion must be one of ('backstage.io/v1beta2', 'scaffolder.backstage.io/v1beta3')"])
check("缺 parameters 被拒", "spec.parameters is required" in validate_template(
    Template("backstage.io/v1beta2", "t", {"type": "service", "steps": []})), True)
check("spec.type 必填", "spec.type is required" in validate_template(
    Template("backstage.io/v1beta2", "t", {"parameters": [], "steps": []})), True)
check("step 缺 id 被拒", "step requires id" in validate_template(
    make_tpl(spec={"steps": [{"action": "publish:github"}]})), True)
check("重复 step id 被拒", any("duplicate" in e for e in validate_template(
    make_tpl(spec={"steps": [{"id": "a", "action": "x"}, {"id": "a", "action": "y"}]}))), True)

# -------------------------------------------------------------------- 执行
ACTIONS = {"publish:github": publish, "catalog:register": register,
           "fetch:template": Action("fetch:template", lambda i: {})}
ctx_out, results = execute(make_tpl(), {"name": "artist-web"}, ACTIONS)
check("两步都完成", [r.status for r in results], ["completed", "completed"])
check("publish 输出被后续步骤消费",
      ctx_out["steps"]["register"]["output"]["entityRef"], "component:default/catalog-info")
check("publish 的 remoteUrl", ctx_out["steps"]["publish"]["output"]["remoteUrl"],
      "github.com?repo=artist-web")
check("默认值填充", execute(make_tpl(), {"name": "x"}, ACTIONS)[0]["parameters"]["enabledDB"], False)
raises("缺必填参数报错", execute, make_tpl(), {}, ACTIONS)
raises("未知 action 报错", execute, make_tpl(), {"name": "x"}, {})

# if 条件跳过：步骤 output 为空，后续引用会取不到
skip_tpl = make_tpl(spec={"steps": [
    {"id": "publish", "action": "publish:github", "if": "${{ parameters.enabledDB }}",
     "input": {"repoUrl": "github.com?repo=x"}},
]})
ctx_skip, res_skip = execute(skip_tpl, {"name": "x"}, ACTIONS)
check("if 为假时跳过", res_skip[0].status, "skipped")
check("跳过的步骤 output 为空", res_skip[0].output, {})
check("if 为真时执行", execute(skip_tpl, {"name": "x", "enabledDB": True},
                              ACTIONS)[1][0].status, "completed")

# each 迭代
each_tpl = make_tpl(spec={"steps": [
    {"id": "fanout", "action": "fetch:template",
     "each": "${{ parameters.targets }}",
     "input": {"name": "${{ each.value }}", "key": "${{ each.key }}"}},
]})
each_ctx, each_res = execute(each_tpl, {"name": "x",
                                        "targets": {"a": "svc-a", "b": "svc-b"}}, ACTIONS)
check("each 产生两组结果", set(each_ctx["steps"]["fanout"]["output"]["results"]), {"a", "b"})

# dry-run：不支持 dry run 的动作按 output schema 生成示例
dry_actions = {"publish:github": no_dry, "catalog:register": register}
_, dry_res = execute(make_tpl(), {"name": "x"}, dry_actions, dry_run=True)
check("dry-run 生成示例输出", dry_res[0].output,
      {"remoteUrl": "<string>", "repoContentsUrl": "<string>"})
check("dry-run 状态标记", dry_res[0].status, "dry-run")
_, dry_res2 = execute(make_tpl(), {"name": "x"}, ACTIONS, dry_run=True)
check("支持 dry run 时仍走真实处理", dry_res2[0].status, "completed")

# ------------------------------------------------------------------ 输出与时长
out_tpl = make_tpl()
out_tpl.spec["output"] = {
    "links": [
        {"title": "Repository", "url": "${{ steps['publish'].output.remoteUrl }}"},
        {"if": "${{ parameters.enabledDB }}", "title": "CI", "url": "https://ci/x"},
    ],
    "text": [{"title": "Summary", "content": "name=${{ parameters.name }}"}],
}
rendered = render_outputs(out_tpl, ctx_out)
check("links 过滤掉 if 为假的条目", [l["title"] for l in rendered["links"]], ["Repository"])
check("link 的 url 已渲染", rendered["links"][0]["url"], "github.com?repo=artist-web")
check("text 保留", rendered["text"][0]["content"], "name=artist-web")
rendered_on = render_outputs(out_tpl, {**ctx_out, "parameters": {**ctx_out["parameters"],
                                                                 "enabledDB": True}})
check("enabledDB 为真时 CI 链接出现", [l["title"] for l in rendered_on["links"]],
      ["Repository", "CI"])

check("PT4H → 14400 秒", parse_duration("PT4H"), 14400.0)
check("PT15M → 900 秒", parse_duration("PT15M"), 900.0)
check("PT8H → 28800 秒", parse_duration("PT8H"), 28800.0)
check("P1DT2H → 93600 秒", parse_duration("P1DT2H"), 93600.0)
raises("空时长被拒", parse_duration, "P")

if FAIL:
    print(f"FAILED {len(FAIL)} / {PASS + len(FAIL)}")
    for f in FAIL:
        print("  -", f)
    raise SystemExit(1)
print(f"scaffold selfcheck: {PASS} assertions passed")

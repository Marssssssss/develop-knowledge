"""自检: GitLab CI rules / include / 下游流水线 的语义断言。

行为与数字均来自本目录 python/gitlab_pipeline.py 头部引用的官方文档原文。
运行: python gitlab_check.py
"""

from __future__ import annotations

from gitlab_pipeline import (
    MAX_INCLUDES,
    MAX_CHILD_CONFIGS,
    MAX_DOWNSTREAM,
    GitLabError,
    check_child_trigger,
    child_pipeline_source,
    job_allow_failure_default,
    resolve_includes,
    select_job,
    eval_if,
)

PASS, FAIL = 0, 0
FAILED = []


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILED.append("%s %s" % (label, detail))
        print("  FAIL %s %s" % (label, detail))


def raises(fn, *a, **kw):
    try:
        fn(*a, **kw)
        return False
    except (ValueError, GitLabError):
        return True


MR = {"name": "merge request", "pipeline_source": "merge_request_event",
      "variables": {"CI_PIPELINE_SOURCE": "merge_request_event"}}
SCHED = {"name": "scheduled", "pipeline_source": "schedule",
         "variables": {"CI_PIPELINE_SOURCE": "schedule"}}
PUSH = {"name": "branch push", "pipeline_source": "push",
        "variables": {"CI_PIPELINE_SOURCE": "push", "CI_COMMIT_BRANCH": "main",
                      "CI_DEFAULT_BRANCH": "main"}}


# ============================ 1. rules 首次匹配 ============================

def test_rules():
    print("[1] rules 首次匹配即生效")
    rules = [
        {"if": '$CI_PIPELINE_SOURCE == "merge_request_event"',
         "when": "manual", "allow_failure": True},
        {"if": '$CI_PIPELINE_SOURCE == "schedule"'},
    ]
    inc, attrs = select_job(rules, MR)
    check("MR 命中第 1 条", inc and attrs["rule_index"] == 0)
    check("第 1 条属性生效 when=manual/allow_failure=true",
          attrs["when"] == "manual" and attrs["allow_failure"] is True)
    inc, attrs = select_job(rules, SCHED)
    check("计划流水线落到第 2 条", inc and attrs["rule_index"] == 1)
    check("第 2 条未写属性 -> 官方默认 on_success/false",
          attrs["when"] == "on_success" and attrs["allow_failure"] is False)
    inc, _ = select_job(rules, PUSH)
    check("其余情况无 rule 命中 -> 不加入流水线", not inc)

    excl = [
        {"if": '$CI_PIPELINE_SOURCE == "merge_request_event"', "when": "never"},
        {"if": '$CI_PIPELINE_SOURCE == "schedule"', "when": "never"},
        {"when": "on_success"},
    ]
    check("when: never 排除 MR", not select_job(excl, MR)[0])
    check("when: never 排除计划流水线", not select_job(excl, SCHED)[0])
    inc, attrs = select_job(excl, PUSH)
    check("兜底 when: on_success 放行 push", inc and attrs["when"] == "on_success")

    # 官方: rules 内 when: manual 的 allow_failure 默认与 job 级关键字不同
    inc, attrs = select_job([{"when": "manual"}], PUSH)
    check("rules 内 when: manual -> allow_failure 默认 False",
          inc and attrs["allow_failure"] is False)
    check("job 级 when: manual -> allow_failure 默认 True",
          job_allow_failure_default("job-keyword") is True)
    check("rules 内 when: manual 与 job 级默认值确实不同",
          job_allow_failure_default("rules") != job_allow_failure_default("job-keyword"))

    inc, attrs = select_job([{"when": "delayed", "start_in": "5 minutes"}], PUSH)
    check("when: delayed 可带 start_in", inc and attrs["when"] == "delayed")
    inc, attrs = select_job([{"if": '$CI_COMMIT_BRANCH == "main"', "variables": {"DEPLOY": "1"}}], PUSH)
    check("rules:variables 随命中 rule 生效", attrs.get("variables") == {"DEPLOY": "1"})


# ============================ 2. rules:if 表达式 ============================

def test_if():
    print("[2] rules:if 变量表达式")
    v = {"CI_COMMIT_BRANCH": "main", "CI_DEFAULT_BRANCH": "main",
         "CI_COMMIT_TITLE": "feat: x-draft", "CI_JOB_NAME": "regex-job1",
         "pattern": "/^ab.*/", "teststring": "abcde"}

    check("== 相等", eval_if('$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH', v))
    check("!= 不等", eval_if('$CI_COMMIT_BRANCH != "dev"', v))
    check("=~ 正则命中(斜杠包裹)", eval_if(r'$CI_COMMIT_TITLE =~ /-draft$/', v))
    check("!~ 正则取反", eval_if(r'$CI_COMMIT_TITLE !~ /^release/', v))
    check("正则里的 $ 是行尾锚, 不是变量前缀", eval_if(r'$CI_COMMIT_TITLE =~ /-draft$/', v))
    check("变量存正则 + =~ 变量", eval_if('$teststring =~ $pattern', v))
    check("正则内的变量不展开(官方示例 1)", not eval_if('$CI_JOB_NAME =~ /$pattern/', v))
    check("正则内的变量不展开(官方示例 2)",
          not eval_if("$CI_JOB_NAME =~ /$CI_COMMIT_BRANCH/", v))
    check("未定义变量按空串", eval_if('$UNDEFINED == ""', v))
    check("括号 + && + ||",
          eval_if('($CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH || $CI_COMMIT_BRANCH == "develop") && $CI_JOB_NAME', v))
    check("括号内或条件为真", eval_if('($CI_COMMIT_BRANCH == "dev" || $CI_DEFAULT_BRANCH == "main")', v))
    check("&& 两侧都真才真",
          not eval_if('$CI_COMMIT_BRANCH == "main" && $CI_DEFAULT_BRANCH == "dev"', v))
    check("非推送类流水线: 计划", eval_if('$CI_PIPELINE_SOURCE == "schedule"', SCHED["variables"]))
    check("dotenv 变量对 rules 不可见(官方明确)",
          not eval_if('$DOTENV_ONLY == "1"', v))
    check("表达式语法错误是创建期错误", raises(eval_if, '$A == $B == $C', {}))


# ============================ 3. changes / exists ============================

def test_changes_exists():
    print("[3] rules:changes 与 rules:exists")
    push = {"pipeline_source": "push", "changed_paths": ["Dockerfile", "src/a.py"],
            "variables": {}, "files": []}
    check("push 流水线: Dockerfile 变更命中",
          select_job([{"changes": ["Dockerfile", "docker/scripts/**/*"]}], push)[0])
    check("push 流水线: 未变更多语言文件不命中",
          not select_job([{"changes": ["**/*.rs"]}], push)[0])

    for src in ("tag", "schedule", "web", "api"):
        ctx = dict(push, pipeline_source=src, changed_paths=[])
        check("非推送类流水线(%s) changes 恒真" % src,
              select_job([{"changes": ["**/*.rs"]}], ctx)[0])

    nb = dict(push, is_new_branch=True, changed_paths=[])
    check("新分支 changes 恒真", select_job([{"changes": ["**/*.rs"]}], nb)[0])

    cmp_ctx = dict(push, changed_paths=["a.py", "README.md"],
                   changed_since={"refs/heads/main": ["a.py"]})
    check("compare_to 换基线后命中",
          select_job([{"changes": {"paths": ["a.py"], "compare_to": "refs/heads/main"}}], cmp_ctx)[0])
    check("compare_to 换基线后不命中",
          not select_job([{"changes": {"paths": ["README.md"], "compare_to": "refs/heads/main"}}], cmp_ctx)[0])
    check("单条 rule 的 changes 模式上限 50",
          raises(select_job, [{"changes": ["f%d" % i for i in range(51)]}], push))
    check("50 个模式仍合法",
          isinstance(select_job([{"changes": ["f%d" % i for i in range(50)]}], push)[0], bool))

    ex = dict(push, files=["go.mod", "cmd/main.go"])
    check("exists 命中", select_job([{"exists": ["go.mod"]}], ex)[0])
    check("exists 未命中", not select_job([{"exists": ["Cargo.toml"]}], ex)[0])
    check("exists 目录 glob 命中", select_job([{"exists": ["cmd/**/*.go"]}], ex)[0])
    check("exists 检查上限 50000",
          raises(select_job, [{"exists": ["x"]}], dict(ex, files=["f%d" % i for i in range(50001)])))

    both = {"pipeline_source": "push", "changed_paths": ["Dockerfile"],
            "variables": {"V": "string value"}, "files": []}
    rule = [{"if": '$V == "string value"',
             "changes": ["Dockerfile", "docker/scripts/**/*"],
             "when": "manual", "allow_failure": True}]
    inc, attrs = select_job(rule, both)
    check("同一条 rule 内 if 与 changes 是 AND(两者都真才命中)",
          inc and attrs["when"] == "manual" and attrs["allow_failure"] is True)
    check("AND 语义: 只把 V 改掉就不命中",
          not select_job(rule, dict(both, variables={"V": "other"}))[0])


# ============================ 4. include ============================

def test_include():
    print("[4] include 合并与上限")
    files = {
        ".gitlab-ci.yml": {"include": ["common.yml"], "build": {"script": ["root-build"]}},
        "common.yml": {"build": {"script": ["included-build"]}, "test": {"script": ["t"]}},
    }
    merged, stat = resolve_includes(files, ".gitlab-ci.yml")
    check("include 的 job 被并入", "test" in merged)
    check(".gitlab-ci.yml 同名 job 覆盖 include(官方明确)", 
          merged["build"]["script"] == ["root-build"])
    check("include 计数为 1", stat["count"] == 1)
    check("解析耗时 = 1 × 200ms", abs(stat["seconds"] - 0.2) < 1e-9)

    dup = {".gitlab-ci.yml": {"include": ["a.yml"] * MAX_INCLUDES}, "a.yml": {}}
    _, st = resolve_includes(dup, ".gitlab-ci.yml")
    check("重复 include 计入上限(恰好 %d 个合法)" % MAX_INCLUDES, st["count"] == MAX_INCLUDES)
    check("%d 个 include 恰好用满 30 秒预算" % MAX_INCLUDES,
          abs(st["seconds"] - 30.0) < 1e-9)
    over = {".gitlab-ci.yml": {"include": ["a.yml"] * (MAX_INCLUDES + 1)}, "a.yml": {}}
    check("超过 %d 个 include 报错" % MAX_INCLUDES,
          raises(resolve_includes, over, ".gitlab-ci.yml"))

    nested = {
        ".gitlab-ci.yml": {"include": ["l1.yml"]},
        "l1.yml": {"include": ["l2.yml"], "j1": 1},
        "l2.yml": {"j2": 2},
    }
    merged, st = resolve_includes(nested, ".gitlab-ci.yml")
    check("嵌套 include 逐层展开", merged.get("j2") == 2 and merged.get("j1") == 1)
    check("嵌套 include 计数含全部层级", st["count"] == 2)
    check("嵌套 include 记录为无变量上下文", st["nested_no_vars"] == 1)

    check("overrides 可再覆盖结果",
          resolve_includes(files, ".gitlab-ci.yml", {"build": {"script": ["x"]}})[0]
          ["build"]["script"] == ["x"])


# ============================ 5. 下游流水线 ============================

def test_downstream():
    print("[5] 父子流水线 / 多项目流水线")
    check("第 1 层子流水线合法(父子)", check_child_trigger(1, ["a.yml"], 1) == [])
    check("第 2 层子流水线合法(父子)", check_child_trigger(2, ["a.yml", "b.yml"], 1) == [])
    errs = check_child_trigger(3, ["a.yml"], 1)
    check("第 3 层被拒: 父子最多两层",
          len(errs) == 1 and "cannot trigger another level of child pipelines" in errs[0],
          str(errs))
    check("多项目流水线无嵌套限制", check_child_trigger(9, ["a.yml"], 1, cross_project=True) == [])
    check("单个子流水线最多 3 个配置文件: 3 个合法",
          check_child_trigger(1, ["a", "b", "c"], 1) == [])
    errs = check_child_trigger(1, ["a", "b", "c", "d"], 1)
    check("单个子流水线 4 个配置文件被拒",
          len(errs) == 1 and str(MAX_CHILD_CONFIGS) in errs[0])
    check("下游流水线 1000 条合法", check_child_trigger(1, ["a"], MAX_DOWNSTREAM) == [])
    errs = check_child_trigger(1, ["a"], MAX_DOWNSTREAM + 1)
    check("下游流水线 1001 条被拒", len(errs) == 1 and str(MAX_DOWNSTREAM) in errs[0])

    check("子流水线 CI_PIPELINE_SOURCE = parent_pipeline",
          child_pipeline_source(False) == "parent_pipeline")
    check("多项目下游 CI_PIPELINE_SOURCE = pipeline",
          child_pipeline_source(True) == "pipeline")
    check("子流水线里不能靠 merge_request_event 选 job(官方建议改用 CI_MERGE_REQUEST_ID)",
          select_job([{"if": '$CI_PIPELINE_SOURCE == "merge_request_event"'}],
                     {"pipeline_source": "parent_pipeline",
                      "variables": {"CI_PIPELINE_SOURCE": "parent_pipeline"}})[0] is False)


def main():
    print("GitLab CI 语义自检")
    test_rules()
    test_if()
    test_changes_exists()
    test_include()
    test_downstream()
    print("\n断言 %d 通过 / %d 失败" % (PASS, FAIL))
    if FAILED:
        print("失败明细:")
        for f in FAILED:
            print("  - " + f)
        raise SystemExit(1)


if __name__ == "__main__":
    main()

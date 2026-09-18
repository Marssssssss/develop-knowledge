#!/usr/bin/env python3
"""dep_reachability.py 自检：SemVer 规范条款 + 解析结果 + 可达性剪枝。"""

import dep_reachability as D

PASS = FAIL = 0


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print("FAIL: %s %s" % (label, detail))


def main():
    c = D.cmp_semver
    V = D.v

    # ---- 1. semver.org §11 优先级链（规范原文给出的完整例子）----
    chain = ["1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-alpha.beta", "1.0.0-beta",
             "1.0.0-beta.2", "1.0.0-beta.11", "1.0.0-rc.1", "1.0.0"]
    for i in range(len(chain) - 1):
        check("优先级链 %s < %s" % (chain[i], chain[i + 1]),
              c(V(chain[i]), V(chain[i + 1])) < 0)
    check("1.0.0 < 2.0.0 < 2.1.0 < 2.1.1",
          c(V("1.0.0"), V("2.0.0")) < 0 and c(V("2.0.0"), V("2.1.0")) < 0
          and c(V("2.1.0"), V("2.1.1")) < 0)
    # beta.2 < beta.11 说明标识符是**逐段**比较而非字符串整体比较
    check("beta.2 < beta.11（数值而非字典序）", c(V("1.0.0-beta.2"), V("1.0.0-beta.11")) < 0)

    # ---- 2. §10 build metadata 不参与优先级 ----
    check("build metadata 被忽略", c(V("1.0.0+build1"), V("1.0.0+build2")) == 0)
    check("build metadata 不影响与正式版的比较", c(V("1.0.0+001"), V("1.0.0")) == 0)

    # ---- 3. 非法版本号（§2 禁止前导零）----
    for bad in ("01.2.3", "1.02.3", "1.2.03"):
        try:
            V(bad)
            check("拒绝前导零 " + bad, False, "未抛异常")
        except ValueError:
            check("拒绝前导零 " + bad, True)

    # ---- 4. ^ 与 ~ 的语义（含 0.x 的陷阱）----
    check("^1.2.3 允许 1.9.0", D.satisfies(V("1.9.0"), D.caret_range("1.2.3")))
    check("^1.2.3 拒绝 2.0.0", not D.satisfies(V("2.0.0"), D.caret_range("1.2.3")))
    # 规范 §4：0.y.z 是初始开发期，API 不稳定 → ^ 在 0.x 上锁到 minor
    check("^0.2.3 拒绝 0.3.0（0.x 只锁 minor）",
          not D.satisfies(V("0.3.0"), D.caret_range("0.2.3")))
    check("^0.2.3 允许 0.2.9", D.satisfies(V("0.2.9"), D.caret_range("0.2.3")))
    check("^0.0.3 拒绝 0.0.4", not D.satisfies(V("0.0.4"), D.caret_range("0.0.3")))
    check("~1.2.3 允许 1.2.9", D.satisfies(V("1.2.9"), D.tilde_range("1.2.3")))
    check("~1.2.3 拒绝 1.3.0", not D.satisfies(V("1.3.0"), D.tilde_range("1.2.3")))

    # ---- 5. 传递解析结果 ----
    r = D.resolve()
    check("解析出 5 个包", len(r) == 5, str(sorted(r)))
    check("httpkit 取区间内最高 2.4.0", r["httpkit"] == "2.4.0", r["httpkit"])
    check("codec 受 ~1.4.2 约束为 1.4.3", r["codec"] == "1.4.3", r["codec"])
    check("compress 停在唯一可用 0.2.7", r["compress"] == "0.2.7", r["compress"])
    check("logfmt 自动升到已修 1.0.2", r["logfmt"] == "1.0.2", r["logfmt"])
    check("orm 停在 3.1.0", r["orm"] == "3.1.0", r["orm"])

    # ---- 6. 两级过滤各自都真的剪掉了东西 ----
    rows = {x["cve"]: x for x in D.scan(r)}
    check("版本匹配命中 3 条",
          sum(1 for x in rows.values() if x["by_version"]) == 3)
    check("最终可达告警 2 条",
          sum(1 for x in rows.values() if x["reachable"]) == 2)
    check("CVE-LOGFMT 被版本过滤掉（已升到修复版）",
          not rows["CVE-LOGFMT"]["by_version"])
    check("CVE-COMPRESS 版本命中但不可达 → 被可达性剪枝",
          rows["CVE-COMPRESS"]["by_version"] and not rows["CVE-COMPRESS"]["reachable"])
    check("CVE-CODEC 可达", rows["CVE-CODEC"]["reachable"])
    check("CVE-ORM 可达", rows["CVE-ORM"]["reachable"])

    # ---- 7. 可达性集合本身 ----
    reach = D.reachable_from(D.ENTRY, D.CALLGRAPH)
    check("codec.Decode 可达", "codec.Decode" in reach)
    check("compress.Inflate 不可达", "compress.Inflate" not in reach)
    check("入口自身在集合内", D.ENTRY in reach)

    # ---- 8. SBOM 结构（SPDX 关系类型）----
    doc = D.sbom(r)
    rels = doc["relationships"]
    check("DESCRIBES 指向根包",
          any(x["relationshipType"] == "DESCRIBES" for x in rels))
    check("CONTAINS 覆盖全部 5 个包",
          sum(1 for x in rels if x["relationshipType"] == "CONTAINS") == 5)
    check("DEPENDS_ON 存在", any(x["relationshipType"] == "DEPENDS_ON" for x in rels))
    pkgs = {p["name"]: p for p in doc["packages"]}
    check("每个包都有 PURL externalRef",
          all("externalRefs" in p for n, p in pkgs.items() if n != D.ROOT[0]))
    check("每个包都有 SHA256 校验和",
          all(p["checksums"][0]["algorithm"] == "SHA256" for p in doc["packages"]))

    print("PASS=%d FAIL=%d" % (PASS, FAIL))
    if FAIL:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

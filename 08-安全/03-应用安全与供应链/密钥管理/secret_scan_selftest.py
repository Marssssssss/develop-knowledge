#!/usr/bin/env python3
"""secret_scan.py 自检：熵的数学性质 + 三级检测器的召回/误报。"""

import math

import secret_scan as S

PASS = FAIL = 0


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print("FAIL: %s %s" % (label, detail))


def main():
    H = S.shannon_entropy
    N = S.normalized_entropy

    # ---- 1. 香农熵的基本性质 ----
    check("空串熵为 0", H("") == 0.0)
    check("单字符串熵为 0", H("aaaa") == 0.0)
    check("均匀分布 16 符号 = 4.0 bit",
          abs(H("0123456789abcdef") - 4.0) < 1e-9, str(H("0123456789abcdef")))
    check("均匀分布 256 符号 = 8.0 bit",
          abs(H("".join(chr(i) for i in range(256))) - 8.0) < 1e-9)
    check("偏斜分布严格小于均匀",
          H("aaaaaabc") < H("abcdefab"), "%.4f vs %.4f" % (H("aaaaaabc"), H("abcdefab")))
    check("熵上界是 log2(|A|)",
          H("3f8a1c7d5e2b4096af17") <= math.log2(S.alphabet_size("3f8a1c7d5e2b4096af17")) + 1e-9)

    # ---- 2. 关键事实：十六进制串的熵上界是 4.0，永远够不到 4.5 ----
    hex40 = "3f8a1c7d5e2b4096af17c3de85b0f2146e9a7c31"
    check("40 位十六进制串熵 < 4.5（阈值 4.5 必然漏报）", H(hex40) < 4.5,
          "H=%.4f" % H(hex40))
    check("十六进制字符集上界就是 4.0", abs(math.log2(16) - 4.0) < 1e-12)
    check("归一化后同一串接近 1.0", N(hex40) > 0.95, "%.4f" % N(hex40))

    # ---- 3. 语料上的三级检测器 ----
    d = {label: det for label, det in S.DETECTORS}
    r1 = S.evaluate(d["D1 裸熵 ≥4.5"])
    r2 = S.evaluate(d["D2 归一化熵 ≥0.90"])
    r3 = S.evaluate(d["D3 规则+归一化熵"])
    check("D1 TP=2 FP=1 FN=3 TN=4", r1[:4] == (2, 1, 3, 4), str(r1[:4]))
    check("D2 TP=4 FP=2 FN=1 TN=3", r2[:4] == (4, 2, 1, 3), str(r2[:4]))
    check("D3 TP=5 FP=1 FN=0 TN=4", r3[:4] == (5, 1, 0, 4), str(r3[:4]))
    check("召回单调提升", r1[0] < r2[0] < r3[0], "%d/%d/%d" % (r1[0], r2[0], r3[0]))
    check("漏报单调下降", r1[2] > r2[2] > r3[2], "%d/%d/%d" % (r1[2], r2[2], r3[2]))
    check("D1 确实漏报了十六进制 token", any("api_token" in w for w in r1[4]))
    check("D2 的误报里有 git_sha", any("git_sha" in w for w in r2[4]))
    check("D3 用上下文白名单消掉了 git_sha 误报", not any("git_sha" in w for w in r3[4]))

    # ---- 4. 不可分性：任何裸熵阈值都分不开 aws_key 与 git_sha ----
    corpus = {n: v for n, v, _ in S.CORPUS}
    ha, hb = H(corpus["aws_key"]), H(corpus["git_sha"])
    check("H(aws_key) < H(git_sha)（密钥比非密钥更\"低熵\"）", ha < hb,
          "%.4f vs %.4f" % (ha, hb))
    check("二者不可被任何裸熵阈值分开",
          not S.separable_by_raw_threshold(corpus["aws_key"], corpus["git_sha"]))
    # 扫描验证：不存在零误报下的高召回
    sw = S.sweep()
    zero_fp = [x for x in sw if x[2] == 0]
    check("存在零误报阈值", len(zero_fp) > 0)
    check("零误报时召回最多 1 条（长密钥共 4 条）",
          max(x[1] for x in zero_fp) == 1, str(max(x[1] for x in zero_fp)))
    check("最高召回也只有 4 条", max(x[1] for x in sw) == 4)

    # ---- 5. 规则各自命中什么 ----
    check("AWS 结构规则命中 AKIA 前缀",
          any(r[1].search(corpus["aws_key"]) for r in S.VALUE_RULES))
    check("AWS 结构规则不命中 git sha",
          not any(r[1].search(corpus["git_sha"]) for r in S.VALUE_RULES))
    check("变量名规则命中 db_password", bool(S.NAME_RULE.search("db_password")))
    check("变量名规则不命中 request_id", not S.NAME_RULE.search("request_id"))
    check("白名单命中 git_sha 的名字", bool(S.NAME_ALLOWLIST.search("git_sha")))
    check("白名单不命中 api_token 的名字", not S.NAME_ALLOWLIST.search("api_token"))

    # ---- 6. 短串不走熵判据（长度门槛的意义）----
    check("短密钥 password123 长度 < MIN_LEN", len("password123") < S.MIN_LEN)
    check("短密钥由变量名字段救回",
          S.detect_combined("db_password", "password123"))
    check("归一化熵对短串不可靠：1.2.3 的归一化熵也很高",
          N("1.2.3") > 0.95 and len("1.2.3") < S.MIN_LEN)

    print("PASS=%d FAIL=%d" % (PASS, FAIL))
    if FAIL:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

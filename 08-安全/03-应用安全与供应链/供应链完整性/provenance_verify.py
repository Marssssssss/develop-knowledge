#!/usr/bin/env python3
"""SLSA v1.0 来源证明（Provenance）的生成与验证。

Provenance 回答的是「**这个制品是在哪里、何时、如何被生产出来的**」，
它是 in-toto attestation 框架里的一种 predicate：

    {
      "_type": "https://in-toto.io/Statement/v1",
      "subject": [ ResourceDescriptor, ... ],      # 构建输出（顶层字段，不在 predicate 里）
      "predicateType": "https://slsa.dev/provenance/v1",
      "predicate": {
        "buildDefinition": { buildType, externalParameters, internalParameters, resolvedDependencies },
        "runDetails": { builder{id,...}, metadata{invocationId, startedOn, finishedOn}, byproducts }
      }
    }

本 demo 的重点**不是生成**，而是**验证**：规范里那些 MUST / SHOULD 到底在防什么。
每条验证规则对应一个可运行的攻击场景。
"""

import base64
import hashlib
import hmac
import json
from typing import Dict, List, Optional, Tuple

PREDICATE_TYPE = "https://slsa.dev/provenance/v1"
STATEMENT_TYPE = "https://in-toto.io/Statement/v1"

# builder.id -> 该构建平台声明的 SLSA Build level
# 规范原话：builder.id "is intended to be the sole determiner of the SLSA Build level"
BUILDER_LEVELS: Dict[str, int] = {
    "https://github.com/actions/runner/github-hosted": 2,
    "https://cloudbuild.googleapis.com/GoogleCloudBuild": 3,
}

# 消费者**只接受特定"签名者—构建者"配对**（Consumers MUST accept only specific
# signer-builder pairs）：GitHub 可以为 GitHub Actions 签名，但不能为 Google Cloud Build 签名。
ACCEPTED_PAIRS = {
    ("github", "https://github.com/actions/runner/github-hosted"),
    ("google", "https://cloudbuild.googleapis.com/GoogleCloudBuild"),
}

# 我们对「正常的 externalParameters」的预期。规范：这些值**不可信**，必须下游验证，
# 且验证者 SHOULD 拒绝其中未识别/意外的字段。
EXPECTED_EXTERNAL_KEYS = {"repository", "ref"}


# ------------------------------------------------------------------ 工具

def canonical(obj) -> bytes:
    """确定性序列化（真实 DSSE 用类似的规范化，签名才不会被键序影响）。"""
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def sha256_hex(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sign(statement: dict, key: bytes, key_id: str) -> dict:
    """用 HMAC-SHA256 模拟签名（不引入第三方依赖；真实场景是 DSSE + 公钥签名）。"""
    payload = base64.b64encode(canonical(statement)).decode()
    sig = hmac.new(key, canonical(statement), hashlib.sha256).hexdigest()
    return {"payload": payload, "keyId": key_id, "signature": sig}


def open_envelope(env: dict, key: bytes) -> Optional[dict]:
    if not hmac.compare_digest(
            hmac.new(key, base64.b64decode(env["payload"]), hashlib.sha256).hexdigest(),
            env["signature"]):
        return None
    return json.loads(base64.b64decode(env["payload"]))


def make_provenance(builder_id: str, subject_name: str, artifact: bytes,
                    external: dict, internal: Optional[dict] = None,
                    deps: Optional[List[dict]] = None,
                    extra_predicate: Optional[dict] = None,
                    invocation: str = "inv-0001") -> dict:
    p = {
        "_type": STATEMENT_TYPE,
        "subject": [{"name": subject_name, "digest": {"sha256": sha256_hex(artifact)}}],
        "predicateType": PREDICATE_TYPE,
        "predicate": {
            "buildDefinition": {
                "buildType": "https://slsa-framework.github.io/github-actions-buildtypes/workflow/v1",
                "externalParameters": dict(external),
                "internalParameters": internal or {},
                "resolvedDependencies": deps or [],
            },
            "runDetails": {
                "builder": {"id": builder_id},
                "metadata": {
                    "invocationId": invocation,
                    "startedOn": "2026-09-18T10:00:00Z",
                    "finishedOn": "2026-09-18T10:04:00Z",
                },
                "byproducts": [],
            },
        },
    }
    if extra_predicate:
        p["predicate"].update(extra_predicate)
    return p


# ------------------------------------------------------------------ 验证

def verify(env: dict, key: bytes, artifact: bytes, min_level: int = 2) -> Tuple[str, List[str]]:
    """返回 (decision, reasons)。decision ∈ {"ALLOW", "DENY"}。"""
    reasons: List[str] = []

    stmt = open_envelope(env, key)
    if stmt is None:
        return "DENY", ["签名校验失败"]

    # 1) subject 摘要必须等于手上制品的摘要 —— 防「构建后又换了包」
    got = stmt["subject"][0]["digest"]["sha256"]
    want = sha256_hex(artifact)
    if not hmac.compare_digest(got, want):
        return "DENY", ["subject 摘要与制品不匹配（制品在构建后被替换）"]

    if stmt["predicateType"] != PREDICATE_TYPE:
        return "DENY", ["predicateType 不是 SLSA provenance v1"]

    pred = stmt["predicate"]
    bd = pred.get("buildDefinition", {})
    rd = pred.get("runDetails", {})
    builder_id = rd.get("builder", {}).get("id", "")

    # 2) 签名者—构建者配对。builder 与 signer 是分开的角色，不能混。
    if (env["keyId"], builder_id) not in ACCEPTED_PAIRS:
        return "DENY", ["不接受该 signer-builder 配对: signer=%s builder=%s"
                        % (env["keyId"], builder_id)]

    # 3) builder.id 是 SLSA Build level 的**唯一**决定因素
    level = BUILDER_LEVELS.get(builder_id, 0)
    if level < min_level:
        msg = "builder.id 声明的级别 %d 低于要求 %d" % (level, min_level)
        # 扩展字段不得改变其它字段的含义 → 自称的级别不作数
        if "x_slsaBuildLevel" in pred:
            msg += "；扩展字段 x_slsaBuildLevel=%r 不得用于提升级别（单调性原则）" \
                   % pred["x_slsaBuildLevel"]
        return "DENY", [msg]

    # 4) externalParameters 不可信 → 必须与预期一致，且拒绝未识别字段
    ext = bd.get("externalParameters", {})
    unexpected = set(ext) - EXPECTED_EXTERNAL_KEYS
    if unexpected:
        return "DENY", ["externalParameters 出现未预期字段: %s" % sorted(unexpected)]

    # 5) internalParameters 由可信平台设置 → **不需要**验证（规范明说）
    reasons.append("internalParameters 由受信任平台设置，按规范不校验")

    # 6) 未识别的顶层字段必须忽略（Consumers MUST ignore unrecognized fields）
    known_top = {"_type", "subject", "predicateType", "predicate"}
    ignored = sorted(set(stmt) - known_top)
    if ignored:
        reasons.append("忽略未识别顶层字段: %s" % ignored)
    known_pred = {"buildDefinition", "runDetails"}
    ignored_p = sorted(set(pred) - known_pred)
    if ignored_p:
        reasons.append("忽略未识别 predicate 字段（扩展）: %s" % ignored_p)
        # 扩展的单调性原则：忽略扩展不应把 DENY 变成 ALLOW。
        # 因此**任何**策略都不得依赖扩展字段来满足强制要求 —— 这里显式再确认一次
        # 级别仍只来自 builder.id。
        if "x_slsaBuildLevel" in pred:
            reasons.append(
                "扩展字段 x_slsaBuildLevel=%r 被忽略：级别只能由 builder.id 决定"
                % pred["x_slsaBuildLevel"])

    return "ALLOW", reasons


# ------------------------------------------------------------------ 场景

GOOD_ARTIFACT = b"binary-v1.0.0"
GOOD_BUILDER = "https://github.com/actions/runner/github-hosted"
GOOD_EXT = {"repository": "https://github.com/octocat/hello-world",
            "ref": "refs/heads/main"}
KEY = b"demo-signing-key"


def scenarios() -> List[Tuple[str, dict, bytes, int, str]]:
    """(名称, envelope, 手上的制品, min_level, 期望)"""

    def env_of(stmt, key_id="github"):
        return sign(stmt, KEY, key_id)

    base = make_provenance(GOOD_BUILDER, "hello-world", GOOD_ARTIFACT, GOOD_EXT,
                           internal={"runnerArch": "X64"},
                           deps=[{"uri": "git+https://github.com/octocat/hello-world",
                                  "digest": {"gitCommit": "7fd1a60b01f91b314f59955a4e4d4e80d8edf11d"}}])
    out = [("S1 正常", env_of(base), GOOD_ARTIFACT, 2, "ALLOW")]

    # S2 构建后被换包
    swapped = b"binary-v1.0.0-with-backdoor"
    out.append(("S2 制品被替换", env_of(base), swapped, 2, "DENY"))

    # S3 builder 不在可接受集合里
    unknown_builder = make_provenance("https://evil.example.com/builder", "hello-world",
                                      GOOD_ARTIFACT, GOOD_EXT)
    out.append(("S3 未知 builder", env_of(unknown_builder), GOOD_ARTIFACT, 2, "DENY"))

    # S4 签名者与构建者不匹配：github 不能给 Google Cloud Build 签名
    google_builder = make_provenance("https://cloudbuild.googleapis.com/GoogleCloudBuild",
                                     "hello-world", GOOD_ARTIFACT, GOOD_EXT)
    out.append(("S4 signer-builder 不匹配", env_of(google_builder), GOOD_ARTIFACT, 2, "DENY"))

    # S5 externalParameters 被塞了意外字段（攻击者诱导的额外构建入口）
    injected = make_provenance(GOOD_BUILDER, "hello-world", GOOD_ARTIFACT,
                               dict(GOOD_EXT, entryPoint="attacker-supplied.yml"))
    out.append(("S5 externalParameters 有意外字段", env_of(injected), GOOD_ARTIFACT, 2, "DENY"))

    # S6 未知扩展字段：必须忽略，且不影响结论
    with_ext = dict(base)
    with_ext["predicate"] = dict(base["predicate"], x_customHint="ignore-me")
    out.append(("S6 未知扩展字段被忽略", env_of(with_ext), GOOD_ARTIFACT, 2, "ALLOW"))

    # S7 扩展字段自称 L3，但 builder.id 只有 L2 → 要求 L3 时必须拒绝
    lvl_ext = make_provenance(GOOD_BUILDER, "hello-world", GOOD_ARTIFACT, GOOD_EXT,
                              extra_predicate={"x_slsaBuildLevel": 3})
    out.append(("S7 扩展自称 L3 但要求 L3", env_of(lvl_ext), GOOD_ARTIFACT, 3, "DENY"))

    return out


if __name__ == "__main__":
    for name, env, art, lvl, want in scenarios():
        got, reasons = verify(env, KEY, art, lvl)
        flag = "OK " if got == want else "BAD"
        print("%s %-34s -> %-5s (want %s)" % (flag, name, got, want))
        for r in reasons:
            print("      · %s" % r)

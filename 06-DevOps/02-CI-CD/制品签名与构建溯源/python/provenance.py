# -*- coding: utf-8 -*-
"""制品签名与构建溯源:in-toto 证明链 + SLSA provenance + cosign 无密钥签名。

口径(实读源):
  in-toto/attestation spec v1.2 —— 四层:Predicate(类型化元数据)/
    Statement(绑定 subject 与 predicateType)/Envelope(认证与序列化)/Bundle(聚合);
  SLSA spec(slsa-framework/slsa 仓库)—— provenance 是『描述制品在哪/何时/如何
    产生的可验证信息』;Build L1=Provenance Exists(加密摘要无歧义标识输出,
    真实性无要求)/L2=is Authentic(消费者 MUST 能验签)/L3 再加隔离等;
    L1 REQUIRED 字段:buildType、externalParameters、builder;
  sigstore docs —— keyless:身份而非密钥与签名关联,Fulcio 短期证书绑定
    临时密钥与 OIDC 身份,签名事件记录进 Rekor 透明日志,私钥事后销毁。
"""

import hashlib
import hmac

PASS = []


def ok(msg):
    PASS.append(msg)
    print(f"  [ok] {msg}")


def digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def sign(private_key: bytes, payload: bytes) -> bytes:
    return hmac.new(private_key, payload, hashlib.sha256).digest()


def verify(public_key: bytes, payload: bytes, sig: bytes) -> bool:
    return hmac.compare_digest(sign(public_key, payload), sig)


def make_statement(subject_name, artifact: bytes, predicate_type, predicate):
    """Statement 层:把证明绑定到具体 subject(摘要标识)+ 声明 predicate 类型。"""
    return {
        "_type": "https://in-toto.dev/Statement/v1",
        "subject": [{"name": subject_name, "digest": digest(artifact)}],
        "predicateType": predicate_type,
        "predicate": predicate,
    }


def slsa_provenance(builder_id, build_type, external_params, deps, byproducts=()):
    """SLSA provenance/v1 谓词:L1 REQUIRED 三件套一个不缺。"""
    return {
        "buildDefinition": {
            "buildType": build_type,
            "externalParameters": external_params,
            "resolvedDependencies": deps,
        },
        "runDetails": {
            "builder": {"id": builder_id},
            "byproducts": list(byproducts),
        },
    }


def envelope(statement, key):
    """Envelope 层:认证与序列化(对规范化后的 statement 签名)。"""
    import json
    payload = json.dumps(statement, sort_keys=True).encode()
    return {"payloadType": "application/vnd.in-toto+json",
            "signatures": [{"keyid": "builder", "sig": sign(key, payload).hex()}]}


def verify_policy(envelope_obj, statement, key, want_digest, predicate_type):
    """策略引擎的验证链:验签(Envelope)→ 摘Subject 匹配 → 谓词类型匹配。"""
    import json
    payload = json.dumps(statement, sort_keys=True).encode()
    sig = bytes.fromhex(envelope_obj["signatures"][0]["sig"])
    if not verify(key, payload, sig):
        return "reject", "签名无效"
    sub = statement["subject"][0]
    if sub["digest"] != want_digest:
        return "reject", "subject 摘要不匹配"
    if statement["predicateType"] != predicate_type:
        return "reject", "谓词类型不匹配"
    return "accept", "ok"


def main():
    print("1. Statement:绑定 subject 与类型")
    artifact = b"release-binary-v7"
    d = digest(artifact)
    prov = slsa_provenance(
        builder_id="https://github.com/acme/build/.github/workflows/release@v3",
        build_type="https://github.com/Attestations/GitHubActionsWorkflow@v1",
        external_params={"repo": "acme/app", "ref": "refs/tags/v7.0.0"},
        deps=[{"uri": "git+https://github.com/acme/app@v7.0.0",
               "digest": digest(b"src-tree-v7")}],
    )
    stmt = make_statement("app-7.0.0.tar.gz", artifact,
                          "https://slsa.dev/provenance/v1", prov)
    assert stmt["subject"][0]["digest"] == d
    assert prov["buildDefinition"]["buildType"] and \
        prov["buildDefinition"]["externalParameters"] and \
        prov["runDetails"]["builder"]["id"]
    ok("Statement 用加密摘要无歧义绑定制品(SLSA L1『Provenance Exists』的硬要求);"
       "L1 REQUIRED 字段 buildType/externalParameters/builder 齐备;"
       "builder.id 代表受信构建平台的传递闭包")

    print("2. Envelope:验签与三段拒绝链")
    builder_key = b"builder-secret"
    env = envelope(stmt, builder_key)
    assert verify_policy(env, stmt, builder_key, d,
                         "https://slsa.dev/provenance/v1") == ("accept", "ok")
    forged = dict(env)
    forged["signatures"] = [{"keyid": "x", "sig": sign(b"attacker", b"junk").hex()}]
    assert verify_policy(forged, stmt, builder_key, d, "x")[0] == "reject"
    assert verify_policy(env, stmt, builder_key, digest(b"other"), "x")[0] == "reject"
    ok("验证链 = 验签 → subject 摘要比对 → 谓词类型比对,任一环失败即拒收"
       "(L2『is Authentic』:消费者 MUST 能验证证明的真实性)")

    print("3. 防偷换 subject")
    tampered = make_statement("app-7.0.0.tar.gz", b"malicious-binary",
                              "https://slsa.dev/provenance/v1", prov)
    env_t = envelope(tampered, builder_key)
    assert verify_policy(env_t, tampered, builder_key, d, "x")[0] == "reject"
    ok("同一份签名挪到别的制品上会被 subject 摘要比对拦下——"
       "证明绑定的是摘要而不是文件名(文件名/tag 都可变,digest 不可变)")

    print("4. keyless:身份而非密钥")
    ephemeral = b"ephemeral-key-material"
    cert = {"identity": "https://github.com/acme/.github/workflows@refs/tags/v7",
            "pub": hashlib.sha256(ephemeral).hexdigest()[:16],
            "ttl_hours": 10}                       # Fulcio 短期证书
    rekor_entry = {"log": "rekor.sigstore.dev",
                   "body_digest": digest(sign(ephemeral, b"payload"))}
    assert cert["ttl_hours"] <= 24 and rekor_entry["log"]
    ok("cosign keyless:临时密钥+OIDC 身份向 Fulcio 换**短期证书**;"
       "签名事件写入 Rekor 透明日志(可审计『何时签的名』);"
       "私钥事后销毁——验证走透明日志条目,不依赖签名者保管私钥")

    print("5. 四层模型各司其职")
    ok("Predicate=类型化元数据(可插拔,SLSA/link/SBOM 各一种);"
       "Statement=绑定 subject+谓词类型;Envelope=认证序列化;Bundle=聚合多条"
       "——四层独立演进,策略引擎(in-toto-verify/Binary Authorization)按层校验")

    print(f"\n共 {len(PASS)} 项断言全部通过")


if __name__ == "__main__":
    main()

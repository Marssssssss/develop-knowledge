"""演示：平台团队定义 XPostgreSQLInstance，应用团队用 Claim 申请一个库。

运行：python python/main.py
"""

from crossplane import (
    Composition, Composite, Names, PipelineStep, Version, Xrd,
    filter_connection_secret, resolve_policies, select_composition,
)

XRD = Xrd(
    name="xpostgresqlinstances.database.example.org",
    group="database.example.org",
    names=Names(kind="XPostgreSQLInstance", plural="xpostgresqlinstances"),
    versions=[Version("v1alpha1", served=True, referenceable=False),
              Version("v1beta1", served=True, referenceable=True)],
    claim_names=Names(kind="PostgreSQLInstance", plural="postgresqlinstances"),
    connection_secret_keys=["endpoint", "username"],
    default_composition_ref="postgres-aws",
)

COMPOSITIONS = [
    Composition("postgres-aws",
                ("database.example.org/v1beta1", "XPostgreSQLInstance"),
                [PipelineStep("patch-and-transform", "function-patch-and-transform"),
                 PipelineStep("auto-ready", "function-auto-ready")],
                labels={"provider": "aws", "tier": "gold"}),
    Composition("postgres-gcp",
                ("database.example.org/v1beta1", "XPostgreSQLInstance"),
                [PipelineStep("patch-and-transform", "function-patch-and-transform")],
                labels={"provider": "gcp", "tier": "standard"}),
]


def main() -> None:
    print("== XRD ==")
    print("  校验：", XRD.validate() or "OK")
    print("  composite GVK：", XRD.composite_gvk())
    print("  claim GVK    ：", XRD.claim_gvk())
    print("  offersClaim  ：", XRD.offers_claim())

    print()
    print("== 组合选择 ==")
    for label, composite in [
        ("不指定（走 XRD 默认）", Composite("db-1")),
        ("按 provider=aws 选择", Composite("db-2", composition_selector={"provider": "aws"})),
        ("按 provider=gcp 选择", Composite("db-3", composition_selector={"provider": "gcp"})),
        ("显式 compositionRef", Composite("db-4", composition_ref="postgres-gcp")),
    ]:
        chosen = select_composition(XRD, composite, COMPOSITIONS)
        print(f"  {label:22} -> {chosen.name}（{len(chosen.pipeline)} 步）")

    print()
    print("== 连接密钥白名单 ==")
    produced = {"endpoint": "db.example.org", "username": "app", "password": "s3cr3t"}
    print("  产出：", produced)
    print("  发布：", filter_connection_secret(XRD.connection_secret_keys, produced))

    print()
    print("== 策略回落 ==")
    print("  实例未指定：", resolve_policies(XRD, Composite("db-1")))
    print("  实例覆盖  ：", resolve_policies(
        XRD, Composite("db-5", composite_delete_policy="Foreground")))


if __name__ == "__main__":
    main()

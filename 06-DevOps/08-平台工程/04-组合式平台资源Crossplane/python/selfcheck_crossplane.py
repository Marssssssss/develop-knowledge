"""自检：Crossplane XRD / Composition / Claim（crossplane.py）。成对构造误报集/漏报集。"""

from crossplane import (
    Composition, Composite, CrossplaneError, Names, PipelineStep, Version, Xrd,
    filter_connection_secret, resolve_policies, select_composition,
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
    except CrossplaneError:
        PASS += 1
    except Exception as e:
        FAIL.append(f"{label}: raised {type(e).__name__} instead of CrossplaneError")
    else:
        FAIL.append(f"{label}: expected CrossplaneError")


def xrd(**over):
    base = dict(
        name="xpostgresqlinstances.database.example.org",
        group="database.example.org",
        names=Names(kind="XPostgreSQLInstance", plural="xpostgresqlinstances"),
        versions=[Version("v1alpha1", served=True, referenceable=False),
                  Version("v1beta1", served=True, referenceable=True)],
    )
    base.update(over)
    return Xrd(**base)


# ------------------------------------------------------------------- XRD 校验
check("合法 XRD", xrd().validate(), [])
check("名字必须等于 plural.group", xrd(name="wrong-name").validate(),
      ["metadata.name must equal <names.plural>.<group>"])
check("plural 必须小写", "names.plural must be lowercase" in xrd(
    names=Names("XPostgreSQLInstance", "XPostgreSQLInstances")).validate(), True)
check("singular 必须小写", xrd(names=Names("XPostgreSQLInstance", "xpostgresqlinstances",
                                          singular="XPostgresql")).validate(),
      ["names.singular must be lowercase"])
check("默认 scope 是 LegacyCluster", xrd().scope, "LegacyCluster")
check("scope 非法值", xrd(scope="Weird").validate(),
      ["scope must be one of ('LegacyCluster', 'Namespaced', 'Cluster')"])
check("无版本报错", xrd(versions=[]).validate()[0], "at least one version is required")
check("无 referenceable 版本报错",
      xrd(versions=[Version("v1", referenceable=False)]).validate(),
      ["at least one version must be referenceable"])

# CEL：只有 LegacyCluster 能开 claim / connection secret
claim = Names(kind="PostgreSQLInstance", plural="postgresqlinstances")
check("LegacyCluster 允许 claim",
      xrd(claim_names=claim).validate(), [])
check("Namespaced 不允许 claim",
      xrd(scope="Namespaced", claim_names=claim).validate(),
      ["Only LegacyCluster composite resources can offer claims"])
check("Cluster 不允许 claim",
      xrd(scope="Cluster", claim_names=claim).validate(),
      ["Only LegacyCluster composite resources can offer claims"])
check("LegacyCluster 允许 connectionSecretKeys",
      xrd(connection_secret_keys=["endpoint"]).validate(), [])
check("Namespaced 不允许 connectionSecretKeys",
      xrd(scope="Namespaced", connection_secret_keys=["endpoint"]).validate(),
      ["Only LegacyCluster composite resources support connection secrets"])

# ----------------------------------------------------------------- GVK 推导
check("composite GVK 取 referenceable 版本", xrd().composite_gvk(),
      ("database.example.org", "v1beta1", "XPostgreSQLInstance"))
check("claim GVK（有 claim）", xrd(claim_names=claim).claim_gvk(),
      ("database.example.org", "v1beta1", "PostgreSQLInstance"))
check("无 claim 时 claim GVK 为空", xrd().claim_gvk(), ("", "", ""))
check("offers_claim 等价于 claimNames 非空", xrd().offers_claim(), False)
check("offers_claim 为真", xrd(claim_names=claim).offers_claim(), True)
multi = xrd(versions=[Version("v1alpha1", referenceable=True),
                      Version("v1beta1", referenceable=True)])
check("多个 referenceable 时取最后一个（官方循环无 break）", multi.composite_gvk(),
      ("database.example.org", "v1beta1", "XPostgreSQLInstance"))
check("referenceable 出现在前面也照样被覆盖", xrd(
    versions=[Version("v1beta1", referenceable=True),
              Version("v2", referenceable=True)]).composite_gvk()[1], "v2")

# ----------------------------------------------------------------- Composition
def comp(name="base", n=1, labels=None, mode="Pipeline"):
    return Composition(
        name=name,
        composite_type_ref=("database.example.org/v1beta1", "XPostgreSQLInstance"),
        pipeline=[PipelineStep(f"step-{i}", f"fn-{i}") for i in range(n)],
        mode=mode,
        labels=labels or {},
    )


check("合法 Composition", comp().validate(), [])
check("pipeline 为空被拒", comp(n=0).validate(),
      ["pipeline length must be in [1, 99]"])
check("pipeline 100 步被拒", comp(n=100).validate(),
      ["pipeline length must be in [1, 99]"])
check("pipeline 99 步合法", comp(n=99).validate(), [])
check("step 名重复被拒", Composition(
    name="dup",
    composite_type_ref=("database.example.org/v1beta1", "XPostgreSQLInstance"),
    pipeline=[PipelineStep("same", "a"), PipelineStep("same", "b")]).validate(),
    ["pipeline step names must be unique (listMapKey=step)"])
check("mode 只能 Pipeline", comp(mode="Resources").validate(),
      ["mode must be one of ('Pipeline',)"])
check("缺省 mode 是 Pipeline", comp().mode, "Pipeline")

# -------------------------------------------------------------- 组合选择
comps = [comp("aws", labels={"provider": "aws", "tier": "gold"}),
         comp("gcp", labels={"provider": "gcp"})]
check("enforced 压过 selector", select_composition(
    xrd(enforced_composition_ref="gcp"),
    Composite("x", composition_selector={"provider": "aws"}), comps).name, "gcp")
check("selector 命中唯一", select_composition(
    xrd(), Composite("x", composition_selector={"provider": "aws"}), comps).name, "aws")
check("实例 ref 生效", select_composition(
    xrd(), Composite("x", composition_ref="gcp"), comps).name, "gcp")
check("回落到 XRD 默认", select_composition(
    xrd(default_composition_ref="aws"), Composite("x"), comps).name, "aws")
raises("selector 命中 0 个", select_composition, xrd(),
       Composite("x", composition_selector={"provider": "azure"}), comps)
raises("selector 命中多个（不静默取第一个）", select_composition, xrd(),
       Composite("x", composition_selector={"tier": "gold"}),
       [comp("a", labels={"provider": "aws", "tier": "gold"}),
        comp("b", labels={"provider": "gcp", "tier": "gold"})])
raises("enforced 指向不存在", select_composition,
       xrd(enforced_composition_ref="nope"), Composite("x"), comps)
raises("无任何来源", select_composition, xrd(), Composite("x"), comps)

# ---------------------------------------------------- connection secret 与策略
produced = {"endpoint": "db.example.org", "password": "s3cr3t", "username": "app"}
check("名单为空 → 全部发布", filter_connection_secret(None, produced), produced)
check("名单为空列表也全部发布", filter_connection_secret([], produced), produced)
check("白名单过滤", filter_connection_secret(["endpoint"], produced),
      {"endpoint": "db.example.org"})
check("白名单含不存在的键", filter_connection_secret(["endpoint", "nope"], produced),
      {"endpoint": "db.example.org"})

x = xrd()
check("默认删除策略是 Background", x.default_composite_delete_policy, "Background")
check("默认更新策略是 Automatic", x.default_composition_update_policy, "Automatic")
check("实例未指定时回落默认值", resolve_policies(x, Composite("x")),
      ("Background", "Automatic"))
check("实例覆盖默认值", resolve_policies(
    x, Composite("x", composite_delete_policy="Foreground",
                 composition_update_policy="Manual")), ("Foreground", "Manual"))

if FAIL:
    print(f"FAILED {len(FAIL)} / {PASS + len(FAIL)}")
    for f in FAIL:
        print("  -", f)
    raise SystemExit(1)
print(f"crossplane selfcheck: {PASS} assertions passed")

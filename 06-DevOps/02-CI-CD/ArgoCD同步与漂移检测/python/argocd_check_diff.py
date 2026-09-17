"""自检(diff 侧): 忽略规则匹配 / JSON Pointer / jq 子集 / managedFields / Quantity 规整。

由 ``argocd_check.py`` 的 ``main()`` 调用, 断言计数与 harness 共用。
"""

from __future__ import annotations

from argocd_diff import (
    apply_jq_path,
    canonicalize_known_types,
    canonicalize_quantity,
    content_digest,
    ignore_differences,
    is_out_of_sync,
    ownership_of,
    parse_jq_path,
    should_ignore_status,
    unescape_pointer_token,
)
from argocd_harness import check, raises


# ============================ 5. diff 定制 ============================

def test_diff():
    print("[5] diff 定制与去噪")
    live = {"apiVersion": "apps/v1", "kind": "Deployment",
            "metadata": {"name": "guestbook", "namespace": "default"},
            "spec": {"replicas": 3, "template": {"spec": {"containers": [
                {"name": "app", "image": "g:1"}]}}},
            "status": {"readyReplicas": 3}}
    target = {"apiVersion": "apps/v1", "kind": "Deployment",
              "metadata": {"name": "guestbook", "namespace": "default"},
              "spec": {"replicas": 2, "template": {"spec": {"containers": [
                  {"name": "app", "image": "g:1"}]}}}}

    check("未忽略时 OutOfSync", is_out_of_sync(live, target, []))
    rule = [{"group": "apps", "kind": "Deployment", "jsonPointers": ["/spec/replicas"]}]
    check("忽略 /spec/replicas 后 Synced", not is_out_of_sync(live, target, rule))
    check("status 字段默认被忽略(官方 ignoreResourceStatusField 默认 all)",
          not is_out_of_sync(live, target, rule))
    check("ignoreResourceStatusField: none 时 status 也算差异",
          is_out_of_sync(live, target, rule, ignore_status="none"))
    check("忽略规则只对匹配的 kind 生效",
          is_out_of_sync(live, target, [{"group": "apps", "kind": "StatefulSet",
                                         "jsonPointers": ["/spec/replicas"]}]))
    check("忽略规则可按 name 收窄: 名字不符则不生效",
          is_out_of_sync(live, target, [{"group": "apps", "kind": "Deployment",
                                         "name": "other",
                                         "jsonPointers": ["/spec/replicas"]}]))
    check("忽略规则可按 namespace 匹配(name+namespace 命中则生效)",
          not is_out_of_sync(live, target, [{"group": "apps", "kind": "Deployment",
                                             "name": "guestbook", "namespace": "default",
                                             "jsonPointers": ["/spec/replicas"]}]))
    check("group 通配 * 对所有资源生效",
          not is_out_of_sync(live, target, [{"group": "*", "kind": "*",
                                             "jsonPointers": ["/spec/replicas"]}]))
    check("core 组的 group 是空串而非 v1",
          not is_out_of_sync(
              {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "c"},
               "metadata2": 1, "data": {"a": "1"}},
              {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "c"},
               "data": {"a": "1"}},
              [{"group": "", "kind": "ConfigMap", "jsonPointers": ["/metadata2"]}]))

    # managedFields 所有权
    live2 = {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "g"},
             "spec": {"replicas": 5, "template": {"spec": {"containers": [{"name": "a"}]}}}}
    tgt2 = {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "g"},
            "spec": {"replicas": 2, "template": {"spec": {"containers": [{"name": "a"}]}}}}
    check("未忽略 replicas 时 OutOfSync", is_out_of_sync(live2, tgt2, []))
    check("按 managedFieldsManagers 忽略后 Synced",
          not is_out_of_sync(live2, tgt2, [{"group": "*", "kind": "*",
                                            "managedFieldsManagers": ["kube-controller-manager"]}]))
    check("kube-controller-manager 拥有 Deployment 的 /spec/replicas",
          ownership_of("kube-controller-manager", "Deployment") ==
          ["/spec/replicas", "/spec/template/spec/containers/0/resources"])
    check("未登记的 manager 无所有权", ownership_of("who", "Deployment") == [])
    check("HPA 也拥有 /spec/replicas",
          ownership_of("horizontal-pod-autoscaler", "Deployment") == ["/spec/replicas"])

    # JSON Pointer 转义
    check("~1 反转义为 /", unescape_pointer_token("node-role.kubernetes.io~1worker") ==
          "node-role.kubernetes.io/worker")
    check("~0 反转义为 ~", unescape_pointer_token("a~0b") == "a~b")

    # jq 子集
    live3 = {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "g"},
             "spec": {"template": {"spec": {"initContainers": [
                 {"name": "injected-init-container", "image": "x"},
                 {"name": "real-init", "image": "y"}]}}}}
    tgt3 = {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "g"},
            "spec": {"template": {"spec": {"initContainers": [{"name": "real-init", "image": "y"}]}}}}
    check("未忽略时 OutOfSync", is_out_of_sync(live3, tgt3, []))
    jq = [{"group": "apps", "kind": "Deployment",
           "jqPathExpressions":
               ['.spec.template.spec.initContainers[] | select(.name == "injected-init-container")']}]
    check("jq select 过滤注入的 initContainer 后 Synced", not is_out_of_sync(live3, tgt3, jq))
    check("jq 子集解析出通配位置", parse_jq_path(".a[].b") == (["a", "b"], 0, None))
    check("通配可以在中间层", parse_jq_path(".a[]?.b.c") == (["a", "b", "c"], 0, None))
    check("无通配时为 None", parse_jq_path(".a.b") == (["a", "b"], None, None))
    check("jq 子集解析出 select", parse_jq_path('.a[] | select(.k == "v")') ==
          (["a"], 0, ("k", "v")))
    check("不支持的 select 报错", raises(parse_jq_path, '.a[] | select(.k > 1)'))
    check("非 . 开头报错", raises(parse_jq_path, "a.b"))
    doc = {"webhooks": [{"clientConfig": {"caBundle": "X"}}, {"clientConfig": {"caBundle": "Y"}}]}
    apply_jq_path(doc, ".webhooks[]?.clientConfig.caBundle")
    check("链式 jq 删除嵌套字段",
          all("caBundle" not in w["clientConfig"] for w in doc["webhooks"]))
    doc2 = {"webhooks": [{"clientConfig": {"caBundle": "X"}}]}
    apply_jq_path(doc2, ".webhooks.clientConfig.caBundle")
    check("省略 [] 时数组不展开, 路径落空",
          doc2["webhooks"][0]["clientConfig"]["caBundle"] == "X")

    # 已知类型规整
    rollout = {"spec": {"template": {"spec": {"containers": [
        {"name": "a", "resources": {"requests": {"cpu": "100m"}}}]}}}}
    canonicalize_known_types(rollout, "argoproj.io/Rollout", "spec.template.spec")
    check("100m 被规整为 0.1", rollout["spec"]["template"]["spec"]["containers"][0]
          ["resources"]["requests"]["cpu"] == 0.1)
    check("100m 与 0.1 规整后相等",
          canonicalize_quantity("100m") == canonicalize_quantity("0.1"))
    check("250m -> 0.25", canonicalize_quantity("250m") == 0.25)
    check("1 保持 1.0", canonicalize_quantity("1") == 1.0)
    check("带后缀的 1Gi 原样返回", canonicalize_quantity("1Gi") == "1Gi")
    check("未登记的类型报错",
          raises(canonicalize_known_types, rollout, "argoproj.io/Other", "spec.x"))
    lv = {"apiVersion": "argoproj.io/v1alpha1", "kind": "Rollout", "metadata": {"name": "r"},
          "spec": {"template": {"spec": {"containers": [
              {"name": "a", "resources": {"requests": {"cpu": "100m"}}}]}}}}
    tg = {"apiVersion": "argoproj.io/v1alpha1", "kind": "Rollout", "metadata": {"name": "r"},
          "spec": {"template": {"spec": {"containers": [
              {"name": "a", "resources": {"requests": {"cpu": "0.1"}}}]}}}}
    check("规整前 100m vs 0.1 是假漂移", is_out_of_sync(lv, tg, [], kind="Rollout"))
    a, b = ignore_differences(lv, tg, [], kind="Rollout")
    canonicalize_known_types(a, "argoproj.io/Rollout", "spec.template.spec")
    canonicalize_known_types(b, "argoproj.io/Rollout", "spec.template.spec")
    check("规整后假漂移消失",
          a == b)

    check("非法 ignoreResourceStatusField 报错",
          raises(should_ignore_status, "sometimes", "Deployment"))
    check("content_digest 形状正确",
          content_digest(b"x").startswith("sha256:") and len(content_digest(b"x")) == 71)
    check("content_digest 内容敏感", content_digest(b"x") != content_digest(b"y"))
    check("sha512 摘要更长", len(content_digest(b"x", "sha512")) == 135)

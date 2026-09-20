# -*- coding: utf-8 -*-
"""anscoll 自检：全部基于实际读过的官方原文口径。"""
from anscoll import (parse_fqcn, is_valid_playbook_name, module_utils_import,
                     resolve_plugin, resolve_in_role, requires_ansible_ok,
                     route_plugin, NON_SEARCHABLE_TYPES)

N = 0
FAIL = []


def check(label, cond, detail=""):
    global N
    N += 1
    if not cond:
        FAIL.append("%s  %s" % (label, detail))


AVAIL = [
    "ansible.builtin.copy",
    "community.general.kubevirt",
    "community.general.my_inventory",
    "my_ns.my_coll.ping",
    "other.coll.ping",
    "my_ns.my_coll.lookup_x",
]

# ---- 1. FQCN 解析 ----
check("A1 FQCN 三段拆分", parse_fqcn("ns.coll.mod") == ("ns", "coll", ["mod"]), parse_fqcn("ns.coll.mod"))
check("A2 子路径保留", parse_fqcn("ns.coll.playbook1") == ("ns", "coll", ["playbook1"]))
check("A3 短名不是 FQCN", parse_fqcn("copy") is None)
check("A4 两段不是 FQCN", parse_fqcn("ns.coll") is None)
check("A5 集合内 playbook 名不允许连字符",
      is_valid_playbook_name("playbook1") and not is_valid_playbook_name("my-playbook"))

# ---- 2. collections 是有序搜索路径 ----
fq, why = resolve_plugin("ping", ["my_ns.my_coll", "other.coll"], AVAIL)
check("B1 命中第一个集合", (fq, why) == ("my_ns.my_coll.ping", "search_path"), (fq, why))
fq, why = resolve_plugin("ping", ["other.coll", "my_ns.my_coll"], AVAIL)
check("B2 顺序决定命中谁", (fq, why) == ("other.coll.ping", "search_path"), (fq, why))
fq, why = resolve_plugin("ping", [], AVAIL)
check("B3 空搜索路径找不到", (fq, why) == (None, "not_found"), (fq, why))
fq, why = resolve_plugin("my_ns.my_coll.ping", [], AVAIL)
check("B4 FQCN 不需要搜索路径", (fq, why) == ("my_ns.my_coll.ping", "fqcn"), (fq, why))
fq, why = resolve_plugin("nope.absent.ping", [], AVAIL)
check("B5 不存在的 FQCN 报 not_found", (fq, why) == (None, "not_found"), (fq, why))

# ---- 3. 非 action/module 类型必须用 FQCN ----
for t in ["lookup", "filter", "test"]:
    fq, why = resolve_plugin("lookup_x", ["my_ns.my_coll"], AVAIL, ptype=t)
    check("C-%s 在搜索路径里也要求 FQCN" % t, (fq, why) == (None, "requires_fqcn"), (fq, why))
fq, why = resolve_plugin("lookup_x", ["my_ns.my_coll"], AVAIL, ptype="module")
check("C4 module 类型可以用短名", (fq, why) == ("my_ns.my_coll.lookup_x", "search_path"), (fq, why))
check("C5 非可搜索类型集合口径",
      NON_SEARCHABLE_TYPES == {"lookup", "filter", "test", "connection",
                               "callback", "vars", "cache"})

# ---- 4. 角色不继承 playbook 的 collections ----
fq, why = resolve_in_role("ping", ["my_ns.my_coll"], [], AVAIL)
check("D1 角色没定义自己的 collections 时解析不到",
      (fq, why) == (None, "not_found"), (fq, why))
fq, why = resolve_in_role("ping", ["my_ns.my_coll"], ["other.coll"], AVAIL)
check("D2 角色用自己的列表", (fq, why) == ("other.coll.ping", "search_path"), (fq, why))
fq, why = resolve_in_role("ping", [], ["my_ns.my_coll"], AVAIL)
check("D3 playbook 为空而角色有值时照样命中",
      (fq, why) == ("my_ns.my_coll.ping", "search_path"), (fq, why))

# ---- 5. module_utils 导入路径 ----
check("E1 module_utils 导入约定",
      module_utils_import("community", "test_collection", "qradar")
      == "ansible_collections.community.test_collection.plugins.module_utils.qradar")

# ---- 6. requires_ansible 与预发布截断 ----
check("F1 官方原例：2.11.0b1 满足 >=2.11", requires_ansible_ok(">=2.11", "2.11.0b1"))
check("F2 截断后 2.10 不满足 >=2.11", not requires_ansible_ok(">=2.11", "2.10.0"))
check("F3 区间说明符", requires_ansible_ok(">=2.10,<2.11", "2.10.3")
      and not requires_ansible_ok(">=2.10,<2.11", "2.11.0"))
check("F4 逗号分隔是与", not requires_ansible_ok(">=2.10,<2.11", "2.12.0"))
check("F5 正式版正常比较", requires_ansible_ok(">=2.9", "2.15.0"))
check("F6 截断对 rc 同样成立", requires_ansible_ok(">=2.11", "2.11.0rc1"))
check("F7 位数不同按位补齐", requires_ansible_ok(">=2.11", "2.11"))

# ---- 7. plugin_routing ----
rt = {"plugin_routing": {"inventory": {"kubevirt": {"redirect": "community.general.kubevirt"}}}}
tgt, w, f = route_plugin(rt, "inventory", "kubevirt")
check("G1 redirect 改指向", (tgt, f) == ("community.general.kubevirt", None), (tgt, w, f))
rt = {"plugin_routing": {"inventory": {"my_inventory": {"tombstone": {
    "removal_version": "2.0.0",
    "warning_text": "my_inventory has been removed. Please use other_inventory instead."}}}}}
tgt, w, f = route_plugin(rt, "inventory", "my_inventory")
check("G2 tombstone 是致命错误", tgt is None and "my_inventory" in f and "2.0.0" in f, f)
rt = {"plugin_routing": {"modules": {"old": {"deprecation": {"warning_text": "use new"},
                                             "redirect": "ns.coll.new"}}}}
tgt, w, f = route_plugin(rt, "modules", "old")
check("G3 改名时 deprecation 与 redirect 并存",
      tgt == "ns.coll.new" and w == ["use new"] and f is None, (tgt, w, f))
tgt, w, f = route_plugin(rt, "modules", "not_routed")
check("G4 未登记的插件原样返回", (tgt, w, f) == ("not_routed", [], None), (tgt, w, f))

print("checks=%d fail=%d" % (N, len(FAIL)))
for f_ in FAIL:
    print("  FAIL", f_)

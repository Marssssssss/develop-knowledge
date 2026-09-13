"""Terraform State diff & Plan 输出生成器

来源:
- Manage resources in Terraform state
  (developer.hashicorp.com/terraform/tutorials/cli/state-cli)
- Manage resource drift (developer.hashicorp.com/terraform/tutorials/state/resource-drift)
- Terraform State File Format (官方 Version 4 JSON)
  "version": 4, "terraform_version": "1.x",
  "serial": 12, "lineage": "uuid",
  "resources": [{ "mode", "type", "name", "provider", "instances": [
  { "schema_version", "attributes": {...}, ... } ]}]

实现:
- 解析 version 4 state JSON
- 输入"desired config"(简化 dict 形式)
- 计算 diff:create / update / destroy 三类
- 输出 +/~/- 一行行 plan 摘要(对应 `terraform plan -no-color`)
"""

import json
from typing import Any


# -----------------------------------------------------------------------------
# State v4 JSON loader
# -----------------------------------------------------------------------------

def load_state_v4(path_or_str) -> dict:
    """读 tfstate Version 4 JSON,提取顶层结构"""
    if "\n" in path_or_str or path_or_str.startswith("{"):
        # 直接吃字符串(demo 用)
        try:
            text = open(path_or_str).read()
        except OSError:
            text = path_or_str
    else:
        text = open(path_or_str).read()
    st = json.loads(text)
    if st.get("version") != 4:
        raise ValueError(f"unsupported state version {st.get('version')}, expected 4")
    return st


def state_resources(st: dict) -> list[dict]:
    """扁平化 state.resources[*].instances[*] 为 {addr, type, name, attrs}"""
    out = []
    for r in st.get("resources", []):
        for inst in r.get("instances", []):
            addr = f"{r['type']}.{r['name']}"
            out.append({
                "addr":  addr,
                "type":  r["type"],
                "name":  r["name"],
                "attrs": inst.get("attributes", {}),
            })
    return out


def state_addr(r: dict) -> str:
    return r["addr"]


# -----------------------------------------------------------------------------
# Diff engine
# -----------------------------------------------------------------------------

CREATE, UPDATE, DESTROY, NOOP = "+", "~", "-", " "


def compute_diff(desired: dict, current: list[dict], include_destroy: bool = True) -> list[tuple]:
    """desired: {addr: attrs_dict};current: state_resources(st) 输出

    返回排序的 [(action, addr, changes)] 列表,
    action: '+' / '~' / '-' / ' '
    changes: dict[field] = (old, new)  或 None(create/destroy)
    """
    diffs = []
    seen = set()

    # 1. Walk desired → create / update
    for addr, want_attrs in desired.items():
        seen.add(addr)
        cur_match = next((c for c in current if state_addr(c) == addr), None)
        if cur_match is None:
            diffs.append((CREATE, addr, {"_all": (None, want_attrs)}))
        else:
            have = cur_match["attrs"]
            # 过滤掉只由 Terraform 自己维护的字段(id/arn)
            managed_fields = {k: v for k, v in want_attrs.items()}
            actual_diffs = {}
            for k, w in managed_fields.items():
                if have.get(k) != w:
                    actual_diffs[k] = (have.get(k), w)
            # 反向检查 want 中没有的字段 has them (drift)
            for k, h in have.items():
                if k not in want_attrs and not k.startswith("_") and k not in ("id", "arn"):
                    actual_diffs[k] = (h, "(unset)")
            if actual_diffs:
                diffs.append((UPDATE, addr, actual_diffs))
            else:
                diffs.append((NOOP, addr, {}))

    # 2. Walk current → destroy
    if include_destroy:
        for c in current:
            addr = state_addr(c)
            if addr not in seen:
                diffs.append((DESTROY, addr, {"_all": (c["attrs"], None)}))
    return diffs


def format_plan(diffs: list[tuple]) -> str:
    """模拟 terraform plan 输出,按 + - ~ 顺序排(terraform 实际 + ~ -)
    顺序:create → update → destroy(实际 terraform 先 + 后 -)
    """
    order = {CREATE: 0, NOOP: 1, UPDATE: 2, DESTROY: 3}
    diffs = sorted(diffs, key=lambda d: (order[d[0]], d[1]))

    lines = []
    creates = sum(1 for d in diffs if d[0] == CREATE)
    updates = sum(1 for d in diffs if d[0] == UPDATE)
    destroys = sum(1 for d in diffs if d[0] == DESTROY)

    for action, addr, changes in diffs:
        if action == NOOP:
            continue
        flag = {CREATE: "+", UPDATE: "~", DESTROY: "-"}[action]
        verb = {CREATE: "create", UPDATE: "update in-place", DESTROY: "destroy"}[action]
        line = f"{flag} {verb:18s} {addr}"
        lines.append(line)
        if changes and "_all" not in changes:
            for k, (old, new) in changes.items():
                lines.append(f"    {flag*1} {k:20s} {old!r:20} → {new!r}")
    summary = f"Plan: {creates} to add, {updates} to change, {destroys} to destroy."
    return "\n".join(lines + ["", summary])


# -----------------------------------------------------------------------------
# Demo 1: 全新初始化 (create-only)
# -----------------------------------------------------------------------------

SAMPLE_STATE_A = json.dumps({
    "version": 4, "terraform_version": "1.6.0", "serial": 0,
    "lineage": "00000000-0000-0000-0000-000000000001",
    "resources": [],
})


def demo_create_only():
    """state 为空,desired 有 3 个新资源 → 全部 create"""
    desired = {
        "aws_vpc.main": {"cidr_block": "10.0.0.0/16", "tags": {"Name": "main-vpc"}},
        "aws_subnet.public": {"vpc_id": "vpc-1234", "cidr_block": "10.0.1.0/24", "availability_zone": "us-east-1a"},
        "aws_instance.web": {"ami": "ami-0c55b159cbfafe1f0", "instance_type": "t3.micro", "subnet_id": "subnet-1234"},
    }
    state = load_state_v4(SAMPLE_STATE_A)
    diffs = compute_diff(desired, state_resources(state))
    print("--- Demo 1: 全新初始化(全部 create) ---")
    print(format_plan(diffs))
    print()


# -----------------------------------------------------------------------------
# Demo 2: drift 检测 (管理字段改动)
# -----------------------------------------------------------------------------

SAMPLE_STATE_B = json.dumps({
    "version": 4, "terraform_version": "1.6.0", "serial": 5,
    "lineage": "00000000-0000-0000-0000-000000000002",
    "resources": [
        {
            "mode": "managed", "type": "aws_instance", "name": "web",
            "provider": 'provider["registry.terraform.io/hashicorp/aws"]',
            "instances": [{
                "schema_version": 1,
                "attributes": {
                    "ami": "ami-0c55b159cbfafe1f0",
                    "instance_type": "t2.micro",  # 漂移到 t2.micro
                    "subnet_id": "subnet-1234",
                    "id": "i-1234567890abcdef0",
                },
            }],
        },
        {
            "mode": "managed", "type": "aws_vpc", "name": "main",
            "provider": 'provider["registry.terraform.io/hashicorp/aws"]',
            "instances": [{
                "schema_version": 0,
                "attributes": {"cidr_block": "10.0.0.0/16", "id": "vpc-1234"},
            }],
        },
    ],
})


def demo_drift():
    """state 中 instance_type = t2.micro,desired 期望 t3.micro → update in-place"""
    desired = {
        "aws_vpc.main":      {"cidr_block": "10.0.0.0/16"},
        "aws_instance.web":  {"ami": "ami-0c55b159cbfafe1f0",
                               "instance_type": "t3.micro",  # 期望
                               "subnet_id": "subnet-1234"},
    }
    state = load_state_v4(SAMPLE_STATE_B)
    diffs = compute_diff(desired, state_resources(state))
    print("--- Demo 2: drift 检测 (instance_type 漂移) ---")
    print(format_plan(diffs))
    print()


# -----------------------------------------------------------------------------
# Demo 3: 删资源 (destroy)
# -----------------------------------------------------------------------------

def demo_destroy():
    """desired 只有 vpc,state 还有 instance → instance 应 destroy"""
    desired = {"aws_vpc.main": {"cidr_block": "10.0.0.0/16"}}
    state = load_state_v4(SAMPLE_STATE_B)
    diffs = compute_diff(desired, state_resources(state))
    print("--- Demo 3: 部分删除 (instance 应 destroy) ---")
    print(format_plan(diffs))
    print()


# -----------------------------------------------------------------------------
# Demo 4: noop(完全一致)
# -----------------------------------------------------------------------------

def demo_noop():
    """desired 等于 state,所有资源 noop(报告 0 changes)"""
    desired = {
        "aws_instance.web":  {"ami": "ami-x", "instance_type": "t2.micro", "subnet_id": "subnet-1"},
        "aws_vpc.main":      {"cidr_block": "10.0.0.0/16"},
    }
    # 构造 state 让 attrs 与 desired 一致(忽略 id)
    state_raw = {
        "version": 4, "terraform_version": "1.6.0", "serial": 99,
        "lineage": "uuid-3", "resources": [
            {"mode": "managed", "type": "aws_instance", "name": "web",
             "provider": 'provider["registry.terraform.io/hashicorp/aws"]',
             "instances": [{"schema_version": 1,
                            "attributes": {**desired["aws_instance.web"], "id": "i-existing"}}]},
            {"mode": "managed", "type": "aws_vpc", "name": "main",
             "provider": 'provider["registry.terraform.io/hashicorp/aws"]',
             "instances": [{"schema_version": 0,
                            "attributes": {**desired["aws_vpc.main"], "id": "vpc-existing"}}]},
        ],
    }
    diffs = compute_diff(desired, state_resources(state_raw))
    print("--- Demo 4: 完全一致 (全部 noop,0 changes) ---")
    print(format_plan(diffs))
    print()


# -----------------------------------------------------------------------------
# main
# -----------------------------------------------------------------------------

def main():
    print("=== Terraform State diff & Plan demo ===\n")
    demo_create_only()
    demo_drift()
    demo_destroy()
    demo_noop()


if __name__ == "__main__":
    main()

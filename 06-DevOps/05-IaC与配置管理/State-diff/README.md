# Terraform State diff & Plan 输出生成

## 简介

Terraform state 是 IaC 工具链的"single source of truth":本地或远程后端(S3/Consul/Terraform Cloud)存的 JSON 文件,记录了实际部署的资源与其属性。本 demo 实现 Terraform 的 `terraform plan` 核心机制——对照 desired config 与 state,生成 **+create / ~update / -destroy** 三类操作清单,这是"drift detection"和"safe apply"的人机交互基础。

Terraform state-cli 教程(developer.hashicorp.com/terraform/tutorials/cli/state-cli):
> "Terraform stores information about your infrastructure in a state file. This state file keeps track of resources created by your configuration and maps them to real-world resources. Terraform compares your configuration with the state file and your existing infrastructure to create plans and make changes to your infrastructure."

## 原理详解

### State Version 4 JSON 结构

```jsonc
{
  "version": 4,                       // 版本号,只支持 4
  "terraform_version": "1.6.0",       // 写入工具版本(不可变原则)
  "serial": 12,                        // 内部计数器(每次 state 写入 +1)
  "lineage": "uuid",                  // 单调递增 run 标识(UUID v4)
  "outputs": {},
  "resources": [
    {
      "mode": "managed",              // managed / data
      "type": "aws_instance",
      "name": "web",
      "provider": "provider[\"registry.terraform.io/hashicorp/aws\"]",
      "instances": [
        {
          "schema_version": 1,
          "attributes": {
            "ami": "ami-0c55b159cbfafe1f0",
            "instance_type": "t3.micro",
            "id": "i-1234567890abcdef0",
            "subnet_id": "subnet-1234"
          }
        }
      ]
    }
  ]
}
```

trendmicro 与 nilebits 文章都指出"version 4"是 Terraform 0.13+ 的现行结构,1.0+ 必填。State 文件包含**所有**被 Terraform 管理的资源含真实云端 id(`i-xxx`、`vpc-yyy` 等)。

### Drift Detection

drift-detection 教程(developer.hashicorp.com/terraform/tutorials/state/resource-drift):
> "Terraform will attempt to reconcile your infrastructure, which may unintentionally destroy or recreate resources."

工作流:
1. 用户在 AWS 控制台手工改了 resource(如改 `instance_type`)
2. 下次 `terraform plan` 触发 refresh(默认开启)→ Provider 读最新云端实际属性 → 写入 state in-memory
3. config 期望与 state attribute diff → 输出 `~ update in-place` 或 `~ update` (`+/-/~` 复合操作)
4. 用户确认 → apply 重新 reconcile

本 demo 把 desired config 视作"用户编辑过的版本",直接与 state 对比生成 plan(不模拟 refresh)。

### 三种 Action 的语义

| Symbol | 含义 | terraform plan 输出格式 |
| --- | --- | --- |
| `+` | create | `+ resource "TYPE" "NAME" { ... }` |
| `~` | update in-place | `~ resource "TYPE" "NAME" { attr = "old" → "new" }` |
| `-` | destroy | `- resource "TYPE" "NAME"` |
| `-/+` | replace (destroy 后 create) | 复合操作,本 demo 不演示 |

注:替换(replace)发生时,Terraform 把 destroy + create 拆为两个独立 plan step,中间状态是临时资源不存在。

### Diff 算法

`compute_diff(desired, current)`:

```
for addr in desired:        # create / update / noop
    if addr not in current:  → CREATE
    else:                     # 字段级 diff
        for k, v in desired[addr]:
            if current[addr][k] != v: → 加 changes
        if changes:  → UPDATE
        else:        → NOOP

for c in current:            # destroy
    if c.addr not in desired: → DESTROY
```

匹配字段时过滤系统维护字段(id/arn/self_link/tags_all),因为云端分配资源 ID 不可预定。

### serial/lineage 字段

`serial` 每次 state 写入 +1;Terraform 内部用它来 detect 并发 edit 冲突(类似乐观锁)。`lineage` 是 UUID,标记整个 state lineage——某些状态下(如 `terraform import`)保留 lineage 同时 serial 跳。本 demo 不演示这两字段的复杂性,只展示核心 diff。

## 对比

| 工具 | 用途 | 输出 |
| --- | --- | --- |
| `terraform plan` | HashiCorp 一手 | plan file + stdout |
| `terraform show -json` | 机器可读 | JSON plan |
| Atlantis / Spacelift | 协作 review plan | PR 评论 + plan output |
| 本 demo | 教学 | 文本 + 计数 |

## 环境准备

- C:GCC 9+(`-Wall -Wextra -std=c99`)
- Python:3.8+(仅标准库)
- Go:1.18+

## 运行方式

```bash
# C
gcc -O2 -Wall -Wextra -std=c99 c/state_diff.c -o state_diff
./state_diff

# Python
python3 python/state_diff.py

# Go
go run go/state_diff.go
```

每版演示 4 个场景:全新初始化 / drift 检测 / 部分删除 / 完全 noop。

## 关键代码片段

Python diff 主循环(`python/state_diff.py`):

```python
def compute_diff(desired, current, include_destroy=True):
    diffs = []
    seen = set()
    for addr, want_attrs in desired.items():
        seen.add(addr)
        cur_match = next((c for c in current if state_addr(c) == addr), None)
        if cur_match is None:
            diffs.append((CREATE, addr, {"_all": (None, want_attrs)}))
        else:
            have = cur_match["attrs"]
            actual_diffs = {}
            for k, w in want_attrs.items():
                if have.get(k) != w:
                    actual_diffs[k] = (have.get(k), w)
            if actual_diffs:
                diffs.append((UPDATE, addr, actual_diffs))
            else:
                diffs.append((NOOP, addr, {}))
    if include_destroy:
        for c in current:
            if state_addr(c) not in seen:
                diffs.append((DESTROY, state_addr(c), ...))
    return diffs
```

Go 版用 sort.SliceStable 模拟 terraform 按 `+ ~ -` 顺序输出 plan(实测 terraform CLI 输出顺序为 create → in-place updates → destroys),state v4 用结构体反序列化。

## 性能与边界

- 时间 **O(D × C × F)**(D = desired count, C = current count, F = fields per resource),生产 Terraform 用 map lookup O(1)
- 本 demo C 版手写 JSON 解析器只覆盖 demo 字段,不解析 `nested object`
- 不演示:`-/+` replace 复合操作、`data` resources、provider 配置漂移、remote backend 读写
- `terraform refresh` 步骤本 demo 略过(假设 state 已经反映当前 cloud 状态)

## 注意事项与常见坑

- **不要手工编辑 state**:`trendmicro` 原文"the state file isn't intended to be directly manually modified, as this will tamper with its condition and might corrupt it. Instead, to get a clearer view of the state's contents, run the `terraform show` command".要改 state 用 `terraform state mv`/`state rm`/`import` 等命令
- **state 不要 commit 到 Git**:含敏感信息(密码/密钥/内部 IP)。本 demo 例子里 `id` 字段就是云端分配的,泄露可能扫到云账号
- **drift 处理的影响**:trendmicro:"if state and configuration do not match your infrastructure, Terraform will attempt to reconcile your infrastructure, which may unintentionally destroy or recreate resources"——reconcile 是单向同步,可能把 AWS 控制台手工改成 Terraform 默认值 → 数据丢失
- **state 并发冲突**:`.terraform.tfstate.lock.info` 文件 + DynamoDB/Cosmos 等 backend lock——两个 engineer 同时 apply 时第二个拿到锁失败而非双写
- **plan ≠ apply 的语义差**:plan 是只读 + output,但 Terraform 输出中 `--detailed-exitcode` 让 plan 也能在 CI 中根据 result 决定是否 apply(0 = no changes, 1 = error, 2 = changes pending)

## 参考资料(实际阅读过的权威来源)

- [Manage resources in Terraform state](https://developer.hashicorp.com/terraform/tutorials/cli/state-cli) — 全文阅读:state 文件是 config ↔ real resource 映射,`terraform apply` 流程,state-cli 子命令引入
- [Manage resource drift](https://developer.hashicorp.com/terraform/tutorials/state/resource-drift) — 全文阅读:drift 概念 + `--refresh-only` 标志 + ec2/sg 实例引入漂移完整 demo + 验证步骤
- [Terraform State Management (earezki)](https://www.earezki.com/ai-news/2026-03-21-terraform-state-the-one-file-you-cant-afford-to-lose/) — 全文阅读:state 含义(blueprint ↔ actual 映射)+ 远程后端(S3/Consul)的必要性 + `terraform import` 命名最佳实践
- [Terraform Drift Detection and Remediation (Nile Bits)](https://www.nilebits.com/blog/2024/07/terraform-drift-detection) — 全文阅读:state file 示例 JSON version=4 + AWS 控制台 drift 引入 + GitHub Actions 自动 plan 调度
- [Trend Micro: Terraform Drift Detection Strategies](https://www.trendmicro.com/en_my/research/24/c/terraform-tutorial-drift-detection-strategies.html) — 全文阅读:state file JSON 示例 + 25MB Azure VM 状态 + serial/lineage 字段解释 + 常见 drift 原因(浏览器/API/CLI 多入口修改)

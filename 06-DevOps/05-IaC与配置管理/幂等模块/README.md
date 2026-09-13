# Ansible 幂等模块

## 简介

**幂等性 (idempotency)** 是 Ansible 模块设计的核心原则:同一 playbook 跑 N 次,效果与跑一次一致。Ansible 官方 playbooks_intro 原文:"Most Ansible modules check whether the desired final state has already been achieved and exit without performing any actions if that state has been achieved. Repeating the task does not change the final state. Modules that behave this way are 'idempotent'."

**`--check` 模式**(dry-run)官方原文(Testing Strategies 页):"--check mode in Ansible can be used as a layer of testing as well. If running a deployment playbook against an existing system, using the `--check` flag will report if Ansible thinks it would have had to have made any changes to bring the system into a desired state. This can let you know up front if there is any need to deploy onto the given system."

本 demo 用 **Python + Go + JavaScript (Node.js)** 三语言实现三个最常见模块的幂等行为:`file` / `package` / `service`,并验证:
- 第一次执行 = N 次 changed(达到 desired state)
- 第二次执行 = **0 次 changed**(幂等)
- `--check` 模式 = 不修改 host,只报 diff

## 原理详解

### 幂等的两大实现模式

| 模式 | 思路 | 例子 | 风险 |
| --- | --- | --- | --- |
| **Check-then-Act** | 读当前状态 vs 期望状态,只在不同时修改 | `file` 模块检查 mode/owner 后再 chmod | TOCTOU 竞态(读后再写的窗口被外部修改) |
| **Always-Act** | 不论当前如何都执行,API 自身幂等 | `apt-get install -y <pkg>` 重装覆盖,systemd `enable --now` | 依赖底层工具幂等 |

Ansible `apt`/`yum`/`file`/`service` 等模块采用前一模式,`command`/`shell` 默认不幂等(用 `creates` 参数辅助)。

### `file` 模块 desired-state 检查逻辑

```python
def module_file(host, params, check_mode=False):
    cur = host.stat_file(path)
    if state == "absent" and cur is not None:
        return changed("file removed")
    if cur is None:
        return changed("file created")  # 全新创建
    # 逐字段比对 mode/owner/group/content
    diffs = {k: v for k, v in desired.items() if cur[k] != v}
    return changed("file updated") if diffs else ok("already in desired state")
```

本 demo 的 Python 版逐字段 diff:C/Python/JS 三个实现均暴露 `diff.{before,after}` 字典。对应 `ansible-playbook --diff` 的输出格式。

### `package` 模块

- `state=present`:若 installed → ok;否则 installed → changed,装
- `state=absent`:镜像

`--check` 模式默认仍尝试 `apt-get update --simulate` 类调用(`apt` 模块的 `check_mode: full` 支持);本 demo 简化。

### `service` 模块

- `state=started`:期望 running=True,比对 → 必要时 `systemctl start`
- `enabled`:独立开关,可选

`--diff` 在 service 模块下不会输出文件 diff,而是输出属性 diff(用户/组/tag 之类)。

### result 字典标准字段

Ansible 官方规范:

| Key | 含义 |
| --- | --- |
| `changed` | bool,本次是否真发生修改 |
| `failed` | bool,任务失败(达到 should_fail 才为 True) |
| `msg` | str,人类可读消息 |
| `diff` | dict `before/after`,仅 `--diff` 模式下填(本 demo 始终填以演示) |
| `invocation` | 模块调用参数(deprecated,新版用 `module_args`) |

`ok` vs `changed` vs `failed` 是 Ansible 控制流的三个核心状态——`handlers` 只在 `changed` 时触发,`failed` 让 `block/rescue/always` 错误处理激活。

## 对比

| 维度 | Ansible `file` | Ansible `command` | 本 demo |
| --- | --- | --- | --- |
| 幂等 | ✓(check-then-act) | ✗(除非 `creates`/`removes`) | ✓ |
| `--check` 支持 | ✓(full) | ✗(默认 skip) | ✓ |
| `diff` 字段 | ✓ | — | ✓ |
| 字节级 diff | ✓(`--diff` 输出 unified) | — | 仅字段级 |

## 环境准备

- Python:3.8+(仅标准库)
- Go:1.18+
- Node.js:14+

## 运行方式

```bash
# Python
python3 python/idem_modules.py

# Go
go run go/idem_modules.go

# Node.js
node js/idem_modules.js
```

预期输出:三次执行,第一遍 changed 计数 > 0,后两遍 = 0;`check_mode` 模式 host 状态未变。

## 关键代码片段

Python 幂等 file 模块核心判断(`python/idem_modules.py`):

```python
# 已存在: 逐字段比对, 任一差异 → changed=True
diffs = {}
if desired_mode is not None and cur["mode"] != desired_mode:
    diffs["mode"] = {"before": cur["mode"], "after": desired_mode}
if desired_owner is not None and cur["owner"] != desired_owner:
    diffs["owner"] = {"before": cur["owner"], "after": desired_owner}
if diffs:
    result["changed"] = True
    result["diff"] = {"before": {k: v["before"] for k, v in diffs.items()},
                       "after":  {k: v["after"]  for k, v in diffs.items()}}
    if not check_mode:
        new = dict(cur)
        new.update({k: v["after"] for k, v in diffs.items()})
        host.write_file(path, **new)
else:
    result["changed"] = False
    result["msg"] = "file already in desired state"
```

## 性能与边界

- check-then-act 受 **TOCTOU** 影响:check 与 act 之间可能被外部修改。Ansible 默认假设任务串行执行(默认 forks=5),TOCTOU 窗口小但非零
- 本 demo **只演示 desired-state 检查**,不演示 `diff_mode` 输出统一 diff(文件字节级 diff 需要 mmap + 比较)
- `service` 模块底层用 `systemctl is-active` 等命令,本 demo 简化为内存字典
- `package` 在 Windows 与 macOS 上对应不同 manager(winget/choco/brew),本 demo 仅 Linux 风格

## 注意事项与常见坑

- **`command` / `shell` 默认不幂等**:Ansible docs 原文(ssdnodes):"ansible.builtin.command and ansible.builtin.shell have no idea what your command does."——必须用 `creates`/`removes` 显式给幂等条件,或改用专门的 module(apt/copy/template)
- **`--check` 在新主机上效果不佳**:ssdnodes:"check mode is accurate against a host the playbook has already converged, and noisy against a fresh one. A --check run where every task reports ok is a real statement about a converged host, because it means nothing would change. On a brand new host, --check mostly tells you the host is new."
- **TOCTOU**:实际生产中 check 与 act 之间另一进程可能改了文件,Ansible 假设任务线性且 forks 较小
- **`changed` ≠ "真改了"**:本 demo 严格区别 changed 计划与实际应用——`--check` 模式 changed=True 但不动;host_diff 报告可能与实际 API 调用有出入(如 AWS API 因 IAM 权限被拒,`apply` 后实际 unchanged 但 module 报 changed=True)
- **service 模块 `does not exist`**:本 demo host 没注册 service 时直接 `failed=True`,实际生产 systemd `systemctl is-enabled <name>` 返回 1 是 not found

## 参考资料(实际阅读过的权威来源)

- [Ansible playbooks_intro 官方文档](https://docs.ansible.com/ansible/devel/playbooks_intro.html) — 全文阅读:原文 "Most Ansible modules check whether the desired final state has already been achieved and exit without performing any actions..." + check mode 描述
- [Ansible Testing Strategies 官方文档](https://docs.ansible.com/projects/ansible/latest/reference_appendices/test_strategies.html) — 全文阅读:"Check Mode As A Drift Test" 用 `--check` 当漂移测试
- [ssdnodes: Ansible check mode and --diff dry runs](https://www.ssdnodes.com/learn/ansible-check-mode-dry-run) — 全文阅读:"command and shell have no idea what your command does" + 新主机 `--check` 失败的根因 + four result words (ok/changed/skipping/fatal)
- [goLinuxCloud: Ansible Modules, ansible-doc, and Collections](https://www.golinuxcloud.com/ansible-modules-ansible-doc-collections/) — 全文阅读:state 参数语义 + check_mode/diff_mode/idempotency 三 attribute + Collections 概念

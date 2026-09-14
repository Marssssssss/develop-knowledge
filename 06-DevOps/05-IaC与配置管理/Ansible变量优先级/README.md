# Ansible 变量优先级

## 简介

Ansible 允许在**几十个不同的地方**定义同名变量：inventory、group_vars/host_vars、role 的 `defaults/` 与 `vars/`、play 的 `vars`/`vars_files`/`vars_prompt`、block/task 级 `vars`、`include_vars`、`set_fact`、role 参数，以及命令行的 `-e`。它的做法不是"报错"，而是**全部加载下来，再按一个固定的 22 级优先级表挑出唯一胜者**。官方原文："Ansible loads every possible variable it finds, and then chooses the variable to apply based on variable precedence."

关键概念：

| 概念 | 一句话解释 |
| --- | --- |
| 优先级（precedence） | 22 个层级的全序，**列表里越靠后越强** |
| extra vars | 第 22 级，`-e` 传入，官方原文"always win precedence" |
| 作用域（scope） | 三档：Global（配置/环境变量/命令行）、Play、Host |
| 归并（merge） | inventory 内部"更具体的覆盖更笼统的"：子组 > 父组，host_var > group_var |
| 同层覆盖 | 同一层级内重复定义，**后加载的赢** |
| `hash_behavior` | 默认 `replace`（整份替换）；`merge` 只做**部分**替换（字典深合并） |

本 demo 用两种语言实现一个**优先级解析器**：把"每个定义来自哪一级"记下来，用同一条 `max(level, 插入序)` 规则解出胜者，并复现官方文档里的三条典型结论。

## 原理详解

### 1. 完整的 22 级优先级表（由弱到强，官方顺序）

| # | 来源 | # | 来源 |
| --- | --- | --- | --- |
| 01 | 命令行取值（`-u my_user`，**不是变量**） | 12 | Play `vars` |
| 02 | Role defaults（`roles/x/defaults/`） | 13 | Play `vars_prompt` |
| 03 | inventory 文件 / 脚本中的 group vars | 14 | Play `vars_files` |
| 04 | Inventory `group_vars/all` | 15 | Role vars（`roles/x/vars/`） |
| 05 | Playbook `group_vars/all` | 16 | Block vars（仅该 block 内任务） |
| 06 | Inventory `group_vars/*` | 17 | Task vars（仅该任务） |
| 07 | Playbook `group_vars/*` | 18 | `include_vars` |
| 08 | inventory 文件 / 脚本中的 host vars | 19 | Registered vars 与 `set_fact` |
| 09 | Inventory `host_vars/*` | 20 | Role（及 `include_role`）params |
| 10 | Playbook `host_vars/*` | 21 | `include` params |
| 11 | Host facts 与被缓存的 `set_fact` | 22 | **Extra vars（`-e`），永远胜出** |

> 第 01 项是**占位**：命令行上的 `-u` 是"行为参数"，不是变量。真正能压过一切的是第 22 级。

### 2. 解析规则

```text
对每个变量名：
  cands = 所有已知定义，每个带 (level, load_seq)
  winner = max(level, load_seq)      # 先比层级，层级相同比加载顺序
```

两条推论：

- **层级压倒顺序**：`host_vars` 里的值永远打得过 `group_vars` 里的值，哪怕 group_vars 后加载。
- **同层级后写赢**：官方原文"If multiple groups have the same variable, the last one loaded wins"。

### 3. inventory 内部归并：更具体者胜

inventory 层不是一个平坦的层级，而是一棵树：所有组都是 `all` 的子组。

```text
all (group_vars/all)              depth 0
 └── boston (group_vars/boston)   depth 1
      └── xyz.boston.example.com (host_vars/...)  depth 2
```

官方建议的用法正是如此：站点级默认值放 `group_vars/all`，地域级放 `group_vars/<location>`，个别机器放 `host_vars/<fqdn>`。本 demo 把 depth 折算成层级上的**小数偏移**（0.1），于是"越具体越强"和"层级越高越强"由同一个 `max` 规则统一处理，与加载顺序无关。

### 4. `hash_behavior`：整份替换 vs 部分替换

官方提醒：默认配置是 `hash_behavior=replace`，改成 `merge` 之后"只做部分覆盖"。当变量值是字典时差异很大：

```yaml
# group_vars/all        # host_vars/web
nginx:                  nginx:
  worker_processes: 2     worker_processes: 8
  keepalive_timeout: 65
```

| 模式 | 结果 | 含义 |
| --- | --- | --- |
| `replace`（默认） | `{nginx: {worker_processes: 8}}` | `keepalive_timeout` **被丢掉** |
| `merge` | `{nginx: {worker_processes: 8, keepalive_timeout: 65}}` | 深合并，只覆盖同名键 |

## 环境准备

- 操作系统：Linux / macOS / Windows（只用标准库）
- Python：3.10+（本 demo 用 `3.13`）
- Go：1.21+

## 运行方式

### Python

```bash
cd python && python3 var_precedence.py
```

### Go

```bash
cd go && go run var_precedence.go
```

## 关键代码片段

```python
def resolve(self, name, default=None):
    """Highest level wins; ties go to the definition loaded last."""
    candidates = self._candidates(name)          # 每个定义带 (level, seq)
    if not candidates:
        return default
    return max(candidates, key=lambda d: (d.level, d.seq)).value


def add_inventory_var(self, name, depth, source, value):
    """depth 0 = 组 all；每深一层更具体一层 -> 折算成层级偏移。"""
    level = self.INVENTORY_BASE + depth * self.DEPTH_STEP
    self._inventory.setdefault(name, []).append(Definition(level, source, value, 0))
```

## 性能与边界

- 解析复杂度：设某变量有 `k` 个定义，单次解析 O(k)；一次 play 的变量表规模在千级，开销可忽略。
- 边界：优先级表**只有 22 级**，没有"第 23 级"；遇到跨 play 的意外覆盖，唯一可靠的排查手段是 `ansible-inventory --list` / `debug` 打印 + 按上表逐层排除。
- 平台差异：`hash_behavior` 是**全局配置项**（`ansible.cfg` 的 `[defaults] hash_behaviour`），不是 per-play 的——一旦改成 `merge`，全项目行为一起变。

## 注意事项与常见坑

1. **不要靠 `-e` 修生产问题**。`-e` 永远赢，很容易把"配置错了"掩盖成"临时覆盖了一下"，下次不带 `-e` 跑就炸。官方建议是"选一处定义、保持简单"。
2. **同名变量分散在多处 = 事故源头**。官方原文：团队约定好"哪类变量放哪里"就能完全回避优先级问题。
3. **`group_vars/all` 是全局变量，容易被误伤**。它是所有组的父组，任何更深层的组都能覆盖它，但反过来它也覆盖"没有显式定义"的一切主机。
4. **role `defaults/` 与 `vars/` 语义相反**：`defaults/` 设计成"最容易被覆盖"（第 02 级）；`vars/` 是"这个角色一定要用这个值"（第 15 级），官方明确说放进 `vars/` 会让使用者更难覆盖。
5. **`hash_behavior` 默认 `replace` 是"静默丢键"**。写 `nginx.keepalive_timeout` 却不生效时，先怀疑是不是被更高层级的 `nginx` 字典整份替换掉了。
6. **第 01 级不是变量**。命令行 `-u user` 是连接参数，它和第 22 级 `-e user=...` 完全不同，前者不会出现在变量表里。
7. **facts 与 `set_fact` 是两级**：普通 host facts / 被缓存的 `set_fact` 在第 11 级；同一次 play 里 `set_fact` 新产生的值在第 19 级，因此能覆盖第 11~18 级的所有东西。

## 参考资料（实际阅读过的权威来源）

- [Using variables — Ansible Community Documentation](https://docs.ansible.com/ansible/latest/playbook_guide/playbooks_variables.html) — 22 级优先级完整有序列表（含脚注：role defaults 的可见性、vars 插件、cacheable `set_fact` 的层级差异）、"the last listed variables override all other variables"、"If multiple groups have the same variable, the last one loaded wins"、`hash_behavior=replace` 与 `merge` 的差异、Global/Play/Host 三档作用域、`group_vars/all` → `group_vars/<location>` → `host_vars/<fqdn>` 的官方示例（全文阅读）

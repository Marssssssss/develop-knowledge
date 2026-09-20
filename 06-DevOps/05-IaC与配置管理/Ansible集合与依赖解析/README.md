# Ansible 集合与依赖解析 (Demo 465)

> 集合（Collection）是 Ansible 的**分发格式**。它带来的核心新概念是 FQCN，以及一套「短名怎么被解析成 FQCN」的作用域规则 ——
> 这套规则最容易踩的坑是：**角色不继承 playbook 的 `collections`**。本 demo 用 Python + Go 双实现复现解析与元数据判定。

## 一、简介

官方（Developing collections）：*"Collections are a distribution format for Ansible content. You can package and distribute playbooks, roles, modules, and plugins using collections."*

一条集合名 = `命名空间.集合名.内容名`，即 **FQCN**（fully qualified collection name）。命名空间由 `galaxy.yml` 定义，官方特别提醒：*"The Galaxy namespace of an Ansible collection is defined in the `galaxy.yml` file. It **can be different** from the GitHub organization or repository name."*

## 二、原理详解

### 2.1 `collections:` 只是「有序搜索路径」，不是安装

原文：*"The `collections` keyword merely creates an ordered 'search path' for non-namespaced plugins and role references. It **does not install content** or otherwise change Ansible's behavior around the loading of plugins or roles."*

两个要点：

1. **有序**：谁写在前面先找谁（自检 `B1`/`B2`）。
2. **只覆盖一部分插件类型**。原文：*"Note that an FQCN is still required for **non-action or module plugins** (for example, lookups, filters, and tests)."* —— 所以 `lookup`/`filter`/`test` 即使集合在搜索路径里，短名也解析不出来（`C1`~`C3`）。

### 2.2 角色不继承 playbook 的 collections（本 demo 重点）

原文：*"If your playbook uses both the `collections` keyword and one or more roles, the roles **do not inherit** the collections set by the playbook."*

以及更严格的一句：*"any roles you call in your playbook define their own collections search order; they do not inherit the calling playbook's settings. **This is true even if the role does not define its own** `collections`."*

也就是说：角色内部解析**只看角色自己的列表**；角色没写 `collections` 时，playbook 的列表也不会补位（`D1`）。官方因此 "*recommend you always use FQCN*"。

另外原文：*"Ansible will use the collections list defined inside the role even if the playbook that calls the role defines different collections."*

### 2.3 module_utils 的导入约定

原文：*"When coding with `module_utils` in a collection, the Python `import` statement needs to take into account the FQCN along with the `ansible_collections` convention."*

```python
from ansible_collections.{namespace}.{collection}.plugins.module_utils.{util} import {something}
```

注意目录是 `plugins/module_utils`，不是顶层 `module_utils`（`E1`）。

### 2.4 meta/runtime.yml：`requires_ansible` 与「预发布截断」

官方：*"The version of Ansible Core (ansible-core) required to use the collection. Multiple versions can be separated with a comma."*

最关键的一句：*"although the version is a PEP440 Version Specifier under the hood, **Ansible deviates from PEP440 behavior by truncating prerelease segments** from the Ansible version. This means that Ansible **2.11.0b1 is compatible** with something that `requires_ansible: '>=2.11'`."*

按标准 PEP440，`2.11.0b1 < 2.11` 会**不**满足 `>=2.11`；Ansible 把预发布段切掉变成 `2.11.0` 再比，于是满足。自检 `F1`、`F6`（`rc` 同理）专门钉住这条。

### 2.5 plugin_routing：redirect / deprecation / tombstone

原文：*"To define a new location for a plugin, set the `redirect` field to another name. To deprecate a plugin, use the `deprecation` field to provide a custom warning message and the removal version or date. If the plugin has been renamed or moved to a new location, the `redirect` field **should also be provided**. If a plugin is being removed entirely, `tombstone` can be used for the fatal error message and removal version or date."*

| 动作 | 语义 | 是否还能用 |
| --- | --- | --- |
| `redirect` | 改从另一处加载 | 能 |
| `deprecation` | 自定义警告 + 移除版本/日期 | 能（通常配 `redirect`） |
| `tombstone` | 整体移除，致命错误 + `warning_text` + `removal_version` | **不能** |

### 2.6 集合里的 playbook 命名

原文：*"the `-` character is not valid for playbook names in collections"* —— 集合内 playbook 名不能用连字符（`A5`）。调用方式：`ansible-playbook my_namespace.my_collection.playbook1`。

## 三、对比

| 写法 | 是否受 `collections:` 影响 | 说明 |
| --- | --- | --- |
| 模块 / action 短名 | ✅ | 按搜索路径顺序 |
| 角色引用短名 | ✅ | 同上 |
| lookup / filter / test 短名 | ❌ | 必须 FQCN |
| 任意 FQCN | ❌ | 直接定位，与搜索路径无关 |
| 角色内部的短名 | ❌ | 只看角色自己的列表 |

| runtime.yml 字段 | 作用 |
| --- | --- |
| `requires_ansible` | 声明所需 ansible-core 版本（PEP440 + 预发布截断） |
| `plugin_routing.<type>.<name>.redirect` | 改指向 |
| `plugin_routing.<type>.<name>.deprecation` | 弃用警告 |
| `plugin_routing.<type>.<name>.tombstone` | 彻底移除（致命） |

## 四、环境与运行

```bash
python selfcheck_anscoll.py     # 30 项断言，纯标准库
go run .                         # Go 版同口径（本机无 Go 工具链，走人工审查 + 静态检查）
```

## 五、关键代码

```python
resolve_plugin("ping", ["my_ns.my_coll", "other.coll"], AVAIL)      # 命中第一个
resolve_plugin("lookup_x", ["my_ns.my_coll"], AVAIL, ptype="lookup")  # requires_fqcn
resolve_in_role("ping", ["my_ns.my_coll"], [], AVAIL)               # not_found：角色不继承
requires_ansible_ok(">=2.11", "2.11.0b1")                            # True：预发布被截断
route_plugin(rt, "inventory", "my_inventory")                        # tombstone → 致命
```

## 六、性能边界

- 解析是 O(搜索路径长度)，通常 <10 个集合
- `requires_ansible` 解析是 O(说明符条数)，每次集合加载一次，可忽略
- 真实的开销在集合的**文件系统扫描**与 Python 模块导入，不在本模型的判定逻辑

## 七、注意事项与常见坑

1. **角色不继承 playbook 的 `collections`** —— 这是官方反复强调、也最常被忽略的一条；写可复用角色请一律用 FQCN。
2. **`collections:` 不会安装任何东西**，它只影响解析；没装就是找不到。
3. **lookup/filter/test 必须写 FQCN**，即使集合在搜索路径里。
4. **`requires_ansible` 的预发布行为与标准 PEP440 不同**，写约束时别照搬 Python 包的直觉。
5. **集合命名空间不等于 GitHub 组织名**，重命名仓库不会自动改 `galaxy.yml`。
6. `vars` 插件*"are not loaded automatically, and always require being explicitly enabled by using their fully qualified collection name"*；`cache` 插件*"are not supported for inventory plugins"*。
7. 本模型不解析真实 YAML、不模拟 `ansible-galaxy` 的安装与依赖求解（requirements.yml 的递归求解另论）。

## 八、参考资料（均为本轮实际抓取并阅读）

- Ansible《Developing collections》—— 集合作为分发格式、命名空间、发布流程
  https://docs.ansible.com/ansible/latest/dev_guide/developing_collections.html
- Ansible《Collection structure》—— `galaxy.yml`、`module_utils` 导入约定、`meta/runtime.yml` 的 `requires_ansible` 与 `plugin_routing`、vars/cache 插件限制
  https://docs.ansible.com/ansible/latest/dev_guide/developing_collections_structure.html
- Ansible《Using collections in a playbook》—— `collections:` 是有序搜索路径、角色不继承、非 action/module 必须 FQCN、集合内 playbook 命名限制
  https://docs.ansible.com/ansible/latest/collections_guide/collections_using_playbooks.html
- Ansible《Using Ansible collections》（安装与 requirements.yml）
  https://docs.ansible.com/ansible/latest/collections_guide/collections_installing.html
- Ansible《Galaxy user guide》—— requirements.yml 中声明集合与角色依赖
  https://docs.ansible.com/ansible/latest/galaxy/user_guide.html

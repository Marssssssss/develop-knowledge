# Terraform State 锁与远程后端

## 简介

Terraform 把基础设施的"真相"存在 **state 文件**里。任何人执行 `plan` / `apply` / `destroy` 都会读改写它，因此**写状态的操作必须互斥**——Terraform 官方的说法是：只要后端支持，Terraform 会自动为所有可能写 state 的操作加锁；**加锁失败时 Terraform 不会继续执行**。本 demo 用三种语言复刻这套锁协议：远程后端的**路径布局**、**条件写（create-only）**语义、**lock ID 作为 nonce** 的强制解锁，以及崩溃残留锁的按龄清理。

关键概念：

| 概念 | 一句话解释 |
| --- | --- |
| state | Terraform 记录的"已创建资源"映射表，官方称其为 source of truth |
| 后端（backend） | state 的存放位置：local / s3 / gcs / azurerm / consul… |
| 条件写 | "仅当对象不存在时才写入"，加锁的全部秘密 |
| lock ID | 加锁时生成的 UUID，`force-unlock` 时充当 nonce |
| `.tflock` | S3 后端 `use_lockfile = true` 时锁对象的后缀 |
| `LockID` | DynamoDB 锁表的 String 型分区键（该机制已废弃） |

历史背景：早期 Terraform 用 **DynamoDB** 做锁表（String 型分区键 `LockID` + 条件 `PutItem`）；因为"为一个 booleans 拉一张 DynamoDB 表"过重，官方后来改为在**同一个 S3 桶**里写一个 `<key>.tflock` 对象，两者共享同一套"条件写"语义。

## 原理详解

### 1. 一次 `apply` 的加锁时序

```
terraform apply
   │
   ├─(1) 解析 backend 配置 → 算出 state 对象路径
   ├─(2) 条件写锁对象  ── 成功 ──▶ 继续：refresh → plan → apply → 写 state
   │                     └─ 失败 ──▶ 打印 Lock Info 并**退出**（不继续）
   ├─(3) 写回新 state
   └─(4) 删除锁对象（Release，compare-and-delete）
```

### 2. 路径布局（S3 后端）

官方文档规定：默认 workspace 的 state 直接落在 `key`；**非默认 workspace** 落在 `<workspace_key_prefix>/<workspace_name>/<key>`，前缀默认是 `env:`。

```text
bucket = "mybucket"  key = "path/to/my/key"
default    → path/to/my/key
production → env:/production/path/to/my/key
锁         → <上述路径>.tflock      (use_lockfile = true)
```

### 3. "条件写"是加锁的全部

- **S3**：`PutObject` 带 `If-None-Match: *`——对象已存在则返回 412，不会覆盖。
- **DynamoDB**（已废弃）：`PutItem` 带 `ConditionExpression = attribute_not_exists(LockID)`——已存在则抛 `ConditionalCheckFailedException`。

本 demo 的 `put_if_absent` 就是这两者的抽象。**关键性质：这把锁没有 TTL**（见"常见坑"）。

### 4. lock ID 是 nonce

加锁时**先生成** UUID，再把它写进锁对象；失败时报出的 ID 就是持有者的 ID。`terraform force-unlock <ID>` 要求传入的字符串与锁对象里的 ID **逐字符相等**：

```text
force-unlock 6a1c...  → 与锁对象 ID 比对 → 不等则拒绝
```

Comparisons and unlocks therefore target the *correct* lock：官方文档明确把 lock ID 类比为密码学 nonce，用来防止"解锁了别人的锁"。本 demo 中错误 nonce 会被拒绝，正确 nonce 才会删除锁对象。

### 5. 锁对象里存了什么

`Lock Info` 至少包含 `ID` / `Operation`（plan/apply/destroy）/ `Who`（谁在跑）/ `Version`（Terraform 版本）/ `Created`（何时开始）/ `Path`。这些字段是排查"到底谁锁住了"的唯一线索，因此 demo 里按这个结构序列化/反序列化。

## 对比：三种后端的锁能力

| 后端 | 是否支持锁 | 锁载体 | 备注 |
| --- | --- | --- | --- |
| local | 否（仅本地文件互斥） | — | 多人协作会直接互相覆盖 |
| s3 | 是（`use_lockfile = true`） | `<key>.tflock` 对象 | 需 `s3:GetObject/PutObject/DeleteObject` |
| s3 + dynamodb | 是（`dynamodb_table`） | `LockID` 项 | **已废弃**，为兼容旧版本保留 |
| consul / etcd | 是 | KV + session | 需要额外组件 |

## 环境准备

- 操作系统：Linux / macOS / Windows（示例代码只用标准库）
- Python：3.10+（本 demo 用 `3.13`；仅用 `dataclasses`/`json`/`uuid`）
- Go：1.21+（本 demo 只用 `encoding/json`、`time`、`fmt`、`os`）
- C：C99 以上，`gcc -Wall -Wextra -pedantic` 编译干净

## 运行方式

### Python

```bash
cd python && python3 state_lock.py
```

### Go

```bash
cd go && go run state_lock.go
```

### C

```bash
cd c && gcc -O2 -Wall -Wextra -pedantic state_lock.c -o state_lock && ./state_lock
```

## 关键代码片段

```python
def acquire(self, operation, workspace="default"):
    key = self.backend.lock_key(workspace)
    lock_id = str(uuid.uuid4())            # (1) 先造 nonce
    payload = json.dumps({"ID": lock_id, "Operation": operation, ...})
    if not self.store.put_if_absent(key, payload):   # (2) 条件写 = 加锁
        existing = json.loads(self.store.get(key))
        raise LockError(LockInfo(**existing).render())  # (3) 失败即中止
    return lock_id

def release(self, lock_id, workspace="default"):
    raw = self.store.get(self.backend.lock_key(workspace))
    if raw is None or json.loads(raw)["ID"] != lock_id:
        return False           # (4) compare-and-delete：只有持有者能释放
    self.store.delete(self.backend.lock_key(workspace))
    return True
```

## 性能与边界

- 加锁 = 一次条件写请求，量级与普通对象写入相同（S3 `PutObject` / DynamoDB 单写）。**锁不是性能瓶颈**，state 体积才是：state 每次都要整份读入内存并整份写回。
- **无 TTL**：官方没有给锁设置过期时间。一个 `terraform apply` 被 `Ctrl-C` 或断网打断后，锁会一直留着。
- 平台限制：本 demo 的对象存储与锁表都是**进程内模拟**，用于观察协议语义；真实后端需网络凭证。

## 注意事项与常见坑

1. **锁失败要中止，不要 "换个方式绕"**。官方文档说 `-lock=false` 可以关锁但"we do not recommend it"。绕过锁去写同一份 state，结果通常是 state 内容与真实资源互相矛盾。
2. **`force-unlock` 极其危险**。文档原文是"Be very careful with this command."——**只有在自己持有锁而自动解锁失败时**才用；别人正在跑的时候强解，会造成**多个写者并发**。
3. **force-unlock 必须带对 ID**。ID 就是防误伤的闸门；随手 `force-unlock` 一个猜出来的字符串，在后端会被拒绝（本 demo 的 `wrong nonce` 分支）。
4. **DynamoDB 锁已废弃**。新配置应使用 `use_lockfile = true`；官方保留 `dynamodb_table` 只为让老版本能平滑迁移。
5. **S3 桶务必开 Versioning**。官方在该后端文档顶部专门加了警告：版本化是"误删/人为错误后恢复 state"的唯一手段。
6. **state 可能被部分覆盖**。若真的绕过锁并发写，后写的一方会整份覆盖，前面写的内容不会"合并"。
7. **锁是 per-workspace 的**。不同 workspace 是不同对象路径（`env:/<ws>/...`），因此它们**互不阻塞**——这也是本 demo 用 production 与 default 各持一把锁的意义。
8. **权限要精确到锁对象**。`s3:DeleteObject` 不需要给 state 对象（Terraform 不删 state），但必须给 `<key>.tflock`，否则释放锁会失败、留下永久残留。

## 参考资料（实际阅读过的权威来源）

- [State: Locking — HashiCorp Developer](https://developer.hashicorp.com/terraform/language/state/locking) — 加锁自动发生、锁失败即中止、`-lock=false`、force-unlock 需要 lock ID 且该 ID 作为 nonce、后端能力差异（全文阅读）
- [Backend Type: s3 — HashiCorp Developer](https://developer.hashicorp.com/terraform/language/backend/s3) — `<key>` / `<workspace_key_prefix>/<workspace>/<key>` 路径规则、`use_lockfile` 与 `<key>.tflock`、DynamoDB `LockID` String 分区键且 **deprecated**、所需 IAM 权限（含 lock file 的 `s3:DeleteObject`）、桶 Versioning 警告（全文阅读）
- [Nonce (cryptographic) — Wikipedia](https://en.wikipedia.org/wiki/Cryptographic_nonce) — HashiCorp 文档中把 lock ID 类比为 nonce 时所引用的定义

# 内容寻址缓存（hashFiles 键 + restore-keys 回退 + LRU/TTL 淘汰）

## 简介

CI 系统用缓存把"依赖安装"这类重复劳动变成一次性的：`actions/cache` / GitLab CI `cache`
把构建产物存到远端，key 通常由**锁文件内容的哈希**派生（GitHub 的 `hashFiles()`）——内容
不变则 key 不变，命中即跳过安装。这正是**内容寻址（content-addressable）**思想：key 是
内容的函数，而不是名字的函数。本 demo 从零实现一个 CI 缓存引擎，覆盖：

- **键派生**：`key = <scope>-<sha256(lockfile)>`，纯内容寻址
- **精确命中 / 未命中**：hit 恢复产物并跳过构建；miss 则执行构建、job 成功后保存
- **restore-keys 前缀回退**：精确未命中时按前缀顺序找**最近访问**的部分匹配作底
- **淘汰**：容量上限（对应 GitHub 单仓库 5 GB）按 LRU 淘汰；一周未访问也淘汰

关键概念：

| 概念 | 一句话解释 |
| --- | --- |
| cache key | 检索与保存共用的一维标识，≤ 512 字符（GitHub 上限） |
| cache-hit / cache-miss | key 精确匹配即 hit；miss 时 job 成功后自动新建 |
| restore-keys | 有序前缀列表，精确未命中时逐个尝试部分匹配 |
| 内容寻址 | 相同输入内容 ⇒ 相同 key ⇒ 复用输出（memoization） |

历史背景：`actions/cache` 官方文档把"锁文件哈希作 key"列为最常见策略；Bazel 等构建系统
进一步把缓存做成内容寻址的行动缓存（action cache），本 demo 实现的是其核心子集。

## 原理详解

### 1. 键派生（hashFiles 语义）

```
lockfile 内容 (bytes) ──sha256──> 64 hex ──>  key = "npm-<hex>"

            ┌── run #1: lockfile 未变 ──> 同 key ──> HIT ──> 跳过 npm install
内容不变 ────┤
            └── run #2: lockfile 加了新依赖 ──> 新 key ──> MISS ──> 安装 + 保存新缓存
```

GitHub 官方示例：`key: ${{ runner.os }}-build-${{ env.cache-name }}-${{ hashFiles('**/package-lock.json') }}`。

### 2. 查找次序（官方文档原文归纳）

```
1. 精确匹配 key                    ──> hit:恢复文件,cache-hit=true
2. key 的部分匹配(前缀更短的同 key)──> 有则恢复最近的
3. restore-keys 逐个前缀匹配(有序)  ──> 部分匹配:恢复最近创建的,job 成功后仍存新 key
4. 全部未命中                      ──> miss:job 成功后自动新建缓存
```

### 3. 淘汰（eviction）

- 容量上限（GitHub：单仓库 5 GB）：超过后按**最近最少访问**淘汰
- TTL（GitHub：一周未访问的缓存也会被清）

demo 用逻辑时钟（每次 lookup/save 计数）近似"最近访问"；容量以条目数近似。

### 4. 安全注意（官方警告）

> 不要把敏感信息放进缓存路径：任何有读权限的人都能通过 PR 读取缓存内容；
> fork 仓库可以访问 base 分支的缓存。

## 对比 / 选型

| 维度 | key 含 hashFiles | key 含日期/run_id | key 含 commit SHA |
| --- | --- | --- | --- |
| 命中率 | 高（依赖不变即命中） | 中（每天轮换） | 低（每提交新缓存） |
| 适用 | 依赖缓存（官方推荐） | 定期强制刷新 | 短生命周期专用缓存 |

## 环境准备

- 操作系统：任意（内存模拟，无文件系统依赖）
- 语言版本：C（C99）/ Python 3.8+ / Go 1.18+
- 依赖：无（C 版自带 SHA-256 实现；Python 用 hashlib；Go 用 crypto/sha256）

## 运行方式

### C
```bash
gcc -O2 -Wall -Wextra sha256.c cache_demo.c -o cache_demo && ./cache_demo
```
### Python
```bash
python3 cache_demo.py
```
### Go
```bash
go run cache_demo.go
```

## 关键代码片段（Python）

```python
def lookup(self, key, restore_keys):
    """官方查找次序:精确 -> 前缀回退(取最近访问);返回 (kind, data)。"""
    if key in self.entries:                       # 1. 精确匹配
        e = self.entries[key]
        e.last_access = self._tick()
        return "exact", e.data
    for prefix in restore_keys:                    # 3. restore-keys 有序前缀
        cands = [(e.last_access, k) for k, e in self.entries.items()
                 if k.startswith(prefix)]
        if cands:
            best = max(cands)[1]                   # 最近访问的部分匹配
            e = self.entries[best]
            e.last_access = self._tick()
            return "partial", e.data
    return "miss", None                            # 4. 全部未命中

def save(self, key, data):
    """job 成功后保存;超容量按 LRU 淘汰。"""
    while len(self.entries) >= self.capacity:
        lru = min(self.entries, key=lambda k: self.entries[k].last_access)
        del self.entries[lru]
    self.entries[key] = Entry(data, self._tick())
```

## 性能与边界

- 查找 O(n)（demo 用线性扫描；真实系统远端存储按 key 直接寻址 O(1)）
- key 最长 512 字符（GitHub 限制，超长直接失败）；单仓库缓存 5 GB（GitHub 限制）
- 官方限制语义：缓存**创建后不可修改**，只能以新 key 新建 —— demo 的 `save` 对已存在
  key 走更新分支仅用于简化，README 在此注明与官方差异

## 注意事项与常见坑

- **`steps.<id>.outputs.cache-hit` 是字符串** `'true'`/`'false'`，判断要写
  `!= 'true'` 而不是布尔取反。
- **restore-keys 是有序的**：放前面的优先；前缀从长到短写（`npm-feature-` → `npm-`），
  顺序写反会总是命中最宽泛的前缀。
- **部分命中 ≠ 跳过构建**：restore-keys 恢复的旧缓存只是"打底"（省增量下载），
  官方语义下 job 仍要执行并在成功后保存新 key。
- **跨 OS 的路径陷阱**：save 与 restore 的 `path` 不一致会导致 miss；官方 caching-strategies
  文档专门列出 Ubuntu/Windows/macOS 各自的 workspace 路径。
- **敏感数据禁入缓存**（见上"安全注意"）。

## 参考资料（实际阅读过的权威来源）

- [Caching dependencies to speed up workflows — GitHub Docs](https://docs.github.com/fr/actions/writing-workflows/choosing-what-your-workflow-does/caching-dependencies-to-speed-up-workflows) — key/restore-keys/path 参数定义、查找次序原文、512 字符上限、5GB 容量与一周淘汰、安全警告
- [actions/cache caching-strategies.md](https://github.com/actions/cache/blob/2b5a782c41f96354f95e57fda59dccf5a4eb2c23/caching-strategies.md) — 锁文件哈希 key / restore-keys 打底 / OS 隔离 / run_id 与 commit SHA 短生命周期缓存策略
- [actions/cache README](https://github.com/cdr/action-gcs-cache) — cache-hit 输出跳过步骤的官方用法与 `${{ runner.os }}-${{ hashFiles('**/lockfiles') }}` 键模板

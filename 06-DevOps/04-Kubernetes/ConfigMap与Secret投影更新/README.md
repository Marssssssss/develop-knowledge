# ConfigMap / Secret 卷投影:原子写入与三条"不更新"

## 简介

挂载 ConfigMap / Secret 的卷**会**自动更新,而环境变量**不会**;`subPath` 挂载**不会**。这三句话背后是 kubelet 里一个叫 `AtomicWriter` 的小组件,以及一个经典技巧:**用符号链接的原子 rename 来切换目录内容**,而不是原地覆盖文件。

`AtomicWriter` 在挂载点里维持这样一层结构:

```
/mnt/config/
├── app.yml          -> ..data/app.yml        ← 用户可见的符号链接
├── ..data           -> ..2026_09_19_16_40_05.12345678/   ← 数据目录链接
├── ..2026_09_19_16_40_05.12345678/
│   └── app.yml                                ← 真实文件
```

更新时**不碰** `app.yml` 这个链接(它永远指向 `..data/app.yml`),而是新建一个时间戳目录 → 写新内容 → 建 `..data_tmp` → **rename 到 `..data`** → 删旧目录。rename 在 POSIX 上是原子的,所以容器里的进程要么看到完整的旧版本,要么看到完整的新版本,**不存在半个新半个旧**。

## 原理详解

### Write 的 12 步(源码注释原文摘译)

```
 1. 校验 payload(路径非法直接返回)
 2. 读 ..data 符号链接,定位当前时间戳目录
 3. 遍历旧版本,判断有没有"被删掉但仍留在盘上"的部分
 4. 比对当前时间戳目录里的数据与本次 payload,决定是否真的需要更新
 5. 需要则新建时间戳目录
 6. 把 payload 写进新目录
 7. 设置权限(可选 setPerms)
 8. 建指向新目录的符号链接 ..data_tmp
 9. 把 ..data_tmp rename 成 ..data(rename 是原子的)
10. 建立/补齐用户可见的符号链接(仅当不存在)
11. 从可见层删掉不再出现的路径
12. 删除上一个时间戳目录
```

注意 **第 9 步在第 10 步之前**:必须先把 `..data` 切到完整的新目录,再去补可见链接,否则会有一段窗口期链接指向的目录内容是半成品。本项目用操作轨迹断言这个顺序。

### validatePath 的五条禁令

```go
// paths may not:
// 1. be absolute
// 2. contain '..' as an element
// 3. start with '..'
// 4. contain filenames larger than 255 characters
// 5. be longer than 4096 characters
```

第 3 条就是 `AtomicWriter` 能**独占 `..` 前缀命名空间**的原因:普通 key 不能以 `..` 开头(长度 > 2),所以 `..data`、`..data_tmp`、`..2026_02_01_...` 永远不会和用户数据撞车。

> 测这条时要小心:单测路径长度上限(4096)必须同时满足文件名上限(255),否则会先被第 4 条拦下,让"4096 字符合法"这条断言看起来失败。本项目用 `255×16 + 15 = 4095` 构造。

### payload 没变就不写

```go
func shouldWritePayload(payload map[string]FileProjection, oldTsDir string) (bool, error) {
    for userVisiblePath, fileProjection := range payload {
        shouldWrite, err := shouldWriteFile(filepath.Join(oldTsDir, userVisiblePath), fileProjection.Data)
        ...
    }
    return false, nil   // 全都没变
}
```

kubelet 每个同步周期都会调 `Write`,**不是每次都会建新目录**。本项目断言:内容未变时轨迹里只有一条 `noop: payload unchanged`,且 `..data` 指向不变。

### 可见链接只建一次

```go
linkname := userVisiblePath[:slashpos]          // 只取路径的第一段
_, err := os.Readlink(filepath.Join(w.targetDir, linkname))
if err != nil && os.IsNotExist(err) {           // 仅当不存在才建
    err = os.Symlink(filepath.Join(dataDirName, linkname), visibleFile)
}
```

两个要点:① 嵌套路径 `dir/a.yml`、`dir/b.yml` 只为 `dir` 建一个链接,不会为每个文件建;② 链接存在就跳过,因为 `xxx -> ..data/xxx` 这个指向是**稳定的**,内容更新时它不用动。

### 三条"不更新"

| 消费方式 | 是否自动更新 | 出处 |
| --- | --- | --- |
| 卷挂载(整个目录) | ✅ 最终更新 | 文档 *"Mounted ConfigMaps are updated automatically"* |
| 卷挂载 + `subPath` | ❌ | 文档 *"A container using a ConfigMap as a subPath volume mount will not receive ConfigMap updates."* |
| 环境变量(`envFrom` / `valueFrom`) | ❌ 需重启 Pod | 文档 *"ConfigMaps consumed as environment variables are not updated automatically and require a pod restart."* |

`subPath` 不更新的原因很直接:`subPath` 挂载的是**单个文件到单个路径**,底层不是 `AtomicWriter` 管理的目录,没有 `..data` 这一层可切。

### 更新会迟到多久

> 原文:*"the total delay from the moment when the ConfigMap is updated to the moment when new keys are projected to the Pod can be as long as the kubelet sync period + cache propagation delay, where the cache propagation delay depends on the chosen cache type (it equals to watch propagation delay, ttl of cache, or zero correspondingly)."*

缓存类型由 `KubeletConfiguration.configMapAndSecretChangeDetectionStrategy` 决定:

| 策略 | 传播延迟 | 总延迟(设 sync=60s) |
| --- | --- | --- |
| `Watch`(默认) | watch 传播延迟 | 60 + watch 延迟 |
| `TTL` | 缓存 TTL | 60 + ttl |
| `Get` | 0(直连 apiserver) | 60 |

也就是说**最快也要等一个 kubelet 同步周期**,而且默认策略下还要再加一段 watch 传播延迟。想让配置更新"看起来是立即的",只有改小同步周期或改用 `Get`(代价是 apiserver 压力)。

## 对比

| 机制 | 原子性来源 | 能否回滚 | 进程可见方式 |
| --- | --- | --- | --- |
| `AtomicWriter`(卷挂载) | `rename(2)` | 天然可(旧目录还没删) | 目录内容整体切换 |
| 原地覆盖写 | 无 | 不能 | 可能读到半截文件 |
| `subPath` 挂载 | 不适用 | 不适用 | 挂载时快照,永不更新 |
| 环境变量 | 不适用 | 不适用 | 启动时快照,需重启 |

## 环境

- Python 3.8+(仅标准库)
- Go 1.18+(仅标准库)
- C99 编译器(实现体在 `atomic_projection_impl.h`,用 `#include` 引入)

## 运行方式

```bash
python python/atomic_projection.py        # 49 项断言
go run go/atomic_projection.go
gcc -std=c99 -o /tmp/ap c/atomic_projection.c && /tmp/ap
```

## 关键代码

```python
# 8/9:建 ..data_tmp 再 rename —— 原子切换点
self.fs.symlink(new_ts, self._p(NEW_DATA_DIR_NAME))
self.fs.rename(self._p(NEW_DATA_DIR_NAME), self._p(DATA_DIR_NAME))

# 10:仅为路径第一段建可见链接,且仅当不存在
for name in payload:
    first = name.split("/")[0]
    if self.fs.readlink(self._p(first)) is None:
        self.fs.symlink(DATA_DIR_NAME + "/" + first, self._p(first))
```

## 性能边界

- 每次更新的 I/O 是 **O(payload 总量)** 的全量重写(不是增量),所以大 ConfigMap 高频更新会持续制造写放大。
- 时间戳目录存活期内磁盘上有**新旧两份**数据,峰值占用约 2×。
- 内存/文件数开销:每个 key 至少一个文件 + 一个可见链接;key 数量大时 inode 占用翻倍。
- ConfigMap 单个**不能超过 1 MiB**(`A ConfigMap is not designed to hold large chunks of data`);超过要用卷或外部存储。
- `Watch` 策略下每个 kubelet 都维持到 apiserver 的 watch 长连接,节点规模大时这是 apiserver 的主要压力来源之一;`Get` 把压力换成全量 GET 查询。
- 时间复杂度:一次 `Write` 是 O(文件数 + 路径长度),相比 I/O 可忽略。

## 注意事项与常见坑

- **更新不是同步的**,最快也要一个 kubelet 同步周期。拿它做"热加载开关"必须接受秒级到分钟级延迟。
- **`subPath` 挂载不更新**是最高频的踩坑点:很多人为了挂载单个文件用 `subPath`,然后发现改了 ConfigMap 没反应。
- **环境变量不更新**,必须重启 Pod;用 `kubectl rollout restart` 是常见做法。
- **应用要自己感知变化**:`..data` 是符号链接,应用可以用 inotify/fanotify 监听它(源码注释原文提到),但**不要监听单个文件** —— 文件本身从不被覆盖,只会被换掉。
- **不要用 `..` 开头的 key**:那是 `AtomicWriter` 的保留命名空间,`validatePath` 会直接拒绝。
- **嵌套路径只建第一段的链接**,所以在容器里 `ls -l` 看到的是 `dir -> ..data/dir`,而不是每个文件各自的链接 —— 排查时注意逐级解析。
- **删掉一个 key 后**,可见层对应链接会被移除(第 11 步),但**应用如果持有已打开的文件描述符,仍能读到旧内容**(Unix 语义)。
- Secret 与 ConfigMap 共用同一套 `AtomicWriter`,行为一致;Secret 的额外差异在 tmpfs 与静态加密上,不在投影机制。

## 参考资料

已实际阅读:

1. Kubernetes 官方文档 — ConfigMaps,https://kubernetes.io/docs/concepts/configuration/configmap/
2. `kubernetes/kubernetes@master` — `pkg/volume/util/atomic_writer.go`,https://cdn.jsdelivr.net/gh/kubernetes/kubernetes@master/pkg/volume/util/atomic_writer.go
3. Kubernetes 官方文档 — Secrets,https://kubernetes.io/docs/concepts/configuration/secret/

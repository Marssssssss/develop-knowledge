# OverlayFS 联合挂载(upper / lower / work / merged)

> Docker `overlay2` 存储驱动的底层机制:把多个**只读镜像层**叠成一个**读写容器层**,对容器进程表现为单一文件系统。

## 简介

OverlayFS 是 Linux 3.18 起合并的内核联合文件系统,由 Miklos Szeredi (SUSE) 编写,见 kernel.org `Documentation/filesystems/overlayfs.rst`。它将多个目录树合并为一棵:

- **lower 层**(只读,可多层,`:` 分隔):基础镜像、依赖镜像、应用镜像。Docker 中每个 `FROM` / `COPY` / `RUN` 生成一层。
- **upper 层**(读写,单层):容器运行时的修改写在这里。Docker 容器的 `UpperDir`。
- **work 层**(内部暂存,同 fs 与 upper):保证 copy-up 的原子性(写临时文件→rename)。
- **merged 层**(对外挂载点):三类目录在该点合为统一视图。

**Container 三大基石之 "文件系统层"**:namespaces 隔离视图、cgroups 限制资源、OverlayFS 把"镜像 + 容器层"叠成一份根文件系统。

Docker 默认存储驱动 `overlay2` 用的就是这个机制(`/var/lib/docker/overlay2/{id}/diff|merged|work`)。Kubernetes/containerd/Podman 同样。

## 原理详解

### 挂载语法

```bash
mount -t overlay overlay \
  -o lowerdir=/lower1:/lower2:/lower3,upperdir=/upper,workdir=/work \
  /merged
```

约束(kernel.org overlayfs.rst):
1. `lowerdir` 可用 `:` 列多层,**右到左**是底层(右 = 最先构建的层)
2. `upperdir` 必须支持 `trusted.*` 扩展属性 + `readdir` 返回有效 `d_type`(故 **NFS 不能做 upper**)
3. `workdir` 必须**与 upperdir 同文件系统**(原子 rename)
4. 只读 overlay(无 upper/work)用作多镜像合并:`mount -t overlay -olowerdir=/l2:/l1 /merged`

### copy-on-write(copy-up)

容器进程 `open("/etc/passwd", O_RDWR)`,但 `/etc/passwd` 在 lower:
1. 从 lower 复制到 upper(在 work 先做临时文件,完成后 `rename` 到 upper)
2. 进程实际打开 upper 上的副本进行写
3. 同一 lower 多容器**共享只读层**,互不影响

**核心代码路径**:`fs/overlayfs/copy_up.c`,用 `do_splice_direct()` 做零拷贝数据搬运 + `ovl_copy_up_metadata()` 复制 owner/mode/mtime/xattr;Linux 5.11+ 加 `metacopy=on` 选项,延迟数据复制到首次写(chmod/chown 只复制元数据)。

### whiteout 与 opaque

**为什么需要?** 因为不能修改 lower(只读),所以删除标记要在 upper 表达。

| 概念 | 表示 | 触发 |
| --- | --- | --- |
| **whiteout 文件** | 设备号 0/0 字符设备,或带 `trusted.overlay.whiteout` xattr 的零字节普通文件 | `rm /lower/file` → upper 创建同名 whiteout |
| **opaque 目录** | upper 目录带 `trusted.overlay.opaque="y"` xattr | `rm -rf /lower/dir && mkdir /lower/dir` → 新目录标 opaque |

upper 找到 whiteout 时,**忽略 lower 中同名条目**;遇到 opaque 目录,**完全替换** lower 的同名词条(Kubernetes 中 DockerImage 重建容器 layer 时常见 — 整层覆盖)。

### readdir 合并策略

读 merged 目录时:先读 upper(去重),再读 lower。该合并结果缓存在 `struct file` 内,**目录关闭前变更不可见**——这对 `ls` 没问题,长跑监控要 `seekdir(0)` 重建。

### 多层 Docker 示例

```text
docker inspect | jq '.[].GraphDriver.Data'
{
  "LowerDir": "/var/lib/docker/overlay2/abc/diff:/var/lib/docker/overlay2/def/diff",
  "MergedDir": "/var/lib/docker/overlay2/xyz/merged",
  "UpperDir": "/var/lib/docker/overlay2/xyz/diff",
  "WorkDir":  "/var/lib/docker/overlay2/xyz/work"
}
```

10 个容器共享 base 镜像的 abc/diff(只读,内核页缓存命中),各自只有 xyz/diff(几 MB 改写层)。

## 对比 / 选型

| 驱动 | 层级 | 速度 | 局限 | 典型 |
| --- | --- | --- | --- | --- |
| overlay2 | 多 lower + 1 upper | 快(内核原生) | 内核 ≥ 3.18,不支持 NFS | Docker 默认 |
| AUFS | 多层 | 快 | out-of-tree,Ubuntu 历史偏爱 | 老 Docker |
| Btrfs/zfs | CoW FS 层 | 中 | FS 依赖 | LXC 高级 |
| devicemapper | block-level CoW | 慢 | LVM 必装 | 老 RHEL/CentOS |

## 环境准备

- OS:Linux 内核 ≥ 3.18;overlay 内核模块加载(`modprobe overlay`)
- C:gcc;Python:3.6+(`os`, `ctypes`);Go:1.21+(仅标准库)
- 实际 mount 需要 root;Demos 用无 root 视角解析 `/proc/mounts`

## 运行方式

### Python(无 root,演示解析)
```bash
cd python && python3 overlay_demo.py inspect
# 输出:合并视图拆解 + upper/lower/work 详情 + 自检路径
python3 overlay_demo.py demo-layers
# 演示层栈数据结构 + 搜索顺序
```

### C(实际 mount,需 root)
```bash
gcc -O2 -Wall overlay_demo.c -o overlay_demo && sudo ./overlay_demo
# 真实在 /tmp 创建 lower/upper/work 三层,挂载 /merged,写入触发 copy-up,验证文件分层
```

### Go(无 root,模拟)
```bash
cd go && go run overlay_demo.go inspect
```

## 关键代码片段

### C 版 mount 核心(`overlay_demo.c`)

```c
// mount(2) overlay kernel.org overlayfs.rst §"Mounting OverlayFS"
char lowerdir[256] = "/tmp/ovl_lower";
char upperdir[256] = "/tmp/ovl_upper";
char workdir[256]  = "/tmp/ovl_work";
snprintf(opts, sizeof(opts),
         "lowerdir=%s,upperdir=%s,workdir=%s",
         lowerdir, upperdir, workdir);
if (mount("overlay", "/tmp/ovl_merged", "overlay", 0, opts) < 0)
    die("mount overlay");  // 需 root
```

### Python 版解析逻辑(`overlay_demo.py`)

```python
# /proc/mounts 行:"device mount-point fs-type options ..."
# overlay 的 options 中 lowerdir 形如 "/a:/b:/c",需 : 拆分并按从右到左排序
def parse_overlay_opts(opts: str) -> dict:
    out = {"lowerdir": [], "upperdir": None, "workdir": None}
    for kv in opts.split(","):
        k, _, v = kv.partition("=")
        if k == "lowerdir":
            out["lowerdir"] = v.split(":")           # 数组
        elif k in ("upperdir", "workdir"):
            out[k] = v
    return out
# Docker inspect 显示 LowerDir 同样按 ":" 拼,右到左 = build 顺序
```

### whiteout 自检
```python
# 在 upper 用 mknod 创建 0/0 字符设备模拟 whiteout
os.mknod("upper/deleted_file", 0o000 | stat.S_IFCHR,
         os.makedev(0, 0))   # 等同内核的 whiteout 标记
```

## 性能与边界

- copy-up 数据搬运:`do_splice_direct` 走管道,**零拷贝**
- metacopy=on(5.11+):chmod/chown 不复制数据(几 KB 元数据 vs 几 GB 内容)
- 多 lower 数量有限制:历史默认 500,内核参数 `fs.overlay-max-layers` 可调;Docker daemon `--max-layer-count`
- Docker 镜像 BuildKit `--chmod` 等元数据修改因 metacopy 几乎零成本
- merged 视图的 inode 可在不同层之间变化(`xino=on` 可使其稳定)

## 注意事项与常见坑

1. **workdir 必须与 upperdir 同 fs**;不同 fs 时 `mount` 返回 `EINVAL`
2. **upperdir 不支持 NFS**(缺 `trusted.*` xattr);但放在 `tmpfs / ext4 / xfs / btrfs` 均可
3. **lower 文件被删除用 whiteout**,绝对不要试图"原位删除"(lower 是只读)
4. **`mount --bind` 含嵌套 overlay 的目录可能触发 `EXDEV`**(跨 fs 改名);Docker volume bind mount 时常见
5. **Docker `COPY` 触发 layer**:即使 `COPY` 一字节文件,build 也会复制整个 layer FS tree(因 docker buildkit 内容寻址 layer cache)
6. **readdir 期间目录被修改**:缓存的合并视图不会更新;用 `seekdir(0)` 重建(go 走 `os.File.Readdir` 同样)
7. **C 演示必须 sudo**:非 root `mount(2)` 返回 `EPERM`
8. **Go 不支持直接 mount**:用 syscall.Mount 包装 + unshare;demo 走只读解析路径(同 Python)

## 参考资料(实际阅读过的权威来源)

- [kernel.org overlayfs.rst — Linux 6.9 文档原文](https://sources.debian.org/src/linux/6.9.7-1/Documentation/filesystems/overlayfs.rst/) — overlay 行为最权威定义,完整阅读(upper/lower 约束、copy-up 流程、whiteout/opaque 语义、readdir 缓存、rename 跨 fs)
- [kernel.org `filesystems/overlayfs.html` 内核文档](https://www.kernel.org/doc/html/v5.8/filesystems/overlayfs.html) — Miklos Szeredi 的原始文档,xino/redirect_dir/sametab 等特性
- [kernel-internals.org OverlayFS 镜像层](https://kernel-internals.org/filesystems/overlayfs/) — Docker overlay2 driver 章节 + 内核数据结构 `ovl_entry`/`ovl_inode` + `ovl_copy_up_inode`(`fs/overlayfs/copy_up.c`)实证
- [Docker storage drivers 官方文档 — overlay2](https://docs.docker.com/storage/storagedriver/overlayfs-driver/) — `LowerDir`/`UpperDir`/`WorkDir` 在 `/var/lib/docker/overlay2/` 的实际布局
- [wikipedia Union mount/OverlayFS 历史](https://en.wikipedia.org/wiki/UnionFS) — 2006 UnionFS → 2009 AUFS → 2014 OverlayFS 演化

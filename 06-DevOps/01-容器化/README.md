# 容器化

> 容器三大基石:namespaces(资源隔离)/ cgroups(资源限制)/ 镜像格式(存储与分发)+ OverlayFS(层合并)+ Capabilities(权限分割)+ Seccomp-BPF(攻击面收窄)+ veth pair(网络)+ OCI Runtime Spec(标准化)。

## 已完成 demo

| demo | 路径 | 知识点 | 语言 |
| --- | --- | --- | --- |
| 045 | [Namespace隔离/](./Namespace隔离/) | Linux namespaces:8 类 ns、unshare/clone/setns、user ns 无特权通道、PID ns 单向性 | C / Python / Go |
| 046 | [Cgroups-v2/](./Cgroups-v2/) | cgroup v2:单一层级、subtree_control、memory.max OOM、cpu.max 限流 | C / Python / Go |
| 047 | [OCI镜像格式/](./OCI镜像格式/) | image-spec:DiffID/ChainID/ImageID、whiteout、image-layout 构建+解包 | Python / Go |
| 152 | [OverlayFS-联合挂载/](./OverlayFS-联合挂载/) | upper/lower/work/merged、copy-up 原子性、whiteout 0/0 chrdev、opaque xattr、metacopy 优化 | C / Python / Go |
| 153 | [Capabilities-权限分割/](./Capabilities-权限分割/) | 5 cap 集合(Effective/Permitted/Inheritable/Bounding/Ambient)、文件 cap、5.9+ 41 cap、Linux 历史版本 | C / Python / Go |
| 154 | [Seccomp-BPF-系统调用过滤/](./Seccomp-BPF-系统调用过滤/) | BPF filter + seccomp_data、7 RET 值(KILL/TRAP/ERRNO/USER_NOTIF/LOG/ALLOW)、arch 校验、PR_SET_NO_NEW_PRIVS | C / Python / Go |
| 155 | [veth-pair-网络设备/](./veth-pair-网络设备/) | pair 跨 netns 移动、bridge L2 转发、FDB、netns exec、iptables MASQUERADE | C / Python / Go |
| 156 | [OCI-Runtime-Spec/](./OCI-Runtime-Spec/) | config.json schema、4 状态机 create/start/kill/delete、Linux 必需 mounts、maskedPaths、默认 14 cap | C / Python / Go |
| 237 | [Docker层缓存失效/](./Docker层缓存失效/) | BuildKit 缓存键链、COPY 元数据校验和(mtime 除外)、RUN 只看命令串、失效级联、构建密钥不入缓存 | Python / Go |
| 238 | [多阶段构建/](./多阶段构建/) | 阶段命名与 `COPY --from`、`--target`、BuildKit 只构建依赖阶段、`--cache-to` min/max 与攻击面 | Python / Go |
| 239 | [UserNamespace-UID映射/](./UserNamespace-UID映射/) | uid_map 行格式与第二字段歧义、只能写一次、340 行/一页上限、overflow 65534、subuid+newuidmap、嵌套 32 层 | C / Python / Go |
| 240 | [Rootless容器/](./Rootless容器/) | RootlessKit+userns、subuid≥65536、存储驱动白名单、cgroup 仅 v2+systemd 且默认只委派 memory/pids、官方报错→根因 | Python / Go |
| 241 | [Cgroup-eBPF附加/](./Cgroup-eBPF附加/) | cgroup v2 subtree_control 四规则与 no-internal-process、BPF_PROG_TYPE_CGROUP_* 与 attach type、BPF token 四项委派与 ns_capable 语义 | C / Python / Go |
| 690 | [CNI插件模型与IPAM/](./CNI插件模型与IPAM/) | CNI SPEC §2 执行协议与 §3 生命周期（ADD 正序 / DEL 逆序且统一喂 add 最终结果 / GC 出错继续）、host-local Range 规范化（/31·/32 拒绝、RangeEnd 排除广播）、RangeIter 轮询（lastReservedIP 起点、跨 range 回绕、网关跳过） | Python / Go |
| 691 | [OCI分发协议与镜像拉取/](./OCI分发协议与镜像拉取/) | distribution-spec 的 14 个端点与 14 个错误码、整体上传两条路、分块上传三条 416 判据、跨仓库挂载（命中 201 / 未命中 202）、manifest 引用完整性、标签 ASCIIbetical 分页（`last` 非包含、`n=0` 无 Link）、referrers 与 artifactType 过滤 | Python / Go |
| 692 | [runc创建流程与同步协议/](./runc创建流程与同步协议/) | 父子双进程的 9 个同步常量与四对握手、seccomp 的两个插入点（无 NNP 尽早 / 有 NNP 紧贴 execve）、`procReady` 兜底判据、exec fifo 写 `"0"` 阻塞、`UnsafeCloseFrom(PassedFilesCount+3)`（CVE-2024-21626） | Python / Go |
| 693 | [pivotRoot与挂载传播/](./pivotRoot与挂载传播/) | pivot_root(2) 六条限制与 errno 分派、`pivot_root(".", ".")` 免临时目录写法、shared/slave/private/unbindable 四种传播、runc 的 prepareRoot（rslave + 父挂载去共享 + bind 自身）与 msMoveRoot 兜底 | Python / Go |
| 694 | [cgroup线程模式与cpuset分区/](./cgroup线程模式与cpuset分区/) | cgroup.type 四态与 `EOPNOTSUPP`、转 threaded 的两个条件与根豁免、thread root 作为资源域、cpuset.cpus.partition 的 member/root/isolated、local 与 remote 分区、有效性三条件与失效降级 | Python / Go |

## 待研究

- [ ] Podman / containerd 替代(docker CLI 兼容层、rootless)
- [ ] 镜像签名与供应链(cosign / SBOM / SLSA provenance)
- [x] cgroup v2 的 threaded 模式与 cpuset 硬绑定 → 已建于 `cgroup线程模式与cpuset分区/`（ID 694）
- [x] 容器网络 CNI 插件模型(IPAM / 链式插件) → 已建于 `CNI插件模型与IPAM/`（ID 690）
- [ ] 运行时安全(LSM 叠加:AppArmor/SELinux 与 seccomp 的关系)
- [ ] containerd 的 snapshotter 与内容存储(content store / leases)
- [ ] 镜像分发加速(Nydus / stargz 惰性拉取与 FUSE)
- [ ] 容器的 checkpoint/restore(CRIU) 与实时迁移

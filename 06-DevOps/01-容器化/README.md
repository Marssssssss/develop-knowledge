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

## 待研究

- [ ] Docker 镜像层与缓存(构建缓存命中机制、COPY 指令与层失效)
- [ ] 多阶段构建减小镜像体积
- [ ] Podman / containerd 替代(docker CLI 兼容层、rootless)
- [ ] cgroup v2 eBPF 程序附加(5.12+ BPF token)
- [ ] user namespace + rootless 容器
- [ ] Podman rootless(无特权创建容器的现状)

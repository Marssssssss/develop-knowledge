# 容器化

> 容器三大基石:namespaces(资源隔离)/ cgroups(资源限制)/ 镜像格式(存储与分发)。

## 已完成 demo

| demo | 路径 | 知识点 | 语言 |
| --- | --- | --- | --- |
| 045 | [Namespace隔离/](./Namespace隔离/) | Linux namespaces:8 类 ns、unshare/clone/setns、user ns 无特权通道、PID ns 单向性 | C / Python / Go |
| 046 | [Cgroups-v2/](./Cgroups-v2/) | cgroup v2:单一层级、subtree_control、memory.max OOM、cpu.max 限流 | C / Python / Go |
| 047 | [OCI镜像格式/](./OCI镜像格式/) | image-spec:DiffID/ChainID/ImageID、whiteout、image-layout 构建+解包 | Python / Go |

## 待研究

- [ ] Docker 镜像层与缓存(构建缓存命中机制、COPY 指令与层失效)
- [ ] 多阶段构建减小镜像体积
- [ ] Podman / containerd 替代(docker CLI 兼容层、rootless)
- [ ] OverlayFS 联合挂载(upper/lower/merged、写时复制)—— OCI 层的运行时落地
- [ ] 容器运行时接口(CRI、OCI runtime-spec、runc 启动流程)
- [ ] 镜像分发协议(OCI Distribution Spec、registry v2 API、拉取去重)

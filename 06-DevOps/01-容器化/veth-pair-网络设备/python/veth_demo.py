"""
veth pair + Linux bridge 演示(无 root,基于文件解析与命令模板)

参考资料(已实际阅读,见 README):
  man7 veth(4):              https://man7.org/linux/man-pages/man4/veth.4.html
  man7 network_namespaces(7): https://man7.org/linux/man-pages/man7/network_namespaces.7.html
  man7 ip-link(8):           https://man7.org/linux/man-pages/man8/ip-link.8.html
  kernel-internals container-networking

用法:
  python3 veth_demo.py topology           # 打印 Docker/CNI 典型拓扑示意
  python3 veth_demo.py commands           # 列出创建 pair/bridge 的命令模板
  python3 veth_demo.py inspect            # 解析 /sys/class/net 现状
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path


def cmd_topology(_args):
    """打印典型容器网络拓扑示意。"""
    print("""
┌──────────── host netns ─────────────┐  ┌─── container netns ───┐
│  eth0 (physical, e.g. 10.0.0.5)       │  │ eth0 (veth1 的 peer)   │
│  docker0 (bridge, 172.17.0.1/16)      │  │ 172.17.0.2/16          │
│   ├── vethA ←── pair peer ─→ 容器 A  │  │ ↑                      │
│   └── vethB ←── pair peer ─→ 容器 B  │  │ ip route default via   │
│  iptables MASQUERADE 172.17/16        │  │   172.17.0.1 dev eth0  │
└──────────────────────────────────────┘  └───────────────────────┘

关键流程:
  容器 A  172.17.0.2 → docker0 → eth0 → 目标服务器 (NAT/MASQUERADE)
  目标服务器回应 → 宿主机 conntrack 改 dst=172.17.0.2 → docker0 → vethA → 容器 A

Veth pair 是虚拟网线,跨 net namespace(man4 veth 原文:
  "Packets transmitted on one device in the pair are immediately received on the other.")
""")


def cmd_commands(_args):
    """打印典型命令模板(便于对照学习)。"""
    cmds = [
        "# 1. 创建 veth pair(两端均在 host netns)",
        "ip link add veth0 type veth peer name veth1",
        "",
        "# 2. 创建 netns(模仿容器 namespace)",
        "ip netns add demo_ns",
        "",
        "# 3. 把 veth1 移进 demo_ns",
        "ip link set veth1 netns demo_ns",
        "",
        "# 4. 配置两端 IP,up 接口",
        "ip addr add 10.200.0.1/24 dev veth0",
        "ip link set veth0 up",
        "ip netns exec demo_ns ip addr add 10.200.0.2/24 dev veth1",
        "ip netns exec demo_ns ip link set veth1 up",
        "ip netns exec demo_ns ip link set lo up",
        "ip netns exec demo_ns ip route add default via 10.200.0.1",
        "",
        "# 5. 测试连通性",
        "ip netns exec demo_ns ping 10.200.0.1",
        "",
        "# 6. 添加 bridge(L2 交换机)",
        "ip link add docker0 type bridge",
        "ip addr add 172.17.0.1/16 dev docker0",
        "ip link set docker0 up",
        "ip link set veth0 master docker0      # host 端接入 bridge",
        "",
        "# 7. NAT 网关(让容器借 host 出公网)",
        "iptables -t nat -A POSTROUTING -s 172.17.0.0/16 ! -o docker0 -j MASQUERADE",
        "sysctl -w net.ipv4.ip_forward=1",
        "",
        "# 8. 清理",
        "ip netns del demo_ns",
        "ip link del docker0",
        "ip link del veth0",
    ]
    print("\n".join(cmds))


def cmd_inspect(_args):
    """扫描 /sys/class/net 与 ip 命令输出,归类 veth/bridge/dummy/物理网卡。"""
    if not sys.platform.startswith("linux"):
        print("非 Linux 平台,跳过 /sys 扫描。")
        return

    print("# 当前 /sys/class/net 接口列表:")
    net_dir = Path("/sys/class/net")
    if not net_dir.exists():
        print("  /sys/class/net 不可访问")
        return

    for entry in sorted(net_dir.iterdir()):
        name = entry.name
        try:
            ifindex = (entry / "ifindex").read_text().strip()
            operstate = (entry / "operstate").read_text().strip()
        except (FileNotFoundError, PermissionError):
            continue
        # 类型识别:有 /sys/class/net/<n>/bridge 子目录 → bridge
        # 有 /sys/class/net/<n>/device/driver → phys
        # veth 模式下 /sys/class/net/<n>/statistics/ 下无 rx bytes
        is_bridge = (entry / "bridge").exists()
        try:
            link_mode = (entry / "link_mode").read_text().strip()
        except FileNotFoundError:
            link_mode = "?"
        try:
            addr = (entry / "address").read_text().strip()
        except FileNotFoundError:
            addr = "?"
        flags = []
        if is_bridge:
            flags.append("bridge")
        if (entry / "device").exists():
            flags.append("phys-or-virtio")
        if (entry / "tun_flags").exists() or name.startswith(("veth", "dummy", "lo")):
            flags.append(f"vdev ({link_mode})")
        print(f"  {name:<20}  idx={ifindex:>3}  state={operstate:<7}  mac={addr}  {flags}")

    # 如果有 ip 命令,打印 veth-info
    if shutil.which("ip"):
        try:
            res = subprocess.run(["ip", "-d", "link", "show"], capture_output=True, text=True, timeout=5)
            veth_section = []
            capture = False
            for ln in res.stdout.splitlines():
                if "veth" in ln or "kind veth" in ln or "kind bridge" in ln:
                    capture = True
                if capture:
                    veth_section.append(ln)
                if capture and len(veth_section) > 6:
                    break
            if veth_section:
                print("\n# ip -d link show 筛选:")
                for ln in veth_section[:15]:
                    print(f"  {ln}")
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass


def main():
    cmds = {"topology": cmd_topology,
            "commands": cmd_commands,
            "inspect":  cmd_inspect}
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        print("usage: veth_demo.py {topology|commands|inspect}")
        sys.exit(1)
    cmds[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    main()

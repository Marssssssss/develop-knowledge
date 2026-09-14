// oci_demo.c — OCI Runtime Spec config.json 生成 + 解析(简单 JSON 演示)
//
// 参考资料:
//   OCI runtime-spec: https://github.com/opencontainers/runtime-spec
//   runc spec 默认实现: https://github.com/opencontainers/runc
//
// 用法:
//   gcc -O2 -Wall oci_demo.c -o oci_demo
//   ./oci_demo spec                          # 打印默认 config (简化 JSON,手写)
//   ./oci_demo capabilities                  # 打印 Docker 默认 14 cap
//   ./oci_demo lifecycle                     # 4 状态机
//   ./oci_demo minimal                       # 最小 config 关键字段
//
// 注释:本 demo 不依赖 cJSON,用最小手写格式方便阅读

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static const char *DEFAULT_CAPS[] = {
    "CAP_CHOWN", "CAP_DAC_OVERRIDE", "CAP_FOWNER", "CAP_FSETID", "CAP_KILL",
    "CAP_NET_BIND_SERVICE", "CAP_NET_RAW", "CAP_SETFCAP", "CAP_SETGID",
    "CAP_SETPCAP", "CAP_SETUID", "CAP_SYS_CHROOT", "CAP_MKNOD", "CAP_AUDIT_WRITE",
    NULL,
};

static void cmd_spec(void) {
    printf("{\n");
    printf("  \"ociVersion\": \"1.2.1\",\n");
    printf("  \"process\": {\n");
    printf("    \"terminal\": true, \"user\": {\"uid\": 0, \"gid\": 0},\n");
    printf("    \"args\": [\"sh\"],\n");
    printf("    \"env\": [\"PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\"],\n");
    printf("    \"cwd\": \"/\",\n");
    printf("    \"noNewPrivileges\": true,\n");
    printf("    \"capabilities\": {\n");
    printf("      \"bounding\":  [\"CAP_CHOWN\",\"CAP_NET_BIND_SERVICE\",\"CAP_NET_RAW\",\"CAP_SYS_CHROOT\",\"CAP_SETUID\",\"CAP_SETGID\"],\n");
    printf("      \"effective\": [\"CAP_CHOWN\",\"CAP_NET_BIND_SERVICE\",\"CAP_NET_RAW\",\"CAP_SYS_CHROOT\",\"CAP_SETUID\",\"CAP_SETGID\"],\n");
    printf("      \"permitted\": [\"CAP_CHOWN\",\"CAP_NET_BIND_SERVICE\",\"CAP_NET_RAW\",\"CAP_SYS_CHROOT\",\"CAP_SETUID\",\"CAP_SETGID\"]\n");
    printf("    },\n");
    printf("    \"rlimits\": [{\"type\": \"RLIMIT_NOFILE\", \"hard\": 1024, \"soft\": 1024}]\n");
    printf("  },\n");
    printf("  \"root\": {\"path\": \"rootfs\", \"readonly\": true},\n");
    printf("  \"hostname\": \"runc\",\n");
    printf("  \"mounts\": [\n");
    printf("    {\"destination\": \"/proc\",    \"type\": \"proc\",   \"source\": \"proc\"},\n");
    printf("    {\"destination\": \"/dev\",     \"type\": \"tmpfs\",  \"source\": \"tmpfs\", \"options\": [\"nosuid\",\"strictatime\",\"mode=755\",\"size=65536k\"]},\n");
    printf("    {\"destination\": \"/dev/pts\", \"type\": \"devpts\", \"source\": \"devpts\", \"options\": [\"nosuid\",\"noexec\",\"newinstance\",\"ptmxmode=0660\",\"gid=5\"]},\n");
    printf("    {\"destination\": \"/dev/shm\", \"type\": \"tmpfs\",  \"source\": \"shm\", \"options\": [\"nosuid\",\"nodev\",\"mode=1777\",\"size=65536k\"]},\n");
    printf("    {\"destination\": \"/sys\",     \"type\": \"none\",   \"source\": \"/sys\", \"options\": [\"rbind\",\"nosuid\",\"noexec\",\"nodev\",\"ro\"]}\n");
    printf("  ],\n");
    printf("  \"linux\": {\n");
    printf("    \"namespaces\": [{\"type\":\"pid\"},{\"type\":\"network\"},{\"type\":\"ipc\"},{\"type\":\"uts\"},{\"type\":\"mount\"}],\n");
    printf("    \"maskedPaths\":  [\"/proc/asound\",\"/proc/acpi\",\"/proc/kcore\",\"/proc/keys\",\"/proc/latency_stats\",\"/proc/scsi\"],\n");
    printf("    \"readonlyPaths\": [\"/proc/bus\",\"/proc/fs\",\"/proc/irq\",\"/proc/sys\",\"/proc/sysrq-trigger\"]\n");
    printf("  }\n");
    printf("}\n");
}

static void cmd_capabilities(void) {
    printf("# Docker / runc 默认 capability 集合(14 保留):\n");
    for (int i = 0; DEFAULT_CAPS[i]; i++) printf("  + %s\n", DEFAULT_CAPS[i]);
    printf("\n# 折中默认:6 核心 cap(nginx / 服务最常见):\n");
    const char *minimal[] = {"CAP_CHOWN", "CAP_NET_BIND_SERVICE", "CAP_NET_RAW",
                             "CAP_SYS_CHROOT", "CAP_SETUID", "CAP_SETGID", NULL};
    for (int i = 0; minimal[i]; i++) printf("  + %s\n", minimal[i]);
}

static void cmd_lifecycle(void) {
    printf("# OCI 容器 4 状态机 (runtime-spec §Lifecycle):\n\n");
    printf("       create\n");
    printf("creating ──────── created\n");
    printf("  ↑                 │\n");
    printf("  │ delete          │ start\n");
    printf("  │                 ↓\n");
    printf("  │              running ──── pause/resume ──── paused\n");
    printf("  │                 │\n");
    printf("  │                 │ kill / exit\n");
    printf("  │                 ↓\n");
    printf("  └──────────── stopped\n");
    printf("                    │\n");
    printf("                    │ delete\n");
    printf("                    ↓\n");
    printf("                (destroyed)\n\n");
    printf("# runc CLI:\n");
    printf("  runc create --bundle <bundle> <id>   # → created\n");
    printf("  runc start <id>                      # → running\n");
    printf("  runc state <id>                      # 查状态\n");
    printf("  runc kill <id> SIGTERM               # → stopped\n");
    printf("  runc delete <id>                     # → destroyed\n");
}

static void cmd_minimal(void) {
    printf("# 最小合法 config.json(必需字段):\n");
    printf("# - ociVersion\n# - process: user/args/env/cwd/capabilities\n");
    printf("# - root\n# - mounts: /proc\n# - linux.namespaces: pid/network/uts/ipc/mount\n\n");
    printf("{\n");
    printf("  \"ociVersion\": \"1.0.2\",\n");
    printf("  \"process\": {\"user\": {\"uid\": 0, \"gid\": 0}, \"args\": [\"/bin/sh\"], \"env\": [], \"cwd\": \"/\",\n");
    printf("              \"capabilities\": {\"bounding\": [], \"effective\": [], \"permitted\": []}},\n");
    printf("  \"root\": {\"path\": \"rootfs\"},\n");
    printf("  \"mounts\": [{\"destination\": \"/proc\", \"type\": \"proc\", \"source\": \"proc\"}],\n");
    printf("  \"linux\": {\"namespaces\": [{\"type\":\"pid\"},{\"type\":\"network\"},{\"type\":\"uts\"},{\"type\":\"ipc\"},{\"type\":\"mount\"}]}\n");
    printf("}\n");
}

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s {spec|capabilities|lifecycle|minimal}\n", argv[0]);
        return 1;
    }
    if (!strcmp(argv[1], "spec"))         cmd_spec();
    else if (!strcmp(argv[1], "capabilities")) cmd_capabilities();
    else if (!strcmp(argv[1], "lifecycle"))    cmd_lifecycle();
    else if (!strcmp(argv[1], "minimal"))     cmd_minimal();
    else { fprintf(stderr, "unknown: %s\n", argv[1]); return 1; }
    return 0;
}

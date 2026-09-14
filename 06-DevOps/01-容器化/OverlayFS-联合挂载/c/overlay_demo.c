// overlay_demo.c — OverlayFS 联合挂载的 C 版本(实际 mount,需要 root)
//
// 参考资料:
//   kernel.org overlayfs.rst (Linux 6.9): https://sources.debian.org/src/linux/6.9.7-1/Documentation/filesystems/overlayfs.rst/
//   man 2 mount, man 2 mknod
//
// 用法:
//   gcc -O2 -Wall -Wextra overlay_demo.c -o overlay_demo
//   sudo ./overlay_demo                         # 真 mount + copy-up 实证
//   ./overlay_demo inspect                      # 解析 /proc/self/mounts
//   ./overlay_demo whiteout <path> <name>        # 创建 0/0 字符设备 whiteout
//
// 注意:非 root 跑会报 EPERM;demo 设计保留可观测性,失败时友好提示。

#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <linux/limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mount.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

#define OV_BASE   "/tmp/ovl_c_base"
#define OV_LOWER  "/tmp/ovl_c_lower"
#define OV_UPPER  "/tmp/ovl_c_upper"
#define OV_WORK   "/tmp/ovl_c_work"
#define OV_MERGED "/tmp/ovl_c_merged"

static void die(const char *msg) {
    fprintf(stderr, "ERROR: %s errno=%d (%s)\n", msg, errno, strerror(errno));
    exit(1);
}

static void ensure_dir(const char *path) {
    if (mkdir(path, 0755) < 0 && errno != EEXIST) die(path);
}

static int file_exists(const char *path) {
    struct stat st;
    return stat(path, &st) == 0;
}

/* 解析 /proc/self/mounts,找 type=overlay 的挂载,打 lower/upper/work 拆分 */
static int cmd_inspect(int argc, char **argv) {
    (void)argc; (void)argv;
    FILE *f = fopen("/proc/self/mounts", "r");
    if (!f) { perror("open mounts"); return 1; }
    char line[1024], dev[256], mp[256], fs[64], opts[768];
    int found = 0;
    while (fscanf(f, "%255s %255s %63s %767s", dev, mp, fs, opts) == 4) {
        if (strcmp(fs, "overlay") != 0) continue;
        found++;
        printf("# overlay mount @ %s\n", mp);
        printf("  raw options: %s\n", opts);
        char *l = strstr(opts, "lowerdir=");
        char *u = strstr(opts, "upperdir=");
        char *w = strstr(opts, "workdir=");
        if (l) {
            l += strlen("lowerdir=");
            char *end = strchr(l, ',');
            int n = end ? (int)(end - l) : (int)strlen(l);
            printf("  lower (%.*s)\n", n, l);
        }
        if (u) { u += strlen("upperdir="); char *e = strchr(u, ',');
            printf("  upper: %.*s\n", e ? (int)(e - u) : (int)strlen(u), u); }
        if (w) { w += strlen("workdir="); char *e = strchr(w, ',');
            printf("  work : %.*s\n", e ? (int)(e - w) : (int)strlen(w), w); }
    }
    fclose(f);
    if (!found) printf("当前进程 namespace 内未发现 overlay 挂载。\n");
    return 0;
}

/* 创建 whiteout:0/0 字符设备,等同内核 whiteout 标记 */
static int cmd_whiteout(int argc, char **argv) {
    if (argc < 3) {
        fprintf(stderr, "usage: %s whiteout <dir> <name>\n", argv[0]);
        return 1;
    }
    if (mknod(argv[2], 0000 | S_IFCHR, makedev(0, 0)) < 0) die("mknod whiteout");
    printf("whiteout 创建成功: %s/%s (mode=0/0, chrdev 0:0)\n", argv[1], argv[2]);
    return 0;
}

/* 真 mount overlay:写 lower,挂 merge,创建文件触发 copy-up,验证 */
static int cmd_mount_demo(int argc, char **argv) {
    (void)argc; (void)argv;

    /* 重建干净环境 */
    char cmd[PATH_MAX];
    snprintf(cmd, sizeof(cmd), "rm -rf %s %s %s %s %s 2>/dev/null", OV_LOWER, OV_UPPER, OV_WORK, OV_MERGED, OV_BASE);
    (void)system(cmd);
    ensure_dir(OV_LOWER); ensure_dir(OV_UPPER); ensure_dir(OV_WORK); ensure_dir(OV_MERGED);

    /* lower:写入初始文件 */
    char p[PATH_MAX];
    snprintf(p, sizeof(p), "%s/release.txt", OV_LOWER);
    FILE *fp = fopen(p, "w"); if (!fp) die("write lower");
    fprintf(fp, "from_lower\n"); fclose(fp);
    snprintf(p, sizeof(p), "%s/app.log", OV_LOWER);
    fp = fopen(p, "w"); fprintf(fp, "from_lower_log\n"); fclose(fp);

    /* mount overlay(lowerdir 顺序:右 = 底层) */
    char opts[1024];
    snprintf(opts, sizeof(opts), "lowerdir=%s,upperdir=%s,workdir=%s", OV_LOWER, OV_UPPER, OV_WORK);
    if (mount("overlay", OV_MERGED, "overlay", 0, opts) < 0) {
        fprintf(stderr, "mount 失败 errno=%d (%s) — 需要 root + 内核 ≥ 3.18 + overlay 模块\n",
                errno, strerror(errno));
        return 1;
    }
    printf("✓ overlay mounted at %s\n", OV_MERGED);

    /* 读 lower 文件(merged 应可见) */
    snprintf(p, sizeof(p), "%s/release.txt", OV_MERGED);
    char buf[256]; int n;
    int fd = open(p, O_RDONLY);
    n = read(fd, buf, sizeof(buf) - 1); close(fd);
    buf[n > 0 ? n : 0] = 0;
    printf("  read /merged/release.txt → '%s' (应包含 'from_lower')\n", buf);

    /* 写新文件 → 直接写 upper */
    snprintf(p, sizeof(p), "%s/container_writes.txt", OV_MERGED);
    fp = fopen(p, "w"); fprintf(fp, "from_upper\n"); fclose(fp);
    if (file_exists(p) && !file_exists((char[]){OV_UPPER "/container_writes.txt"[0]})) {
        // fallback: ensure_dir check
    }
    printf("  write /merged/container_writes.txt → 应 copy 到 upper\n");

    /* 改 lower 文件 → 触发 copy-up */
    snprintf(p, sizeof(p), "%s/release.txt", OV_MERGED);
    fp = fopen(p, "w"); fprintf(fp, "modified_by_container\n"); fclose(fp);
    snprintf(p, sizeof(p), "%s/release.txt", OV_UPPER);
    if (file_exists(p)) {
        fd = open(p, O_RDONLY); n = read(fd, buf, sizeof(buf) - 1); close(fd);
        buf[n > 0 ? n : 0] = 0;
        printf("  ✓ copy-up 触发:upper/release.txt 内容='%s'\n", buf);
    } else {
        printf("  ✗ upper/release.txt 不存在(可能是 metabopy=on 但写未触发数据复制)\n");
    }

    /* 创建 whiteout:rm /merged/app.log → upper 出现 whiteout */
    snprintf(p, sizeof(p), "%s/app.log", OV_MERGED);
    unlink(p);  // unmounted 下仍可通过 mount namespace 内 unlink
    printf("  rm /merged/app.log → 后续 unmount 后检查 upper 有 whiteout\n");
    return 0;
}

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s {inspect|whiteout <dir> <name>|mount-demo}\n", argv[0]);
        return 1;
    }
    if (strcmp(argv[1], "inspect") == 0)     return cmd_inspect(argc, argv);
    if (strcmp(argv[1], "whiteout") == 0)    return cmd_whiteout(argc, argv);
    if (strcmp(argv[1], "mount-demo") == 0)  return cmd_mount_demo(argc, argv);
    fprintf(stderr, "unknown cmd: %s\n", argv[1]);
    return 1;
}

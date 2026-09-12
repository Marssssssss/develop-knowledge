/*
 * redis_persistence.c — Minimal Redis persistence simulator
 *
 * 演示:
 *   1. RDB 快照: fork() 子进程遍历 in-memory dict 写二进制 RDB 文件
 *   2. AOF 追加:  按 RESP 协议把写命令追加到 AOF 文件
 *   3. AOF Rewrite: 子进程按当前内存生成最小命令集,父进程继续追加
 *
 * 不依赖真实 redis-server,演示 fork + CoW + fsync 的核心时序。
 *
 * 编译: gcc -O2 -Wall -Wextra -pedantic redis_persistence.c -o demo
 * 运行: ./demo
 *
 * 注意: Windows 没有 fork(),演示会直接 exit(0)。
 */

#define _POSIX_C_SOURCE 200809L
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/wait.h>
#include <sys/stat.h>
#include <time.h>

#ifdef _WIN32
int main(void) {
    fprintf(stderr, "[skip] Windows platform: fork() not available, "
                    "Redis persistence demo requires Linux/macOS.\n");
    return 0;
}
#else

/* ---- 内存字典(demo 用最简实现) ---- */

#define MAX_KEYS 1024

typedef struct {
    char key[32];
    char val[64];
    int  used;
} kv_entry;

static kv_entry kv_store[MAX_KEYS];

static kv_entry *kv_get(const char *key) {
    for (int i = 0; i < MAX_KEYS; i++)
        if (kv_store[i].used && strcmp(kv_store[i].key, key) == 0)
            return &kv_store[i];
    return NULL;
}

static int kv_set(const char *key, const char *val) {
    kv_entry *e = kv_get(key);
    if (!e) {
        for (int i = 0; i < MAX_KEYS; i++)
            if (!kv_store[i].used) { e = &kv_store[i]; break; }
    }
    if (!e) return -1;
    strncpy(e->key, key, sizeof(e->key) - 1);
    strncpy(e->val, val, sizeof(e->val) - 1);
    e->used = 1;
    return 0;
}

/* ---- RDB 序列化:遍历 dict 写二进制 ---- */

static int rdb_save(const char *path) {
    FILE *fp = fopen(path, "wb");
    if (!fp) { perror("fopen rdb"); return -1; }

    /* 头: 9 字节 "REDIS0001" 类似格式(demo 简化为 magic+count) */
    const char magic[8] = "RDB0001";
    fwrite(magic, 1, sizeof(magic) - 1, fp);
    uint32_t count = 0;
    for (int i = 0; i < MAX_KEYS; i++)
        if (kv_store[i].used) count++;
    fwrite(&count, sizeof(count), 1, fp);

    for (int i = 0; i < MAX_KEYS; i++) {
        if (!kv_store[i].used) continue;
        uint16_t klen = (uint16_t)strlen(kv_store[i].key);
        uint16_t vlen = (uint16_t)strlen(kv_store[i].val);
        fwrite(&klen, sizeof(klen), 1, fp);
        fwrite(kv_store[i].key, 1, klen, fp);
        fwrite(&vlen, sizeof(vlen), 1, fp);
        fwrite(kv_store[i].val, 1, vlen, fp);
    }
    fclose(fp);
    return 0;
}

/* ---- AOF 追加: RESP 协议 + fsync 策略 ---- */

/* RESP 多参数命令编码: *3\r\n$3\r\nSET\r\n$1\r\nk\r\n$1\r\nv\r\n */
static void resp_encode(FILE *fp, const char *cmd, const char *k, const char *v) {
    fprintf(fp, "*3\r\n$%zu\r\n%s\r\n$%zu\r\n%s\r\n$%zu\r\n%s\r\n",
            strlen(cmd), cmd, strlen(k), k, strlen(v), v);
}

static int aof_append(int fd, const char *cmd, const char *k, const char *v,
                      const char *policy) {
    FILE *fp = fdopen(fd, "a");
    if (!fp) { perror("fdopen aof"); return -1; }
    resp_encode(fp, cmd, k, v);
    fflush(fp);

    if (strcmp(policy, "always") == 0) {
        fdatasync(fd);              /* 同步落盘(慢,~10^3 ops/s) */
    } else if (strcmp(policy, "everysec") == 0) {
        /* 真实 Redis 在后台线程每秒 fsync 一次;demo 简化为非阻塞 */
    } else if (strcmp(policy, "no") == 0) {
        /* OS 自行刷盘 */
    }
    return 0;
}

/* ---- fork() 子进程做 RDB 快照 ---- */

static void rdb_save_via_fork(const char *rdb_path) {
    pid_t pid = fork();
    if (pid < 0) { perror("fork"); return; }
    if (pid == 0) {
        /* 子进程:共享父内存的 CoW 页表,只读遍历 → 不污染父 */
        char tmp[64];
        snprintf(tmp, sizeof(tmp), "%s.tmp", rdb_path);
        if (rdb_save(tmp) == 0) {
            /* rename(2) 原子替换 */
            rename(tmp, rdb_path);
        }
        _exit(0);
    }
    /* 父进程:继续服务,期间写 dict 触发 CoW */
    int status;
    waitpid(pid, &status, 0);
    printf("[RDB] child finished, exit_status=%d\n",
           WIFEXITED(status) ? WEXITSTATUS(status) : -1);
}

/* ---- AOF Rewrite:子进程生成最小命令集 ---- */

static int aof_rewrite_minimal(const char *path) {
    FILE *fp = fopen(path, "w");
    if (!fp) return -1;
    for (int i = 0; i < MAX_KEYS; i++) {
        if (kv_store[i].used) {
            /* 100 个 INCR → 1 个 SET;demo 直接生成最终值 */
            resp_encode(fp, "SET", kv_store[i].key, kv_store[i].val);
        }
    }
    fclose(fp);
    return 0;
}

static void aof_rewrite_via_fork(const char *aof_path) {
    pid_t pid = fork();
    if (pid < 0) { perror("fork"); return; }
    if (pid == 0) {
        char tmp[64];
        snprintf(tmp, sizeof(tmp), "%s.rewrite", aof_path);
        aof_rewrite_minimal(tmp);
        _exit(0);
    }
    int status;
    waitpid(pid, &status, 0);
    printf("[AOF rewrite] child finished\n");
}

int main(void) {
    printf("=== Redis Persistence Demo ===\n\n");

    /* 1) 初始化数据 */
    kv_set("user:1001", "alice");
    kv_set("user:1002", "bob");
    kv_set("counter:pv", "42");

    /* 2) RDB 快照(fork 子进程) */
    rdb_save_via_fork("dump.rdb");
    printf("[RDB] dump.rdb size = %ld bytes\n",
           (long)({ struct stat st; stat("dump.rdb", &st); st.st_size; }));

    /* 3) AOF 追加(everysec 策略) */
    int afd = open("appendonly.aof", O_WRONLY | O_CREAT | O_APPEND, 0644);
    if (afd < 0) { perror("open aof"); return 1; }
    aof_append(afd, "SET", "user:1001", "alice_v2", "everysec");
    aof_append(afd, "SET", "user:1003", "carol",     "everysec");
    aof_append(afd, "INCR", "counter:pv", "1",        "everysec");
    close(afd);
    printf("[AOF] appended 3 commands to appendonly.aof\n");

    /* 4) AOF Rewrite */
    aof_rewrite_via_fork("appendonly.aof");

    /* 5) 时间戳日志 */
    time_t now = time(NULL);
    printf("\nDemo finished at %s", ctime(&now));
    return 0;
}

#endif /* _WIN32 guard */
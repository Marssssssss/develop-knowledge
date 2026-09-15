/* matching_algo.h — 匹配算法: 排队-配对-对战-更新循环, 与队伍平衡
 *
 * 与 python/matchmaking.py 的 simulate / team_split 对应。
 */
#ifndef MATCHING_ALGO_H
#define MATCHING_ALGO_H

#include "rating.h"

#define MAX_PLAYERS 128
#define MAX_QUEUE   8192
#define MAX_TEAM    32

typedef struct {
    int    pid;
    int    arrive;      /* 入队 tick */
    double skill;       /* 隐藏的真实技能(只有它决定胜负) */
    Rating r;           /* 系统侧的 (mu, sigma) 估计 */
    int    idle_after;  /* 冷却结束 tick */
} MPlayer;

typedef struct {
    int    matches;             /* 对局数 */
    double wait;                /* 平均等待 tick */
    double gap;                 /* 平均 |d展示分| */
    double true_gap;            /* 平均 |d真技能| —— 真正衡量"公不公平" */
    double quality;             /* 平均匹配质量 */
    double tg_early, tg_late;   /* 前/后半程的 |d真技能|(看收敛效应) */
    int    left;                /* 结束时仍滞留队列的人数 */
    double sigma;               /* 结束时全体平均 sigma */
} SimResult;

SimResult simulate(int n_players, int ticks, double base, double rate,
                   int by_quality, unsigned int seed, int cooldown);

/* 队伍平衡: 输出 贪心 / 局部搜索 / 精确最优(2^n 枚举) 的两队总分差 */
void team_split(const double *ratings, int n,
                double *greedy_diff, double *ls_diff, double *exact_diff);

#endif /* MATCHING_ALGO_H */

/* rating.h — 评分模型: Elo 与 TrueSkill(常量 / 类型 / 接口)
 *
 * 编译见 matchmaking.c 顶部。数值与 python/rating.py、go/rating.go 一致:
 *   TrueSkill: 新玩家 mu=25, sigma=8.333, 展示分 = mu - 3*sigma = 0;
 *   Elo: 期望胜率 E = 1/(1+10^((Rb-Ra)/400))。
 */
#ifndef RATING_H
#define RATING_H

#define MU0        25.0
#define SIGMA0     (25.0 / 3.0)
#define BETA       (SIGMA0 / 2.0)     /* 表现围绕技能波动 */
#define TAU        (SIGMA0 / 100.0)   /* 赛前 sigma 微增("动量") */
#define K_DISPLAY  3.0                /* 保守估计 mu - 3*sigma */
#define ELO_K      32.0

/* 技能信念: 均值 mu + 不确定性 sigma */
typedef struct { double mu, sigma; } Rating;

/* 线性同余发生器: 与 Python/Go 同一序列, 保证跨语言结果可比 */
typedef struct { unsigned int s; } Rng;

void   rng_init(Rng *r, unsigned int seed);
unsigned int rng_u32(Rng *r);
double rng_u(Rng *r);
double rng_normal(Rng *r);

double norm_pdf(double x);
double norm_cdf(double x);

/* ---- Elo ---- */
double elo_expect(double ra, double rb);
void   elo_update(double ra, double rb, double score_a, double *na, double *nb);

/* ---- TrueSkill ---- */
double ts_display(double mu, double sigma);
void   ts_1v1(Rating win, Rating lose, Rating *nw, Rating *nl);
double match_quality(Rating a, Rating b);

#endif /* RATING_H */

/* rating.c — 评分模型的实现(ElO / TrueSkill / 匹配质量)。
 *
 * 与 python/rating.py 逐行对应; 概率密度 phi 与分布函数 Phi 用 libm 的
 * exp/erf 直接算, 不引入第三方依赖。
 */
#include "rating.h"

#include <math.h>

#define SQRT2PI 2.5066282746310002

void rng_init(Rng *r, unsigned int seed)
{
    r->s = seed & 0xFFFFFFFFu;
}

unsigned int rng_u32(Rng *r)
{
    r->s = (1664525u * r->s + 1013904223u) & 0xFFFFFFFFu;
    return r->s;
}

double rng_u(Rng *r)
{
    return (double)rng_u32(r) / 4294967296.0;
}

/* Irwin-Hall: 4 个均匀分布之和近似标准正态(均值 0, 标准差 1) */
double rng_normal(Rng *r)
{
    return (rng_u(r) + rng_u(r) + rng_u(r) + rng_u(r) - 2.0) * sqrt(3.0);
}

double norm_pdf(double x)
{
    return exp(-x * x / 2.0) / SQRT2PI;
}

double norm_cdf(double x)
{
    return 0.5 * (1.0 + erf(x / sqrt(2.0)));
}

/* ---------------------------------------------------------------- Elo */
double elo_expect(double ra, double rb)
{
    return 1.0 / (1.0 + pow(10.0, (rb - ra) / 400.0));
}

void elo_update(double ra, double rb, double score_a, double *na, double *nb)
{
    double ea = elo_expect(ra, rb);
    *na = ra + ELO_K * (score_a - ea);
    *nb = rb + ELO_K * ((1.0 - score_a) - (1.0 - ea));
}

/* ---------------------------------------------------- TrueSkill */
double ts_display(double mu, double sigma)
{
    return mu - K_DISPLAY * sigma;
}

/* 双人 1v1 贝叶斯更新: 先加动量, 再按胜负更新。
 * c^2 = 2*beta^2 + sigma_w^2 + sigma_l^2 是总方差;
 * t = (mu_w - mu_l)/c; v = phi(t)/Phi(t); w = v*(v+t)。 */
void ts_1v1(Rating win, Rating lose, Rating *nw, Rating *nl)
{
    double sw = sqrt(win.sigma * win.sigma + TAU * TAU);
    double sl = sqrt(lose.sigma * lose.sigma + TAU * TAU);
    double c2 = 2.0 * BETA * BETA + sw * sw + sl * sl;
    double c = sqrt(c2);
    double t = (win.mu - lose.mu) / c;
    double v = norm_pdf(t) / norm_cdf(t);
    double w = v * (v + t);

    nw->mu = win.mu + sw * sw / c * v;
    nw->sigma = sqrt(sw * sw * (1.0 - sw * sw / c2 * w));
    nl->mu = lose.mu - sl * sl / c * v;
    nl->sigma = sqrt(sl * sl * (1.0 - sl * sl / c2 * w));
}

/* 匹配质量 = (虚拟)平局概率, 取值 0(最差)~1(最好) */
double match_quality(Rating a, Rating b)
{
    double denom = 2.0 * BETA * BETA + a.sigma * a.sigma + b.sigma * b.sigma;
    double d = a.mu - b.mu;
    return sqrt(2.0 * BETA * BETA / denom) * exp(-d * d / (2.0 * denom));
}

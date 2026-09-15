/* cc_impl.h — CUBIC 数学 + Reno/CUBIC 状态机 + 模拟驱动（实现头）。
 *
 * 由 main.c 在原来那段代码的位置 #include 进来：文本级包含，所有函数仍是
 * 同一个翻译单元里的 static 定义，因此**不需要改 static、不需要写原型**，
 * 零链接风险（本机没有 C 工具链，这一步很重要）。
 *
 * 数值断言的**可跑**版本在 ../python/cc_model.py + ../python/main.py。
 */
static double cubic_k(double w_max, double cwnd_epoch) {
    double delta = w_max - cwnd_epoch;
    if (delta < 0.0) delta = 0.0;
    return cbrt(delta / C_CUBIC);
}

/* RFC 9438 §4.2 Figure 1：W_cubic(t) = C*(t-K)^3 + W_max
 * t < K 为凹段（增速递减、逼近平台），t > K 转凸（增速递增、探测新带宽） */
static double w_cubic(double t, double k, double w_max) {
    double d = t - k;
    return C_CUBIC * d * d * d + w_max;
}

/* ------------------------------------------------------------------ Reno */
typedef struct {
    double cwnd, ssthresh, t;
    int losses, rto_events;
} Reno;

static void reno_init(Reno *r) {
    r->cwnd = IW;
    r->ssthresh = INFINITY;
    r->t = 0.0;
    r->losses = r->rto_events = 0;
}

/* 推进 1 个 RTT；loss = 快重传(3 dup ACK)，timeout = RTO */
static void reno_round(Reno *r, int loss, int timeout) {
    r->t += RTT;
    if (timeout) {
        /* RFC 5681 §3.1 公式(4)：ssthresh = max(FlightSize/2, 2*SMSS) */
        r->ssthresh = r->cwnd / 2.0 > 2.0 ? r->cwnd / 2.0 : 2.0;
        r->cwnd = 1.0; /* LW = 1 个满尺寸段，回到慢启动 */
        r->rto_events++;
        r->losses++;
        return;
    }
    if (loss) {
        r->losses++;
        r->ssthresh = r->cwnd / 2.0 > 2.0 ? r->cwnd / 2.0 : 2.0;
        /* §3.2 规则 3/6：膨胀 3 段后再放气，净效果 = 窗口乘性减半 */
        r->cwnd = r->ssthresh;
        return;
    }
    if (r->cwnd < r->ssthresh) {
        r->cwnd *= 2.0; /* 慢启动：一个 RTT 内窗口翻倍 */
    } else {
        r->cwnd += 1.0; /* 拥塞避免：每 RTT +1 段（加性增） */
    }
}

/* ----------------------------------------------------------------- CUBIC */
typedef struct {
    double cwnd, ssthresh, t;
    double w_max, cwnd_prior, cwnd_epoch, t_epoch, w_est, alpha, k;
    int losses, rto_events, entered_ca, fast_convergence;
} Cubic;

static void cubic_init(Cubic *c, int fast_convergence) {
    memset(c, 0, sizeof(*c));
    c->cwnd = IW;
    c->ssthresh = INFINITY;
    c->fast_convergence = fast_convergence;
}

static void cubic_on_loss(Cubic *c, int timeout) {
    c->losses++;
    if (timeout) {
        /* §4.8：cwnd 按 Reno 降到 1 段，但 ssthresh 用 β_cubic；K 置 0，
         * W_max = 本阶段起始 cwnd。 */
        c->cwnd_prior = c->cwnd;
        c->ssthresh = c->cwnd * BETA_CUBIC > 2.0 ? c->cwnd * BETA_CUBIC : 2.0;
        c->cwnd = 1.0;
        c->rto_events++;
        c->w_max = c->cwnd;
        c->cwnd_epoch = c->cwnd;
        c->k = 0.0;
    } else {
        /* §4.7 fast convergence：拥塞时若 cwnd < W_max，说明饱和点在下移，
         * 主动多让出带宽 → W_max 再乘 (1+β)/2。 */
        if (c->fast_convergence && c->w_max > 0.0 && c->cwnd < c->w_max) {
            c->w_max = c->cwnd * (1.0 + BETA_CUBIC) / 2.0;
        } else {
            c->w_max = c->cwnd;
        }
        if (c->entered_ca) c->cwnd_prior = c->cwnd;
        c->cwnd *= BETA_CUBIC; /* §4.6 乘性减（不是 Reno 的 0.5） */
        c->cwnd_epoch = c->cwnd;
        c->ssthresh = c->cwnd > 2.0 ? c->cwnd : 2.0;
        c->k = cubic_k(c->w_max, c->cwnd_epoch);
    }
    c->entered_ca = 1;
    c->t_epoch = c->t;
    c->w_est = c->cwnd; /* §4.3：W_est 初值 = cwnd_epoch */
    c->alpha = ALPHA_CUBIC;
}

static void cubic_round(Cubic *c, int loss, int timeout) {
    c->t += RTT;
    if (loss || timeout) {
        cubic_on_loss(c, timeout);
        return;
    }
    if (c->cwnd < c->ssthresh) { /* 慢启动不变 */
        c->cwnd *= 2.0;
        return;
    }
    {
        double elapsed = c->t - c->t_epoch;
        double target = w_cubic(elapsed, c->k, c->w_max);
        double upper = 1.5 * c->cwnd; /* 上界：增速不超过慢启动 */
        if (target > upper) target = upper;
        if (target > c->cwnd) c->cwnd = target; /* 下界：增速非递减 */
        /* §4.3 Reno-friendly：W_est 线性增长，追上 cwnd_prior 后 α 降为 1 */
        c->w_est += c->alpha;
        if (c->w_est >= c->cwnd_prior) c->alpha = 1.0;
        if (c->w_est > c->cwnd) c->cwnd = c->w_est;
    }
}

/* ------------------------------------------------------------ 模拟驱动 */
/* 跑到第一次丢包，返回丢包前的 cwnd */
static double run_first_loss(void *cc, double sat_window, int is_reno,
                            long *rounds_out) {
    long r = 0;
    double w_before;
    double cwnd = is_reno ? ((Reno *)cc)->cwnd : ((Cubic *)cc)->cwnd;
    while (cwnd <= sat_window && r < 100000) {
        if (is_reno) {
            reno_round((Reno *)cc, 0, 0);
            cwnd = ((Reno *)cc)->cwnd;
        } else {
            cubic_round((Cubic *)cc, 0, 0);
            cwnd = ((Cubic *)cc)->cwnd;
        }
        r++;
    }
    w_before = cwnd;
    if (is_reno) {
        reno_round((Reno *)cc, 1, 0);
    } else {
        cubic_round((Cubic *)cc, 1, 0);
    }
    if (rounds_out) *rounds_out = r;
    return w_before;
}

/* 窗口为 w_max 时丢 1 段，测量爬回 w_max 所需的 RTT 数 */
static int recovery_rtts(double w_max, int is_reno, void *out_cc) {
    int n = 0;
    if (is_reno) {
        Reno *r = (Reno *)out_cc;
        reno_init(r);
        r->cwnd = w_max;
        reno_round(r, 1, 0);
        while (r->cwnd < w_max && n < 100000) {
            reno_round(r, 0, 0);
            n++;
        }
    } else {
        Cubic *c = (Cubic *)out_cc;
        cubic_init(c, 1);
        c->cwnd = w_max;
        cubic_round(c, 1, 0);
        while (c->cwnd < w_max && n < 100000) {
            cubic_round(c, 0, 0);
            n++;
        }
    }
    return n;
}

static double avg_mbps(const double *traj, int n) {
    double s = 0.0;
    int i;
    for (i = 0; i < n; i++) s += traj[i];
    return s / n * SMSS * 8 / RTT / 1e6;
}


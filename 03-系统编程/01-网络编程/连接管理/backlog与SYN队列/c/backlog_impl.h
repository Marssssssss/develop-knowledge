/* backlog_impl.h — 监听套接字状态机 + 离散事件日历（实现头）。
 *
 * 由 main.c 在原地 #include：文本级包含，保住同一个翻译单元，
 * 所有 static 定义与原型关系原样不变。
 *
 * 数值断言的可跑版本在 ../python/backlog_model.py + ../python/main.py。
 */
typedef struct {
    int backlog, somaxconn, tcp_max_syn_backlog;
    int syncookies, abort_on_overflow;
    int syn_q, accept_q;              /* 两条队列的当前长度 */
    /* 统计 */
    long syn_received, syn_dropped, syncookie_issued, syncookie_completed;
    long established, overflow, rst_sent, accepted, recovered, gave_up;
    int max_syn_q, max_accept_q;
    unsigned char *ignored;           /* 最终 ACK 曾被忽略的 client 标记 */
    int n_clients;
} Listener;

static int accept_limit(const Listener *l) {
    return l->backlog < l->somaxconn ? l->backlog : l->somaxconn;
}

static void listener_init(Listener *l, int n_clients, int backlog, int somaxconn,
                          int syn_backlog, int syncookies, int abort_on_overflow) {
    memset(l, 0, sizeof(*l));
    l->n_clients = n_clients;
    l->backlog = backlog;
    l->somaxconn = somaxconn;
    l->tcp_max_syn_backlog = syn_backlog;
    l->syncookies = syncookies;
    l->abort_on_overflow = abort_on_overflow;
    l->ignored = calloc((size_t)n_clients, 1);
}

/* 内核在连接建立过程中的动作 */
enum action {
    ACT_SYNACK_SENT = 0,   /* SYN 进半连接队列，回 SYN+ACK */
    ACT_SYNCOOKIE_SENT,    /* 半连接队列满 + syncookies 开启：状态编码进 seq */
    ACT_SYN_DROPPED,       /* 半连接队列满 + syncookies 关闭：丢 SYN */
    ACT_MOVED_TO_ACCEPT,   /* 最终 ACK 到达且全连接队列有位置 */
    ACT_RST,               /* 全连接队列满 + abort_on_overflow=1：回 RST */
    ACT_ACK_IGNORED        /* 全连接队列满 + 默认策略：静默忽略，等重传 */
};

/* 收到 SYN */
static enum action on_syn(Listener *l, int client) {
    l->syn_received++;
    if (l->syn_q < l->tcp_max_syn_backlog) {
        l->syn_q++;
        if (l->syn_q > l->max_syn_q) l->max_syn_q = l->syn_q;
        return ACT_SYNACK_SENT;
    }
    if (l->syncookies) {
        l->syncookie_issued++;
        return ACT_SYNCOOKIE_SENT;
    }
    l->syn_dropped++;
    return ACT_SYN_DROPPED;
}

/* 收到第三次握手的 ACK */
static enum action on_final_ack(Listener *l, int client) {
    if (l->syn_q > 0) l->syn_q--;
    if (l->accept_q < accept_limit(l)) {
        l->accept_q++;
        l->established++;
        if (l->accept_q > l->max_accept_q) l->max_accept_q = l->accept_q;
        if (l->syncookie_issued) l->syncookie_completed++;
        if (client < l->n_clients && l->ignored[client]) {
            l->ignored[client] = 0;
            l->recovered++;
        }
        return ACT_MOVED_TO_ACCEPT;
    }
    l->overflow++;
    if (l->abort_on_overflow) {
        l->rst_sent++;
        return ACT_RST;
    }
    if (client < l->n_clients) l->ignored[client] = 1;
    return ACT_ACK_IGNORED;
}

static void listener_accept(Listener *l) {
    if (l->accept_q > 0) {
        l->accept_q--;
        l->accepted++;
    }
}

/* -------------------------------------------------------------- 事件日历 */
typedef struct {
    long tick;
    int client, attempt, kind; /* kind: 0=SYN, 1=ACK */
} Event;

typedef struct {
    Event *v;
    int n, cap;
} Calendar;

static void cal_init(Calendar *c, int cap) {
    c->v = malloc(sizeof(Event) * (size_t)cap);
    c->n = 0;
    c->cap = cap;
}

static void cal_push(Calendar *c, long tick, int client, int attempt, int kind) {
    if (c->n >= c->cap) { c->cap *= 2; c->v = realloc(c->v, sizeof(Event) * (size_t)c->cap); }
    c->v[c->n].tick = tick;
    c->v[c->n].client = client;
    c->v[c->n].attempt = attempt;
    c->v[c->n].kind = kind;
    c->n++;
}

/* 真实 TCP 的重传是指数退避的（约 1s、2s、4s、8s、16s、32s）。
 * 用固定间隔重试是常见建模错误——它会让"自愈"几乎不可能发生。 */
static long retry_delay(int base, int attempt) {
    long d = base;
    for (int i = 0; i < attempt; i++) d *= 2;
    return d;
}

/* 跑一轮模拟。clients_per_tick > 1 即模拟洪泛/突发。 */
static void simulate(Listener *l, int n_clients, int accept_every, long ticks,
                     int syn_retx, int rtt_ticks, int clients_per_tick) {
    Calendar cal;
    long next_accept = accept_every;
    cal_init(&cal, n_clients * 8 + 64);
    for (int i = 0; i < n_clients; i++)
        cal_push(&cal, i / clients_per_tick, i, 0, 0);

    for (long t = 0; t < ticks; t++) {
        for (int i = 0; i < cal.n; i++) {
            if (cal.v[i].tick != t) continue;
            Event ev = cal.v[i];
            cal.v[i] = cal.v[cal.n - 1];
            cal.n--;
            i--;
            if (ev.kind == 0) {                     /* SYN 到达 */
                enum action a = on_syn(l, ev.client);
                if (a == ACT_SYN_DROPPED) {
                    if (ev.attempt >= SYNACK_RETRIES) l->gave_up++;
                    else cal_push(&cal, t + retry_delay(syn_retx, ev.attempt),
                                  ev.client, ev.attempt + 1, 0);
                } else {
                    cal_push(&cal, t + rtt_ticks, ev.client, 0, 1);
                }
            } else {                                /* 最终 ACK 到达 */
                enum action a = on_final_ack(l, ev.client);
                if (a == ACT_ACK_IGNORED) {
                    /* 服务端重传 SYN+ACK，客户端重新 ACK */
                    if (ev.attempt >= SYNACK_RETRIES) l->gave_up++;
                    else cal_push(&cal, t + retry_delay(syn_retx, ev.attempt),
                                  ev.client, ev.attempt + 1, 1);
                }
            }
        }
        if (t >= next_accept) {
            listener_accept(l);
            next_accept = t + accept_every;
        }
        if (cal.n == 0 && l->accept_q == 0) break;  /* 排空才收工 */
    }
    free(cal.v);
}

static void listener_free(Listener *l) { free(l->ignored); }


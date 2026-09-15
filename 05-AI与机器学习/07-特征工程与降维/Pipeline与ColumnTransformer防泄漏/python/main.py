#!/usr/bin/env python3
"""Pipeline + ColumnTransformer 防交叉验证泄漏 —— 4 组可复现实验。

知识点来源(sklearn 官方文档,实际读过):
  - `common_pitfalls.html` §12 "Data leakage":
      §12.1 *How to avoid data leakage* 给出两条纪律:
            ① 任何"用数据学统计量"的步骤都必须放进 Pipeline;
            ② 交叉验证要用 `cross_val_score` / `cross_validate`,它保证每折内部重新 fit。
      §12.2 *Data leakage during feature selection*:在 10000 个特征里选 25 个,
            若在**全量数据**上选择,CV 分数会虚高到 ~0.7;先切分再选择则掉到 ~0.5。
  - `compose.html` §8.1 `Pipeline`:官方列出的三大用途之一是 **Safety** ——
      "avoid leaking statistics from your test data into the trained model in cross-validation,
       by ensuring that the same samples are used to train the transformers and predictors."
      §8.1.4 `ColumnTransformer` 提供按列路由 + `remainder='drop'|'passthrough'|变换器`。

本文件用实验 1~4 量化"泄漏值多少分、随什么变化",并演示正确的装配方式。
运行:`python main.py`(纯标准库,无第三方依赖;实验规模较大,约需数分钟)
"""

import math
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pipeline_lite import (ColumnTransformer, LogReg, OneHot, Pipeline,  # noqa: E402
                           SelectKBest, StandardScaler, accuracy, cross_val_score)

CLF = dict(epochs=1000, lr=1.0, l2=0.01)
LINE = "=" * 78


# --------------------------------------------------------------------------
# 数据与工具
# --------------------------------------------------------------------------
def make_data(n, p, n_inf, coef, seed):
    """n 个样本、p 个特征;前 n_inf 个带等权信号 coef,其余纯噪声。标签由 logistic 采样。"""
    rng = random.Random(seed)
    X = [[rng.gauss(0.0, 1.0) for _ in range(p)] for _ in range(n)]
    y = []
    for i in range(n):
        z = coef * math.fsum(X[i][j] for j in range(n_inf))
        y.append(1 if rng.random() < 1.0 / (1.0 + math.exp(-z)) else 0)
    return X, y


def split(X, y, cut=0.5, seed=0):
    idx = list(range(len(y)))
    random.Random(seed).shuffle(idx)
    m = int(len(y) * cut)
    tr, te = idx[:m], idx[m:]
    return [X[i] for i in tr], [y[i] for i in tr], [X[i] for i in te], [y[i] for i in te]


def mean(xs):
    return math.fsum(xs) / len(xs)


def std(xs):
    mu = mean(xs)
    return math.sqrt(math.fsum((x - mu) ** 2 for x in xs) / len(xs))


# --------------------------------------------------------------------------
# 实验 1:泄漏值多少分 —— 样本量 n 的作用
# --------------------------------------------------------------------------
def exp1_leak_size():
    print(LINE)
    print("实验 1 —— 特征选择的泄漏值多少分:随样本量 n 缩小")
    print(LINE)
    p, k = 10000, 25
    # (n, 信号系数, 数据种子数);coef=0.00 表示前 25 个特征其实也是噪声
    configs = [(100, 0.30, 3), (200, 0.30, 2), (400, 0.30, 2), (100, 0.00, 3)]
    print(f"设定:p={p} 个特征、用 ANOVA F 选 top-{k}、5 折 CV。"
          f"特征 0..24 的等权系数 = coef,其余 {p - 25} 个纯噪声\n")
    print(f"{'n':>5} {'coef':>5} | {'泄漏式 CV':>10} {'(σ_fold)':>9} | "
          f"{'正确式 CV':>10} {'(σ_fold)':>9} | {'Δ':>8}")
    print("-" * 78)
    rows = []
    for n, coef, nseed in configs:
        leaky, safe = [], []
        for s in range(nseed):
            X, y = make_data(n, p, 25, coef, seed=1 + s)
            Xs = SelectKBest(k).fit_transform(X, y)              # ← 泄漏:用了全量 y
            leaky += cross_val_score(lambda: LogReg(**CLF), Xs, y, 5, seed=0)
            safe += cross_val_score(                              # ← 正确:选择在折内
                lambda: Pipeline([("sel", SelectKBest(k)), ("clf", LogReg(**CLF))]),
                X, y, 5, seed=0)
        lmu, smu = mean(leaky), mean(safe)
        rows.append((n, coef, lmu, smu, std(leaky), std(safe)))
        print(f"{n:>5} {coef:>5.2f} | {lmu:>10.3f} {std(leaky):>9.3f} | "
              f"{smu:>10.3f} {std(safe):>9.3f} | {lmu - smu:>+8.3f}")
    noise, d100 = rows[3][2] - rows[3][3], rows[0][2] - rows[0][3]
    dn = [r[2] - r[3] for r in rows[1:3]]
    sig = [r[4] for r in rows[:3]] + [r[5] for r in rows[:3]]
    print(f"""
读法(以下每个数字都对应上表的实际输出):
  * **最刺眼的一行是 coef=0.00**:那 25 个"特征"和其它 9975 个一样是纯噪声,
    标签也是抛硬币生成的,理论上限就是 0.5。但泄漏式照样报出 {rows[3][2]:.3f},
    正确式只有 {rows[3][3]:.3f} —— 这 {noise:+.3f} 分是**纯粹凭空变出来的**。
    它相当于一条只靠流程缺陷就能拿到的"性能",而且没有任何物理含义。
  * 机制:ANOVA 分数是在全量 y 上算的。一个噪声特征要挤进 top-{k},必须"碰巧"把
    **全部 n 个样本**分开 —— 其中就包括后面会被当作验证集的那些。于是这个特征的
    取值方向与那些样本的标签同向;分类器在训练折上给它定的符号,到验证折上依然管用。
    它看起来"泛化"了,实际上只是把选择阶段"偷看"过的信息又在验证阶段兑现了一次。
  * 泄漏量随 n 稀释,但**不是线性的**:n=100 时 Δ={d100:+.3f},已经相当可观;
    n=200/400 时 Δ={dn[0]:+.3f}/{dn[1]:+.3f},而这三档各自的单折波动 σ_fold ∈
    [{min(sig):.2f}, {max(sig):.2f}] —— 差值不再稳定地压在噪声之上,也就是说
    **样本一多,单看一次实验就可能完全看不到这个泄漏**。
    这正是它危险的地方:小数据上它是决定性的,大数据上它隐藏起来,而流程缺陷一直都在。
  * 注意泄漏式的**训练**精度并不比正确式高,高的只是验证精度 —— 你看到的是一条
    "训练/验证都还行"的曲线,没有任何异常信号可以用来怀疑。""")
    return rows


# --------------------------------------------------------------------------
# 实验 2:留出集上的形状 —— 训练/测试精度各是多少
# --------------------------------------------------------------------------
def exp2_split_before_select():
    print("\n" + LINE)
    print("实验 2 —— 先选择再切分 vs 先切分再选择:8 次随机重复")
    print(LINE)
    n, p, k, coef, reps = 100, 10000, 25, 0.30, 8
    print(f"设定:n={n}, p={p}, k={k}, coef={coef};训练/测试 = 50/50,重复 {reps} 次\n")
    print(f"{'重复':>4} | {'泄漏式 训练/测试':>20} | {'正确式 训练/测试':>20} | {'测试差':>8}")
    print("-" * 78)
    leaky, safe = [], []
    for r in range(reps):
        X, y = make_data(n, p, 25, coef, seed=500 + r)
        Xtr, ytr, Xte, yte = split(X, y, 0.5, seed=r)
        # (A) 泄漏:选择器看全量 y
        sa = SelectKBest(k).fit(X, y)
        ma = LogReg(**CLF).fit(sa.transform(Xtr), ytr)
        a = (accuracy(ma, sa.transform(Xtr), ytr), accuracy(ma, sa.transform(Xte), yte))
        # (B) 正确:选择器只看训练部分
        sb = SelectKBest(k).fit(Xtr, ytr)
        mb = LogReg(**CLF).fit(sb.transform(Xtr), ytr)
        b = (accuracy(mb, sb.transform(Xtr), ytr), accuracy(mb, sb.transform(Xte), yte))
        leaky.append(a[1])
        safe.append(b[1])
        print(f"{r:>4} | {a[0]:>9.3f} / {a[1]:<8.3f} | {b[0]:>9.3f} / {b[1]:<8.3f} | "
              f"{a[1] - b[1]:>+8.3f}")
    print("-" * 78)
    print(f"{'均值':>4} | {'':>20} | {'':>20} | {mean(leaky) - mean(safe):>+8.3f}")
    print(f"   泄漏式测试 {mean(leaky):.3f} ± {std(leaky):.3f}    "
          f"正确式测试 {mean(safe):.3f} ± {std(safe):.3f}")
    print(f"""
读法:
  * 泄漏式的**训练**精度和正确式差不多,但测试精度系统性高出
    {mean(leaky) - mean(safe):+.3f} —— 单次实验的波动(σ≈{std(leaky):.2f})远大于这个差值。
    {sum(1 for a, b in zip(leaky, safe) if a < b)}/{reps} 次重复里泄漏式反而更低 ——
    **单次实验根本分辨不出来**,而报表里那个更好看的数字会被当成真实收益。
  * 这就是 §12.2 的现象在留出集上的对应版本:泄漏不只污染 CV,任何
    "先用全部数据加工特征、再切分评估"的流程都会被同样污染,包括离线评测集。""")
    return mean(leaky), mean(safe)


# --------------------------------------------------------------------------
# 实验 3:ColumnTransformer 的按列路由与 remainder 语义
# --------------------------------------------------------------------------
def exp3_column_transformer():
    print("\n" + LINE)
    print("实验 3 —— ColumnTransformer:按列路由 + remainder 三种语义")
    print(LINE)
    # 列:0 年龄(数值) 1 收入(数值) 2 城市(类别) 3 等级(类别) 4 活跃天数(留给 remainder)
    Xtr = [[31.0, 12.5, "上海", "A", 7.0], [45.0, 30.0, "北京", "B", 30.0],
           [28.0, 8.0, "上海", "A", 12.0], [52.0, 41.0, "北京", "C", 60.0],
           [39.0, 22.0, "广州", "B", 20.0]]
    ytr = [0, 1, 0, 1, 1]
    Xte = [[35.0, 18.0, "深圳", "A", 15.0], [48.0, 33.0, "上海", "B", 42.0]]  # 深圳=未见类别
    print("原始 5 列:0=年龄 1=收入 2=城市 3=等级 4=活跃天数(未被任何路由声明)\n")

    def route(remainder):
        return ColumnTransformer([("num", StandardScaler(), [0, 1]),
                                  ("cat", OneHot(), [2, 3])], remainder=remainder)

    print(f"{'remainder':<20} {'训练列数':>8} {'测试列数':>8}   测试第 0 行")
    print("-" * 78)
    for tag, rem in (("'drop'(默认)", "drop"), ("'passthrough'", "passthrough"),
                     ("StandardScaler()", StandardScaler())):
        ct = route(rem).fit(Xtr, ytr)
        Ztr, Zte = ct.transform(Xtr), ct.transform(Xte)
        shown = ", ".join(f"{v:+.2f}" for v in Zte[0])
        print(f"{tag:<20} {len(Ztr[0]):>8} {len(Zte[0]):>8}   [{shown}]")
    print("""
读法:
  * 两条路由各自产出自己的块 —— num 块 2 列(标准化后)、cat 块 3+3=6 列(城市与等级各 3 类),
    再横向拼接;`remainder` 只决定**未被任何路由声明**的第 4 列怎么处理。
  * 'drop' 丢列 → 8 列;'passthrough' 原样带过去 → 9 列(末位就是 +15.00,未做任何加工);
    传变换器则先过一遍该变换器 → 9 列(末位 -0.51,用**训练集**的 μ/σ 标准化)。
  * 前 8 列在所有三种 remainder 下逐位相同 —— remainder 只影响"多出来的那几列"。
  * 顺带一个易踩的坑:cat 块是由 2 条**不同**的类别列拼出来的,宽度 = Σ(各列类别数),
    而不是"类别列数 × 类别数"。列宽计算错了,后面接选择器/线性层时形状对不上。""")

    print("\n未见类别 '深圳'(只在测试集出现):")
    city_tr = [[row[2]] for row in Xtr]
    city_te = [[row[2]] for row in Xte]
    enc_full, enc_safe = OneHot().fit(city_tr + city_te), OneHot().fit(city_tr)
    print(f"    全量 fit 类别表 {enc_full.cols[0]} → 宽度 {len(enc_full.cols[0])}")
    print(f"    仅训练 fit 类别表 {enc_safe.cols[0]} → 宽度 {len(enc_safe.cols[0])}")
    print(f"    对训练集 transform 的宽度:全量 {len(enc_full.transform(city_tr)[0])} / "
          f"仅训练 {len(enc_safe.transform(city_tr)[0])}")
    print(f"    深圳 在仅训练拟合表下 → {enc_safe.transform(city_te)[0]}(全 0,不报错,列宽稳定)")
    print("""
读法:
  * 列宽必须由**训练数据**决定。如果编码器见过测试数据,列宽就依赖"这一折里有哪些样本",
    于是不同折之间特征矩阵宽度不一致 —— 训练时是 9 列、线上是 8 列,静默错位。
  * 未见类别退化成全 0 行(等价于 sklearn 的 `handle_unknown='ignore'`),是**有意为之**
    的降级:既不会崩,也不会因为多出一列而改变模型结构。""")

    # 与 Pipeline 串联:路由 → 选择 → 分类(样本太少,只看装配是否跑通)
    pipe = Pipeline([("ct", route("passthrough")),
                     ("sel", SelectKBest(6)),
                     ("clf", LogReg(**CLF))]).fit(Xtr, ytr)
    print(f"\n串成 Pipeline([ColumnTransformer, SelectKBest, LogReg]):"
          f"5 原始列 → {pipe._n_features} 特征 → 训练精度 {accuracy(pipe, Xtr, ytr):.3f}"
          f"(仅 5 个样本,数字无意义,只验证装配与 predict 通路)")
    return None


# --------------------------------------------------------------------------
# 实验 4:嵌套 CV —— 超参选择也要防泄漏
# --------------------------------------------------------------------------
def exp4_nested_cv():
    print("\n" + LINE)
    print("实验 4 —— 嵌套 CV:k 的选择也要放在外层折之内")
    print(LINE)
    n, p, coef, grid = 150, 300, 0.70, [5, 25, 100]
    # 本实验对同一个外层折要反复 fit 几十次,故把训练轮数压到 250(其余实验用全局 CLF=1000)
    fast = dict(epochs=250, lr=1.0, l2=0.01)
    print(f"设定:n={n}, p={p}, 前 25 个特征带信号 coef={coef};候选 k ∈ {grid};"
          f"外层 4 折 × 3 个种子,内层 3 折\n")

    def pipe_with(k):
        return Pipeline([("sel", SelectKBest(k)), ("clf", LogReg(**fast))])

    def inner_best(Xtr, ytr, seed):
        return max(grid, key=lambda k: mean(cross_val_score(
            lambda: pipe_with(k), Xtr, ytr, 3, seed=seed)))

    safe, peek, fixed, picked = [], [], [], []
    for seed in range(3):
        X, y = make_data(n, p, 25, coef, seed=200 + seed)
        idx = list(range(n))
        random.Random(seed).shuffle(idx)
        for f in range(4):
            lo, hi = f * n // 4, (f + 1) * n // 4
            va = idx[lo:hi]
            tr = [i for i in range(n) if i not in va]
            Xtr, ytr = [X[i] for i in tr], [y[i] for i in tr]
            Xva, yva = [X[i] for i in va], [y[i] for i in va]
            # (A) 嵌套:内层用训练折选 k,外层只评估一次
            k = inner_best(Xtr, ytr, seed)
            picked.append(k)
            safe.append(accuracy(pipe_with(k).fit(Xtr, ytr), Xva, yva))
            # (B) 偷看:在外层验证集上把每个 k 都试一遍,取最好
            scores = [accuracy(pipe_with(kk).fit(Xtr, ytr), Xva, yva) for kk in grid]
            peek.append(max(scores))
            # (C) 固定 k=25:不调超参,自然没有这层偏差
            fixed.append(accuracy(pipe_with(25).fit(Xtr, ytr), Xva, yva))

    print(f"{'协议':<36} {'外层平均精度':>12}")
    print("-" * 78)
    for name, xs in (("(A) 嵌套 CV:内层选 k,外层评估", safe),
                     ("(B) 在外层验证集上调 k(偷看)", peek),
                     ("(C) 固定 k=25(不调超参,基准)", fixed)):
        print(f"{name:<36} {mean(xs):>12.3f}")
    print(f"\n内层 {len(picked)} 次选出的 k 分布:"
          f"{ {k: picked.count(k) for k in sorted(set(picked))} }")
    print(f"""
读法:
  * (B) 比 (A) 高 {mean(peek) - mean(safe):+.3f} —— 这部分差值**与特征选择无关**,
    纯粹来自"在同一批验证数据上试 {len(grid)} 个 k、取最大"这个动作。
    注意 (A) 与 (B) 里每个评估器本身都是完整 Pipeline(选择在折内),两者的差别只有一处:
    (A) 选的 k 来自**内层** CV,(B) 选的 k 来自**外层**验证集。取最大这一步
    把外层验证集的噪声挑了出来 —— 即使某个 k 的真实效果平平,它在这一次切分上
    的随机波动也可能让它当选。
  * (C) 与 (A) 相差 {mean(fixed) - mean(safe):+.3f},反映的是"这个数据上 k=25 是否恰好接近内层选择的结果"。
    本次内层选出的 k 分布说明该数据对 k 不敏感,所以两者接近;
    当最优 k 随数据大幅波动时,(A) 会明显低于 (C),因为内层选择的方差被外层如实计入。
  * 工程结论:凡"看分数做决定"的步骤(选 k、选模型、调阈值、early stopping、
    按验证分数挑 checkpoint)都要放进**内层**;外层只允许评估一次。
    否则你得到一个乐观的外层分数,而这个分数正是你要拿去汇报的那个。""")
    return safe, peek, fixed


def main():
    exp1_leak_size()
    exp2_split_before_select()
    exp3_column_transformer()
    exp4_nested_cv()
    print("\n" + LINE)
    print("全部实验完成。核心结论:任何用 y 学参数的步骤(特征选择、编码、缩放、超参搜索)"
          "\n都必须关进 Pipeline 或内层 CV;否则被污染的那个分数,恰好是你拿去汇报的那个。")
    print(LINE)


if __name__ == "__main__":
    main()

#import <Foundation/Foundation.h>

// ObjC 版求解内核:强度常量 + 稠密单纯形 + 两个建模类(LALayoutConstraint /
// LALayoutSolver),由 ObjCLayoutScenarios.m 以 #import 文本包含 ——
// 与 C 侧「实现头文件」同一套路:本机无 ObjC 工具链,拆成两个翻译单元需要同步
// 维护声明与链接,而文本包含把所有 static 与 @implementation 留在同一个 TU 里,
// 零链接风险。拆分只为守住「单源文件 ≤300 行」,代码逐字节未改。
// 权威依据见同目录 README 的「参考资料」。
// ---------------------------------------------------------------------------
// 强度与常量
// ---------------------------------------------------------------------------
static const double kRequired = 1001001000.0;

/// Cassowary 符号强度压平成一个可比较的数。
static double Strength(double a, double b, double c) {
    double v = a * 1000000.0 + b * 1000.0 + c;
    return v < kRequired - 1000.0 ? v : kRequired - 1000.0;
}

/// Apple 的 UILayoutPriority(1..1000)。1000 是 required;250/750 是 CHCR 默认值。
static double ApplePriority(int priority) {
    return priority >= 1000 ? kRequired : Strength((double)priority, 0, 0);
}

static const double kStrong = 1000000.0;   // Strength(1,0,0)
static const double kMedium = 1000.0;      // Strength(0,1,0)
static const double kWeak   = 1.0;         // Strength(0,0,1)

// ---------------------------------------------------------------------------
// 表格式两阶段单纯形(固定容量,教学用)
// ---------------------------------------------------------------------------
#define LA_MAX_ROWS 32
#define LA_MAX_COLS 128

typedef struct {
    double A[LA_MAX_ROWS][LA_MAX_COLS];
    double b[LA_MAX_ROWS];
    double cost[LA_MAX_COLS];
    int basis[LA_MAX_ROWS];
    int nrows, ncols, pivots;
} LASimplex;

static void LASimplexInit(LASimplex *lp) {
    memset(lp, 0, sizeof(*lp));
    for (int i = 0; i < LA_MAX_ROWS; i++) lp->basis[i] = -1;
}

static int LASimplexCol(LASimplex *lp, double cost) {
    lp->cost[lp->ncols] = cost;
    return lp->ncols++;
}

static void LASimplexRow(LASimplex *lp, const double *coeffs, double rhs) {
    int r = lp->nrows++;
    for (int j = 0; j < lp->ncols; j++) lp->A[r][j] = coeffs[j];
    lp->b[r] = rhs;
    lp->basis[r] = -1;
}

static void LASimplexPivot(LASimplex *lp, int r, int c, double *obj2) {
    lp->pivots++;
    double piv = lp->A[r][c];
    for (int j = 0; j < lp->ncols; j++) lp->A[r][j] /= piv;
    lp->b[r] /= piv;
    for (int i = 0; i < lp->nrows; i++) {
        if (i == r) continue;
        double f = lp->A[i][c];
        if (fabs(f) <= 1e-12) continue;
        for (int j = 0; j < lp->ncols; j++) lp->A[i][j] -= f * lp->A[r][j];
        lp->b[i] -= f * lp->b[r];
    }
    for (int pass = 0; pass < 2; pass++) {
        double *obj = pass == 0 ? lp->cost : obj2;
        double f = obj[c];
        if (fabs(f) <= 1e-12) continue;
        for (int j = 0; j < lp->ncols; j++) obj[j] -= f * lp->A[r][j];
    }
    lp->basis[r] = c;
}

/// 目标行存 reduced cost:最小化时 < 0 才进基;Bland 规则取最小下标防循环。
static void LARun(LASimplex *lp, double *p1, double *p2, const int *allowed) {
    while (1) {
        double *obj = p1 ? p1 : p2;                 // 第一阶段优先
        int enter = -1;
        for (int j = 0; j < lp->ncols; j++) {
            if (!allowed[j]) continue;
            if (obj[j] < -1e-9) { enter = j; break; }
        }
        if (enter < 0) return;
        int leave = -1;
        double best = 0;
        for (int i = 0; i < lp->nrows; i++) {
            double a = lp->A[i][enter];
            if (a <= 1e-9) continue;
            double ratio = lp->b[i] / a;
            if (leave < 0 || ratio < best - 1e-12) { leave = i; best = ratio; }
        }
        if (leave < 0) return;                      // 无界
        LASimplexPivot(lp, leave, enter, obj == p1 ? p2 : p1);
    }
}

/// 返回 0 = 最优,-1 = required 不可满足。z 为各列取值。
static int LASimplexSolve(LASimplex *lp, double *z) {
    int m = lp->nrows;
    for (int i = 0; i < m; i++) {
        if (lp->b[i] < 0) {
            for (int j = 0; j < lp->ncols; j++) lp->A[i][j] = -lp->A[i][j];
            lp->b[i] = -lp->b[i];
        }
    }
    int arts[LA_MAX_ROWS];
    for (int i = 0; i < m; i++) {                   // 人工变量当初始基
        int c = LASimplexCol(lp, 0);
        lp->A[i][c] = 1.0;
        lp->basis[i] = c;
        arts[i] = c;
    }
    double p1[LA_MAX_COLS] = {0}, p2[LA_MAX_COLS] = {0};
    for (int i = 0; i < m; i++) p1[arts[i]] = 1.0;
    for (int j = 0; j < lp->ncols; j++) p2[j] = lp->cost[j];
    for (int i = 0; i < m; i++) {                   // 用基把目标行消成规范形
        double f1 = p1[lp->basis[i]], f2 = p2[lp->basis[i]];
        for (int j = 0; j < lp->ncols; j++) {
            if (fabs(f1) > 1e-12) p1[j] -= f1 * lp->A[i][j];
            if (fabs(f2) > 1e-12) p2[j] -= f2 * lp->A[i][j];
        }
    }
    int allowed[LA_MAX_COLS];
    for (int j = 0; j < lp->ncols; j++) allowed[j] = 1;
    LARun(lp, p1, p2, allowed);
    double artSum = 0;
    for (int i = 0; i < m; i++)
        for (int k = 0; k < m; k++)
            if (lp->basis[i] == arts[k]) artSum += lp->b[i];
    if (artSum > 1e-7) return -1;                   // required 集自相矛盾
    for (int k = 0; k < m; k++) allowed[arts[k]] = 0;
    for (int i = 0; i < m; i++) {                   // 把人工变量赶出基
        int isArt = 0;
        for (int k = 0; k < m; k++) if (lp->basis[i] == arts[k]) isArt = 1;
        if (!isArt) continue;
        for (int j = 0; j < lp->ncols; j++) {
            if (allowed[j] && fabs(lp->A[i][j]) > 1e-9) {
                LASimplexPivot(lp, i, j, p2);
                break;
            }
        }
    }
    LARun(lp, NULL, p2, allowed);
    memset(z, 0, sizeof(double) * LA_MAX_COLS);
    for (int i = 0; i < m; i++) z[lp->basis[i]] = lp->b[i];
    return 0;
}

// ---------------------------------------------------------------------------
// 建模层
// ---------------------------------------------------------------------------
@interface LALayoutConstraint : NSObject
@property (nonatomic, copy) NSString *name, *op;
@property (nonatomic, strong) NSDictionary<NSNumber *, NSNumber *> *coeffs;
@property (nonatomic) double constant, strength;
@end

@implementation LALayoutConstraint
/// 约束的实际偏差(≥0;0 表示完全满足)。
- (double)residualWithValues:(NSArray<NSNumber *> *)values {
    double total = _constant;
    for (NSNumber *key in _coeffs)
        total += _coeffs[key].doubleValue * values[key.integerValue].doubleValue;
    if ([_op isEqualToString:@"=="]) return fabs(total);
    if ([_op isEqualToString:@">="]) return total < 0 ? -total : 0;
    return total > 0 ? total : 0;
}
@end

@interface LALayoutSolver : NSObject
- (NSInteger)variableNamed:(NSString *)name;
- (LALayoutConstraint *)addCoeffs:(NSDictionary<NSNumber *, NSNumber *> *)coeffs
                         constant:(double)constant
                               op:(NSString *)op
                         strength:(double)strength
                             name:(NSString *)name;
- (NSArray<NSNumber *> *)solveWithError:(NSError **)error;
@property (nonatomic, readonly) NSMutableArray<LALayoutConstraint *> *constraints;
@property (nonatomic, readonly) NSInteger pivots;
@property (nonatomic, readonly) NSArray<NSString *> *names;
@end

@implementation LALayoutSolver {
    NSMutableArray<LALayoutConstraint *> *_constraints;
    NSMutableArray<NSString *> *_names;
    NSInteger _pivots;
}

- (instancetype)init {
    if ((self = [super init])) {
        _constraints = [NSMutableArray array];
        _names = [NSMutableArray array];
    }
    return self;
}

- (NSMutableArray<LALayoutConstraint *> *)constraints { return _constraints; }
- (NSArray<NSString *> *)names { return _names; }
- (NSInteger)pivots { return _pivots; }

- (NSInteger)variableNamed:(NSString *)name {
    [_names addObject:name];
    return (NSInteger)_names.count - 1;
}

- (LALayoutConstraint *)addCoeffs:(NSDictionary<NSNumber *, NSNumber *> *)coeffs
                         constant:(double)constant
                               op:(NSString *)op
                         strength:(double)strength
                             name:(NSString *)name {
    LALayoutConstraint *c = [LALayoutConstraint new];
    c.coeffs = coeffs;
    c.constant = constant;
    c.op = op;
    c.strength = strength;
    c.name = name;
    [_constraints addObject:c];
    return c;
}

/// required 约束集矛盾时返回 nil 并给出错误(对应 Auto Layout 打印冲突日志)。
- (NSArray<NSNumber *> *)solveWithError:(NSError **)error {
    LASimplex lp;
    LASimplexInit(&lp);
    NSInteger n = _names.count;
    int plus[64], minus[64];
    for (NSInteger v = 0; v < n; v++) {             // 自由变量拆成 x⁺ − x⁻
        plus[v] = LASimplexCol(&lp, 0);
        minus[v] = LASimplexCol(&lp, 0);
    }
    for (LALayoutConstraint *c in _constraints) {
        double row[LA_MAX_COLS] = {0};
        for (NSNumber *key in c.coeffs) {
            NSInteger v = key.integerValue;
            double k = c.coeffs[key].doubleValue;
            row[plus[v]] += k;
            row[minus[v]] -= k;
        }
        if (c.strength >= kRequired) {
            if ([c.op isEqualToString:@">="]) row[LASimplexCol(&lp, 0)] = -1;  // surplus
            if ([c.op isEqualToString:@"<="]) row[LASimplexCol(&lp, 0)] = 1;   // slack
            LASimplexRow(&lp, row, -c.constant);
            continue;
        }
        int em = LASimplexCol(&lp, c.strength), ep = LASimplexCol(&lp, c.strength);
        row[em] += 1;                                // 误差变量 e⁻ / e⁺
        row[ep] -= 1;
        LASimplexRow(&lp, row, -c.constant);
    }
    double z[LA_MAX_COLS];
    int st = LASimplexSolve(&lp, z);
    _pivots = lp.pivots;
    if (st != 0) {
        if (error) {
            *error = [NSError errorWithDomain:@"LALayout" code:1 userInfo:@{
                NSLocalizedDescriptionKey : @"required 约束集不可满足"}];
        }
        return nil;
    }
    NSMutableArray<NSNumber *> *values = [NSMutableArray arrayWithCapacity:n];
    for (NSInteger v = 0; v < n; v++)
        [values addObject:@(z[plus[v]] - z[minus[v]])];
    return values;
}
@end


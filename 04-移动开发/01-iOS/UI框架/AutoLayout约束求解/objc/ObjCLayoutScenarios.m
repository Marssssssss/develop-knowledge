// Auto Layout 约束求解(Cassowary 型)—— Objective-C 版。
//
// 权威依据:
//   * Apple《Auto Layout Guide》*Anatomy of a Constraint*
//     item1.attribute1 = multiplier × item2.attribute2 + constant;优先级 1..1000
//     (1000 = required);CHCR 默认 Hugging 250 / CompressionResistance 750;
//     方程反转:乘数取倒数、常量取反。
//   * Badros / Borning / Stuckey《The Cassowary Linear Arithmetic Constraint
//     Solving Algorithm》(TOCHI 8(4), 2001):slack 化不等式、error 变量承载偏差、
//     误差加权和最小、edit / stay 约束。
//
// 与 Swift / Python 版同题同结果(50/200/300 与 80/220)。本版用固定大小的
// 稠密表(double A[行][列])而不是逐行 NSMutableArray —— ObjC 里这样最短也最好读。
//
// 运行: clang -fobjc-arc -framework Foundation ObjCLayoutSolver.m -o layout && ./layout

#import <Foundation/Foundation.h>

#import "LALayoutSolver.h"
// ---------------------------------------------------------------------------
// 场景自检
// ---------------------------------------------------------------------------
static int gPass = 0, gFail = 0;

static void Check(NSString *label, BOOL ok, NSString *detail) {
    ok ? gPass++ : gFail++;
    printf("  [%s] %s%s\n", ok ? "PASS" : "FAIL", label.UTF8String,
           detail.length ? [@"   " stringByAppendingString:detail].UTF8String : "");
}

static BOOL Near(double a, double b, double tol) { return fabs(a - b) <= tol; }

int main(void) {
    @autoreleasepool {
        printf("[1] 强度刻度与 Apple 优先级映射\n");
        Check(@"required > strong > medium > weak > 0",
              kRequired > kStrong && kStrong > kMedium && kMedium > kWeak, @"");
        Check(@"apple(250) < apple(750) < apple(1000)=required",
              ApplePriority(250) < ApplePriority(750) && ApplePriority(1000) == kRequired, @"");

        printf("[2] 可伸缩行(required 定骨架 + weak 定偏好)\n");
        LALayoutSolver *s = [LALayoutSolver new];
        NSInteger w = [s variableNamed:@"windowWidth"];
        NSInteger l1 = [s variableNamed:@"box1.left"], r1 = [s variableNamed:@"box1.right"];
        NSInteger l2 = [s variableNamed:@"box2.left"], r2 = [s variableNamed:@"box2.right"];
        [s addCoeffs:@{@(w) : @1} constant:-300 op:@"==" strength:kRequired name:@"windowWidth=300"];
        [s addCoeffs:@{@(l1) : @1} constant:0 op:@"==" strength:kRequired name:@"box1.left=0"];
        [s addCoeffs:@{@(r2) : @1, @(w) : @-1} constant:0 op:@"==" strength:kRequired name:@"box2.right=windowWidth"];
        [s addCoeffs:@{@(l2) : @1, @(r1) : @-1} constant:0 op:@">=" strength:kRequired name:@"box2.left>=box1.right"];
        [s addCoeffs:@{@(r1) : @1, @(l1) : @-1} constant:0 op:@">=" strength:kRequired name:@"width1>=0"];
        [s addCoeffs:@{@(r2) : @1, @(l2) : @-1} constant:0 op:@">=" strength:kRequired name:@"width2>=0"];
        [s addCoeffs:@{@(r1) : @1, @(l1) : @-1} constant:-50 op:@"==" strength:kWeak name:@"prefer width1=50"];
        [s addCoeffs:@{@(r2) : @1, @(l2) : @-1} constant:-100 op:@"==" strength:kWeak name:@"prefer width2=100"];
        NSArray<NSNumber *> *v = [s solveWithError:NULL];
        Check(@"box1.right = 50", Near(v[r1].doubleValue, 50), v[r1].stringValue);
        Check(@"box2.left = 200", Near(v[l2].doubleValue, 200), v[l2].stringValue);
        Check(@"box2.right = 300", Near(v[r2].doubleValue, 300), v[r2].stringValue);
        Check(@"总宽守恒 50 + 150 + 100 = 300",
              Near(v[r2].doubleValue - v[l1].doubleValue, 300), @"");

        printf("[3] 优先级冲突\n");
        LALayoutSolver *s3 = [LALayoutSolver new];
        NSInteger x = [s3 variableNamed:@"x"];
        [s3 addCoeffs:@{@(x) : @1} constant:-100 op:@"==" strength:kRequired name:@"required width=100"];
        LALayoutConstraint *weak = [s3 addCoeffs:@{@(x) : @1} constant:-50 op:@"==" strength:kWeak name:@"weak width=50"];
        NSArray<NSNumber *> *v3 = [s3 solveWithError:NULL];
        Check(@"required 100 压过 weak 50 → x=100", Near(v3[x].doubleValue, 100), v3[x].stringValue);
        Check(@"weak 被牺牲,偏差 = 50", Near([weak residualWithValues:v3], 50),
              [NSString stringWithFormat:@"%.0f", [weak residualWithValues:v3]]);

        printf("[4] CHCR:等 hugging 歧义 → 拉开优先级后唯一解\n");
        NSMutableArray<LALayoutConstraint *> *cons = [NSMutableArray array];
        void (^build)(LALayoutSolver *, int, int) = ^(LALayoutSolver *solver, int labelHug, int fieldHug) {
            NSInteger a = [solver variableNamed:@"label.width"];
            NSInteger b = [solver variableNamed:@"field.width"];
            [cons addObject:[solver addCoeffs:@{@(a) : @1, @(b) : @1} constant:-300 op:@"==" strength:kRequired name:@"a+b=300"]];
            [cons addObject:[solver addCoeffs:@{@(a) : @1} constant:-80 op:@">=" strength:ApplePriority(750) name:@"label CR 750"]];
            [cons addObject:[solver addCoeffs:@{@(a) : @1} constant:-80 op:@"<=" strength:ApplePriority(labelHug) name:@"label Hug"]];
            [cons addObject:[solver addCoeffs:@{@(b) : @1} constant:-100 op:@">=" strength:ApplePriority(750) name:@"field CR 750"]];
            [cons addObject:[solver addCoeffs:@{@(b) : @1} constant:-100 op:@"<=" strength:ApplePriority(fieldHug) name:@"field Hug"]];
        };
        LALayoutSolver *s4 = [LALayoutSolver new];
        build(s4, 250, 250);
        NSArray<NSNumber *> *v4 = [s4 solveWithError:NULL];
        Check(@"等 hugging 时解落在 80..220", v4[0].doubleValue >= 80 - 1e-6 && v4[0].doubleValue <= 220 + 1e-6,
              [NSString stringWithFormat:@"label=%.0f field=%.0f", v4[0].doubleValue, v4[1].doubleValue]);
        double costA = 0, costB = 0;
        for (LALayoutConstraint *c in cons) {
            if (c.strength >= kRequired) continue;
            double rA = [c residualWithValues:@[@80, @220]];
            double rB = [c residualWithValues:@[@200, @100]];
            costA += c.strength * rA;
            costB += c.strength * rB;
        }
        Check(@"两个端点解代价相同 → 目标函数平坦,布局歧义", Near(costA, costB, 1.0),
              [NSString stringWithFormat:@"%.3g vs %.3g", costA, costB]);
        NSMutableArray<LALayoutConstraint *> *cons2 = [cons copy];
        LALayoutSolver *s5 = [LALayoutSolver new];
        build(s5, 251, 250);
        NSArray<NSNumber *> *v5 = [s5 solveWithError:NULL];
        Check(@"label Hug 251 → label 保持内在宽 80", Near(v5[0].doubleValue, 80), v5[0].stringValue);
        Check(@"剩余空间归 textField → 220", Near(v5[1].doubleValue, 220), v5[1].stringValue);
        Check(@"field 的 Hugging 被违反 120 点",
              Near([cons2[4] residualWithValues:v5], 120),
              [NSString stringWithFormat:@"%.0f", [cons2[4] residualWithValues:v5]]);

        printf("[5] 编辑约束 + stay 约束\n");
        LALayoutSolver *s6 = [LALayoutSolver new];
        NSMutableArray<NSNumber *> *m = [NSMutableArray array];
        for (int i = 0; i < 4; i++) {
            NSInteger idx = [s6 variableNamed:[NSString stringWithFormat:@"m%d", i]];
            [m addObject:@(idx)];
            [s6 addCoeffs:@{@(idx) : @1} constant:-(100.0 * (i + 1)) op:@"==" strength:kWeak
                      name:[NSString stringWithFormat:@"stay(m%d)", i]];
        }
        NSArray<NSNumber *> *v6 = [s6 solveWithError:NULL];
        BOOL allStay = YES;
        for (int i = 0; i < 4; i++)
            if (!Near(v6[m[i].integerValue].doubleValue, 100.0 * (i + 1))) allStay = NO;
        Check(@"未拖动时 stay 全部满足", allStay, @"");
        LALayoutConstraint *drag = [s6 addCoeffs:@{@(m[0].integerValue) : @1} constant:-250 op:@"=="
                                        strength:kStrong name:@"drag m0"];
        NSArray<NSNumber *> *v6b = [s6 solveWithError:NULL];
        Check(@"拖动后 m0 = 250(strong 压过 weak stay)",
              Near(v6b[m[0].integerValue].doubleValue, 250), v6b[m[0].integerValue].stringValue);
        Check(@"其余点纹丝不动",
              Near(v6b[m[1].integerValue].doubleValue, 200) && Near(v6b[m[2].integerValue].doubleValue, 300) &&
              Near(v6b[m[3].integerValue].doubleValue, 400), @"");
        printf("         拖动由 strong 强度的 edit 约束承载:改常量即可拖动,不必重建约束集\n");

        printf("[6] required 冲突检测\n");
        LALayoutSolver *s7 = [LALayoutSolver new];
        NSInteger q = [s7 variableNamed:@"q"];
        [s7 addCoeffs:@{@(q) : @1} constant:-10 op:@"==" strength:kRequired name:@"q=10"];
        [s7 addCoeffs:@{@(q) : @1} constant:-20 op:@"==" strength:kRequired name:@"q=20"];
        NSError *err = nil;
        NSArray<NSNumber *> *v7 = [s7 solveWithError:&err];
        Check(@"矛盾的 required 被检测出来(对应 Auto Layout 冲突日志)", v7 == nil && err != nil,
              err.localizedDescription ?: @"");

        printf("\n断言 %d 通过 / %d 失败\n", gPass, gFail);
    }
    return gFail == 0 ? 0 : 1;
}

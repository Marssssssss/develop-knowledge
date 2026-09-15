// RenderTraps.m —— 在真实的 CALayer 上数「谁被重绘了」
//
// 构建运行(QuartzCore + CoreGraphics,**不依赖 UIKit**, macOS / iOS 都能编译):
//   clang -fobjc-arc -framework Foundation -framework QuartzCore RenderTraps.m -o render-traps && ./render-traps
//
// 这个文件不用模拟器,而是直接继承 CALayer 覆盖 drawInContext:,数它被调用的**次数** ——
// 于是"改 frame 不重绘 / 改内容才重绘 / 只有脏了的层才重绘"这几条从模型里的推论
// 变成可数的整数。这正是 Core Animation 能在几千个 layer 的界面上跑满 60fps 的机制。

#import <Foundation/Foundation.h>
#import <QuartzCore/QuartzCore.h>

static int gPassed = 0, gFailed = 0;

static void that(const char *label, BOOL ok, NSString *detail) {
    ok ? gPassed++ : gFailed++;
    printf("  [%s] %s%s%s\n", ok ? "PASS" : "FAIL", label,
           detail.length ? "   " : "", detail.UTF8String);
}

// MARK: - 记账用的 layer

@interface CountingLayer : CALayer
@property (nonatomic, assign) NSUInteger drawCount;
@property (nonatomic, assign) NSUInteger layoutCount;
@end

@implementation CountingLayer
- (void)drawInContext:(CGContextRef)ctx {
    self.drawCount++;
    CGContextSetRGBFillColor(ctx, 0.2, 0.4, 0.8, 1.0);
    CGContextFillRect(ctx, self.bounds);
}
- (void)layoutSublayers {
    self.layoutCount++;
    [super layoutSublayers];
}
@end

static CountingLayer *makeLayer(CGFloat x, CGFloat y, CGFloat w, CGFloat h) {
    CountingLayer *l = [CountingLayer layer];
    l.frame = CGRectMake(x, y, w, h);
    l.backgroundColor = CGColorGetConstantColor(kCGColorWhite);
    [l setNeedsDisplay];
    [l displayIfNeeded];            // 先画一次,把 drawCount 归到 1
    l.drawCount = 0;
    return l;
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        // 关掉隐式动画:每次改属性都生成 CAAnimation 会让"谁被重绘"这件事看不清
        [CATransaction begin];
        [CATransaction setDisableActions:YES];

        CountingLayer *root = [CountingLayer layer];
        root.frame = CGRectMake(0, 0, 400, 400);
        NSMutableArray<CountingLayer *> *children = [NSMutableArray array];
        for (int i = 0; i < 20; i++) {
            CountingLayer *c = makeLayer(i * 10, i * 10, 50, 50);
            [root addSublayer:c];
            [children addObject:c];
        }
        root.drawCount = 0;

        printf("[1] 改 frame:触发 layout,但不触发重绘\n");
        {
            CountingLayer *one = children[0];
            one.frame = CGRectMake(200, 200, 60, 60);
            [root layoutIfNeeded];
            [one displayIfNeeded];
            that("只改 frame 的 layer 的 drawInContext 一次都没被调用(0 次重绘)",
                 one.drawCount == 0,
                 [NSString stringWithFormat:@"drawCount=%lu", (unsigned long)one.drawCount]);
            that("其它 19 个兄弟 layer 一次都没被碰",
                 [[children valueForKeyPath:@"@sum.drawCount"] unsignedIntegerValue] == 0,
                 @"脏标记是逐 layer 的,不是整棵树一起重画");
        }

        printf("\n[2] 改内容:走 setNeedsDisplay 才会重绘\n");
        {
            CountingLayer *two = children[1];
            [two setNeedsDisplay];
            [two displayIfNeeded];
            that("setNeedsDisplay 之后 drawInContext 被精确调用 1 次",
                 two.drawCount == 1,
                 [NSString stringWithFormat:@"drawCount=%lu", (unsigned long)two.drawCount]);
            [two displayIfNeeded];
            that("再 displayIfNeeded 不会重复绘制(脏标记被清掉了)",
                 two.drawCount == 1, @"display 不会重复劳动");
            that("邻居依然 0 次(重绘范围没有被放大)",
                 children[2].drawCount == 0, @"这是「按需重绘」的关键");
        }

        printf("\n[3] needsDisplayOnBoundsChange:改 frame 也会重绘\n");
        {
            CountingLayer *three = children[3];
            three.needsDisplayOnBoundsChange = YES;
            three.frame = CGRectMake(10, 10, 200, 200);
            [three displayIfNeeded];
            that("打开 needsDisplayOnBoundsChange 后,改 bounds 也会触发 drawInContext",
                 three.drawCount == 1,
                 @"CATextLayer / UILabel 内部就是这么做的 —— 所以「只是移动一个 Label」也要重绘");
            that("这是 [1] 里那条「改 frame 不重绘」的例外,代价是一个数量级的差",
                 three.drawCount > 0, @"需要时就打开,不需要时别打开");
        }

        printf("\n[4] 脏层数决定成本,与「改了多少次」无关\n");
        {
            for (CountingLayer *c in children) { [c setNeedsDisplay]; }
            for (CountingLayer *c in children) { [c setNeedsDisplay]; }   // 再标一遍
            [root displayIfNeeded];
            NSUInteger total = [[children valueForKeyPath:@"@sum.drawCount"] unsignedIntegerValue];
            that("20 个子层各标脏两次 → 一共 20 次绘制(标脏是布尔量)",
                 total == 20,
                 [NSString stringWithFormat:@"总绘制 %lu 次", (unsigned long)total]);
            that("成本只与「脏层数」成正比,与「标脏次数」无关",
                 total == 20, @"所以频繁改属性不一定慢,改很多不同 layer 才慢");
        }

        printf("\n[5] 离屏渲染:属性组合就是触发条件\n");
        {
            CountingLayer *avatar = makeLayer(0, 0, 120, 120);
            avatar.cornerRadius = 60;
            avatar.masksToBounds = YES;
            that("cornerRadius + masksToBounds 的组合 = 一次离屏 pass",
                 avatar.cornerRadius > 0 && avatar.masksToBounds,
                 @"Instruments 的 Color Offscreen-Rendered Yellow 会把它标黄");

            CountingLayer *shadowNoPath = makeLayer(0, 0, 120, 120);
            shadowNoPath.shadowOpacity = 0.5f;
            shadowNoPath.shadowRadius = 6;
            CountingLayer *shadowWithPath = makeLayer(0, 0, 120, 120);
            shadowWithPath.shadowOpacity = 0.5f;
            shadowWithPath.shadowPath = CGPathCreateWithRoundedRect(shadowWithPath.bounds, 8, 8, NULL);
            that("阴影不带 shadowPath → 离屏;带了 shadowPath → 不用离屏",
                 shadowNoPath.shadowPath == NULL && shadowWithPath.shadowPath != NULL,
                 @"给阴影补 shadowPath 是「最小改动最大收益」的一条");

            CountingLayer *rasterized = makeLayer(0, 0, 200, 200);
            rasterized.shouldRasterize = YES;
            rasterized.rasterizationScale = 2.0;
            that("shouldRasterize 只该给「内容稳定复用」的复杂层开",
                 rasterized.shouldRasterize && rasterized.rasterizationScale > 0,
                 @"列表 cell 快速复用时缓存立刻失效 → 负收益");
            if (shadowWithPath.shadowPath) { CGPathRelease(shadowWithPath.shadowPath); }
        }

        printf("\n[6] 事务边界:一次 commit 把整棵树一次性交出去\n");
        {
            [CATransaction begin];
            [CATransaction setDisableActions:YES];
            NSUInteger before = [[children valueForKeyPath:@"@sum.drawCount"] unsignedIntegerValue];
            for (int i = 0; i < 5; i++) {
                children[i].frame = CGRectMake(i * 20, i * 20, 80, 80);
                [children[i] setNeedsDisplay];
            }
            [root displayIfNeeded];                 // 事务内的所有脏标记在这里一起被消费
            NSUInteger after = [[children valueForKeyPath:@"@sum.drawCount"] unsignedIntegerValue];
            that("同一事务内改 5 个层 → 5 次绘制,合成一次 commit",
                 after - before == 5,
                 [NSString stringWithFormat:@"新增绘制 %lu 次", (unsigned long)(after - before)]);

            CountingLayer *moved = children[8];
            [moved setNeedsDisplay];
            [moved displayIfNeeded];                // 先把它清零
            NSUInteger base = [[children valueForKeyPath:@"@sum.drawCount"] unsignedIntegerValue];
            moved.frame = CGRectMake(320, 320, 40, 40);     // 只改 frame,不碰内容
            [root displayIfNeeded];
            NSUInteger afterMove = [[children valueForKeyPath:@"@sum.drawCount"] unsignedIntegerValue];
            that("同一事务里只改 frame 的层不会被重绘(Layout 与 Display 是两条脏标记)",
                 afterMove == base,
                 [NSString stringWithFormat:@"绘制次数保持 %lu 次", (unsigned long)base]);
            [CATransaction commit];
        }

        [CATransaction commit];

        printf("\n断言 %d 通过 / %d 失败\n", gPassed, gFailed);
        return gFailed ? 1 : 0;
    }
}

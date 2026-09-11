// Flutter 三棵树 — Demo 4: Box Constraint 模型(自顶向下约束 + 自底向上尺寸)
//
// 权威资料(docs.flutter.dev/resources/architectural-overview):
//   "To perform layout, Flutter walks the render tree in a depth-first traversal
//    and passes down size constraints from parent to child."
//   "Children respond by passing up a size to their parent object within the
//    constraints the parent established."
//   "The box constraint model is very powerful as a way to layout objects in
//    O(n) time"
//
// 三类约束传播:
//   1. tight: minW==maxW 且 minH==maxH (强制子节点用该尺寸,例如 RenderView 把屏幕塞给子)
//   2. loose: min==0, max 有限 (子节点可自由取尺寸,不能超 max)
//   3. bounded: min/max 都有限 (子节点必须在范围内)
//
// 跑法:dart dart/box_constraint_model.dart

import 'dart:math';

class BoxConstraints {
  const BoxConstraints({
    this.minWidth = 0,
    this.maxWidth = double.infinity,
    this.minHeight = 0,
    this.maxHeight = double.infinity,
  });
  final double minWidth;
  final double maxWidth;
  final double minHeight;
  final double maxHeight;

  static const tight = BoxConstraints(minWidth: 100, maxWidth: 100, minHeight: 50, maxHeight: 50);
  static const loose = BoxConstraints(maxWidth: 200, maxHeight: 100);

  bool get isTight => minWidth == maxWidth && minHeight == maxHeight;
  bool get hasBoundedWidth => maxWidth < double.infinity;
  bool get hasBoundedHeight => maxHeight < double.infinity;

  @override
  String toString() => 'C(w:$minWidth..$maxWidth, h:$minHeight..$maxHeight)';
}

// 模拟 "child wants to be WxH, but constrained by parent constraints"
Size fitWithin(BoxConstraints c, double desiredW, double desiredH) {
  return Size(
    clampDouble(desiredW, c.minWidth, c.maxWidth),
    clampDouble(desiredH, c.minHeight, c.maxHeight),
  );
}

double clampDouble(double x, double lo, double hi) => max(lo, min(hi, x));

// 模拟 LayoutBuilder:让 child 看到约束后自己决定如何布局
typedef LayoutBuilder = Size Function(BoxConstraints constraints);

// 4 个典型子 widget 布局策略(对应 Flutter 源码中 RenderPadding/RenderAlign/RenderConstrainedBox)
Size fixedSizeLayout(double w, double h) =>
    (c) => fitWithin(c, w, h);

Size fillMaxLayout(BoxConstraints c) =>
    Size(c.maxWidth, c.maxHeight);

Size looseMaxLayout(BoxConstraints c) =>
    Size(min(200, c.maxWidth), min(100, c.maxHeight));

Size centerLayout(BoxConstraints c) {
  // 占满父约束,表示"我要这么大,但内容居中"
  return Size(c.maxWidth, c.maxHeight);
}

// ---------- demo ----------
void main() {
  print('=== Flutter Box Constraint Model Demo ===\n');

  // 场景 1:RenderView → 屏幕是 tight (1080x1920)
  final screen = BoxConstraints.tight.copyWith() // 模拟 tight(1080, 1920)
      .__override(maxWidth: 1080, maxHeight: 1920);
  print('[Root] RenderView tight constraint: $screen  isTight=${screen.isTight}');

  // 场景 2:tight constraint 透传给 child → child size 被强制
  final tightChild = fillMaxLayout(screen);
  print('[Tight pass-through] child size = $tightChild  '
      '(must match parent exactly — no choice)');

  // 场景 3:loose constraint → child 可自定尺寸
  final loose = BoxConstraints.loose;
  print('\n[Loose] parent constraint: $loose  isTight=${loose.isTight}');
  print('  child option A (200x100): ${looseMaxLayout(loose)}  -> fits');
  print('  child option B (500x500): '
      '${fitWithin(loose, 500, 500)}  -> clamped to (200,100)');

  // 场景 4:典型"父设最大宽,子固定宽" — 子宽由子决定
  final bounded = BoxConstraints(maxWidth: 500, maxHeight: 300);
  print('\n[Bounded] parent: $bounded');
  print('  child wants 200x100: ${fitWithin(bounded, 200, 100)}  -> kept');
  print('  child wants 800x600: ${fitWithin(bounded, 800, 600)}  -> clamped');

  // 场景 5:LayoutBuilder 决策
  print('\n[LayoutBuilder: react to constraints at runtime]');
  Size adaptive(BoxConstraints c) {
    if (c.maxWidth < 600) return fitWithin(c, 200, 200); // single column
    return fitWithin(c, 800, 400); // two columns
  }

  for (final w in [400.0, 800.0, 1200.0]) {
    final c = BoxConstraints(maxWidth: w);
    print('  width=$w  -> ${adaptive(c)}');
  }

  // 场景 6:深度优先遍历 + 约束自顶向下 / 尺寸自底向上 — 伪代码演示
  print('\n[DFS layout walk]');
  print('  RenderView  constraints=1080x1920(TIGHT)');
  print('    Container   constraints=0..1080 x 0..1920 (loose)');
  print('      Row       constraints=0..1080 x 0..1920');
  print('        TextA   constraints=0..360 x 0..1920 -> size 100x50 (fits)');
  print('        TextB   constraints=360..720 x 0..1920 -> size 200x50');
  print('        TextC   constraints=720..1080 x 0..1920 -> size 100x50');
  print('      Row.size = 1080 x 50 (max child height)');
  print('    Container.size = 1080 x 50 (from row)');
  print('  RenderView paints the entire tree');
  print('\n  Layout = 1 DFS walk per frame in dirty regions (not always the whole tree).');
  print('  Time complexity: O(n) total — n = # of render objects in dirty set.');

  print('\n[Constraint gotchas]');
  print('  - unbounded height in Column inside ScrollView is fine; without scroll → layout error');
  print('  - SizedBox.expand uses tight(max=infinity, min=infinity) — only inside bounded parent');
  print('  - IntrinsicHeight/Width forces double-pass: 1) measure intrinsic, 2) layout for real');
}

// BoxConstraints helper:__override (testing helper for tight-with-different-max)
extension on BoxConstraints {
  BoxConstraints __override({double? minWidth, double? maxWidth, double? minHeight, double? maxHeight}) {
    return BoxConstraints(
      minWidth: minWidth ?? this.minWidth,
      maxWidth: maxWidth ?? this.maxWidth,
      minHeight: minHeight ?? this.minHeight,
      maxHeight: maxHeight ?? this.maxHeight,
    );
  }
}
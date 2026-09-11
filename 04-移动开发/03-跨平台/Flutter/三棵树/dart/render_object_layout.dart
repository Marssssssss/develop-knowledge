// Flutter 三棵树 — Demo 3: RenderObject + Layout(布局计算与约束传递)
//
// 权威资料(docs.flutter.dev/resources/architectural-overview):
//   "The rendering layer provides an abstraction for dealing with layout. With
//    this layer, you can build a tree of renderable objects."
//   "During the build phase, Flutter creates or updates an object that inherits
//    from RenderObject for each RenderObjectElement in the element tree."
//
// RenderObject 的两类派生:
//   - RenderBox: 笛卡尔坐标系下的二维盒子,大多数 Flutter widget 用它
//   - RenderSliver: 滚动懒加载专用(GridView/ListView 内部)
//
// RenderBox 核心方法:
//   - performLayout(): 接收父约束,计算自身大小,递归 layout 每个 child
//   - paint(): 把结果绘制到 Canvas(下一帧)
//
// 跑法:dart dart/render_object_layout.dart

import 'dart:math';

// ---------- BoxConstraints 模拟 ----------
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

  static const BoxConstraints tighten({required double width, required double height}) =>
      BoxConstraints(minWidth: width, maxWidth: width, minHeight: height, maxHeight: height);

  BoxConstraints enforce(BoxConstraints c) => BoxConstraints(
        minWidth: clampDouble(minWidth, c.minWidth, c.maxWidth),
        maxWidth: clampDouble(maxWidth, c.minWidth, c.maxWidth),
        minHeight: clampDouble(minHeight, c.minHeight, c.maxHeight),
        maxHeight: clampDouble(maxHeight, c.minHeight, c.maxHeight),
      );

  BoxConstraints loosen() => BoxConstraints(
        minWidth: 0,
        maxWidth: maxWidth,
        minHeight: 0,
        maxHeight: maxHeight,
      );

  bool get isTight => minWidth == maxWidth && minHeight == maxHeight;

  @override
  String toString() =>
      'BoxConstraints(w: $minWidth..$maxWidth, h: $minHeight..$maxHeight)';
}

double clampDouble(double x, double lo, double hi) => max(lo, min(hi, x));

// ---------- 模拟 RenderBox ----------
abstract class RenderBox {
  Size? size;
  BoxConstraints? _constraints;
  void performLayout() {
    // 子类实现:layout 子节点,记录 size
  }
  void paint() {
    // 实际绘制到 Canvas,本 demo 略
  }
}

// 一个固定大小的盒子 — 典型 RenderConstrainedBox
class RenderFixedBox extends RenderBox {
  RenderFixedBox(this.width, this.height);
  final double width;
  final double height;

  @override
  void performLayout() {
    // 收紧约束到自己的尺寸
    size = Size(width, height);
  }

  @override
  String toString() => 'RenderFixedBox(${width}x$height)';
}

// 一个充满父约束的盒子 — 典型 RenderSizedBox.expand
class RenderExpandedBox extends RenderBox {
  RenderExpandedBox(this.label);
  final String label;

  @override
  void performLayout() {
    // 充满父约束(假设父约束是 tight)
    final c = _constraints;
    if (c == null) throw StateError('performLayout called without parent constraints');
    size = Size(c.maxWidth, c.maxHeight);
  }

  @override
  String toString() => 'RenderExpandedBox("$label", size=$size)';
}

// 容器:接受一个 child,接受父约束,透传给 child
class RenderContainer extends RenderBox {
  RenderContainer(this.child);
  RenderBox? child;

  @override
  void performLayout() {
    final c = _constraints;
    if (c == null) throw StateError('no constraints');
    // 透传约束给 child
    child!._constraints = c.loosen();
    child!.performLayout();
    size = child!.size;
  }

  @override
  String toString() => 'RenderContainer(child=$child, size=$size)';
}

// 横向 Row:把 maxWidth 按比例分给 children,maxHeight 取最大
class RenderRow extends RenderBox {
  RenderRow(this.children, {required this.flexFactors});
  final List<RenderBox> children;
  final List<double> flexFactors;

  @override
  void performLayout() {
    final c = _constraints!;
    final totalFlex = flexFactors.fold<double>(0, (a, b) => a + b);
    final perFlex = c.maxWidth / totalFlex;
    double maxChildHeight = 0;
    for (var i = 0; i < children.length; i++) {
      final w = perFlex * flexFactors[i];
      children[i]._constraints =
          BoxConstraints.tighten(width: w, height: c.maxHeight);
      children[i].performLayout();
      maxChildHeight = max(maxChildHeight, children[i].size!.height);
    }
    size = Size(c.maxWidth, maxChildHeight);
  }

  @override
  String toString() =>
      'RenderRow(size=$size, children=${children.map((c) => c.size).toList()})';
}

// 用一个外部方法模拟 "parent calls child.performLayout() with constraints"
extension RenderBoxLayout on RenderBox {
  set constraints(BoxConstraints c) {
    performLayoutWith(c);
  }
  // private method to set _constraints
  void performLayoutWith(BoxConstraints c) {
    final ro = this as RenderBox;
    ro._setConstraints(c);
    ro.performLayout();
  }
}

extension on RenderBox {
  void _setConstraints(BoxConstraints c) {
    // 内部 setter
    (this as dynamic)._constraints = c;
  }
}

// ---------- demo ----------
void main() {
  print('=== Flutter RenderObject Layout Demo ===\n');

  // 场景 1:RenderView 是根,约束 = 屏幕 (tight: 1080x1920)
  final screen = BoxConstraints.tighten(width: 1080, height: 1920);
  print('[Root] RenderView constraints: $screen');

  // 场景 2:一棵 Row 含 3 个子节点,flex = [1, 2, 1] → 子节点宽度比 1:2:1
  final row = RenderRow(
    [
      RenderFixedBox(100, 50),  // 占位 — 但父约束是 tight,所以 size 由父决定
      RenderFixedBox(100, 50),
      RenderFixedBox(100, 50),
    ],
    flexFactors: [1, 2, 1],
  );

  // 父约束:宽 1080,高无限 → 实际 maxHeight 由最大子节点高度决定
  row.constraints = BoxConstraints(maxWidth: 1080, maxHeight: 1920);
  print('[After layout] row.size = ${row.size}  '
      '(each child width = 1080/4 × flex factor)');
  for (var i = 0; i < row.children.length; i++) {
    final c = row.children[i];
    print('  child[$i] = $c  layout-constraint = ${(c as dynamic)._constraints}');
  }

  print('\n[Box constraint model] (per docs.flutter.dev):');
  print('  - Top-down: parent passes constraints to children');
  print('  - Bottom-up: each child computes its size, returns to parent');
  print('  - Time complexity O(n) — one full traversal per layout');
  print('  - parent can force child size (tight constraints) or let child decide (loose)');

  print('\n[Key insight]');
  print('  - RenderObject.size is MUTABLE (set during performLayout)');
  print('  - RenderObject itself is mutable, but Widget is not');
  print('  - Most layout bugs come from misreading constraints (e.g. child ignores parent\'s maxHeight)');
}
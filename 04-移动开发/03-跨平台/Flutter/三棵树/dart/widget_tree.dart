// Flutter 三棵树 — Demo 1: Widget 树(不可变配置声明)
//
// 权威资料(docs.flutter.dev/resources/architectural-overview):
//   "In Flutter, widgets (akin to components in React) are represented by
//    immutable classes that are used to configure a tree of objects."
//   "any change to the widget tree ... causes a new set of widget objects to be
//    returned. But this doesn't mean the underlying representation has to be
//    rebuilt."
//
// 核心要点:
//   - Widget 是 immutable 配置对象,描述"我希望 UI 长什么样"
//   - state 变化时,build() 返回一棵新的 Widget 树(浅比较用 canUpdate / runtimeType / key)
//   - Widget 本身极轻量(只持 final 引用),频繁创建是 Flutter 设计的核心
//
// 跑法:dart dart/widget_tree.dart

import 'dart:convert';

// 模拟 Flutter Widget 基类:不可变 + 可以 == 比较
abstract class Widget {
  const Widget({this.key});
  final String? key;
  @override
  bool operator ==(Object other) {
    if (identical(this, other)) return true;
    return other.runtimeType == runtimeType && other is Widget && _propsEqual(other);
  }
  bool _propsEqual(Widget other) => true;
  @override
  int get hashCode => Object.hash(runtimeType, key);
}

class StatelessWidget extends Widget {
  const StatelessWidget({super.key});
  Widget build() => throw UnimplementedError();
}

// 用 toString 显示 widget 树的形状(类似 Flutter Inspector 的 render tree 输出)
String showTree(Widget w, [int depth = 0]) {
  final pad = '  ' * depth;
  final s = '$w';
  if (w is ContainerW) {
    return '$pad$s\n${w.child != null ? showTree(w.child!, depth + 1) : ''}';
  } else if (w is RowW) {
    return '$pad$s\n${w.children.map((c) => showTree(c, depth + 1)).join('\n')}';
  }
  return '$pad$s';
}

// ---------- 几种 widget(模拟 Flutter 风格) ----------
class ContainerW extends StatelessWidget {
  const ContainerW({super.key, this.color = '', this.child});
  final String color;
  final Widget? child;
  @override
  String toString() => 'Container(color=$color)';
  @override
  bool _propsEqual(Widget o) => o is ContainerW && o.color == color;
}

class TextW extends StatelessWidget {
  const TextW(this.data, {super.key});
  final String data;
  @override
  String toString() => 'Text("$data")';
  @override
  bool _propsEqual(Widget o) => o is TextW && o.data == data;
}

class RowW extends StatelessWidget {
  const RowW(this.children, {super.key});
  final List<Widget> children;
  @override
  String toString() => 'Row';
  @override
  bool _propsEqual(Widget o) => o is RowW && o.children.length == children.length;
}

// ---------- demo ----------
void main() {
  // 场景 1:第一次 build,产生一棵 widget 树
  final tree1 = ContainerW(
    color: 'blue',
    child: RowW([
      TextW('A'),
      TextW('B'),
      TextW('C'),
    ]),
  );
  print('=== Flutter Widget Tree Demo ===\n');
  print('[Build #1] (initial render)');
  print(showTree(tree1));

  // 场景 2:setState 触发 rebuild → build() 返回一棵新的 widget 树
  // 注意:tree1 不变(不可变),而是产生 tree2;新树和旧树 == 比较后,框架只更新差异
  final tree2 = ContainerW(
    color: 'blue', // 颜色没变 → canUpdate 返回 true → 子树可复用
    child: RowW([
      TextW('A'),
      TextW('B'),
      TextW('C'),
    ]),
  );
  print('\n[Build #2] (state update — color unchanged, text unchanged)');
  print(showTree(tree2));
  print('  tree1 == tree2 ? ${tree1 == tree2}  '
      '(runtimeType + key + props 全等 → 可以复用 element/render 对象)');

  // 场景 3:真正发生变化 — 第二个 Text 内容变化
  final tree3 = ContainerW(
    color: 'blue',
    child: RowW([
      TextW('A'),
      TextW('B-CHANGED'), // 这里 prop 变了
      TextW('C'),
    ]),
  );
  print('\n[Build #3] (text[1] changed from "B" -> "B-CHANGED")');
  print(showTree(tree3));
  print('  tree1 == tree3 ? ${tree1 == tree3}  '
      '(TextW("B") != TextW("B-CHANGED") → 该子树需要更新)');

  // 场景 4:Widget 数量对比 — 体现"Widget 极轻量,可频繁创建"
  print('\n[Why Widget is intentionally lightweight]');
  print('  3 widgets: Container + Row + 3x Text = 5 widget objects per build');
  print('  A real Material App can have ~500+ widgets per frame');
  print('  Flutter builds widgets every frame in many cases — it must be cheap');
  print('  Real cost lives in the Element tree (persistent) + RenderObject tree (layout/paint)');
  print('  Widget == compare is shallow: only runtimeType + key + final props');

  // JSON 输出,便于跨 demo 比对
  print('\n[JSON of tree1 (conceptual)]');
  print(const JsonEncoder.withIndent('  ').convert({
    'type': 'Container',
    'color': 'blue',
    'child': {
      'type': 'Row',
      'children': [
        {'type': 'Text', 'data': 'A'},
        {'type': 'Text', 'data': 'B'},
        {'type': 'Text', 'data': 'C'},
      ],
    },
  }));
}
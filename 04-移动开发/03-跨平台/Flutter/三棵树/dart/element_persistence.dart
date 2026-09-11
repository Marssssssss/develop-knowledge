// Flutter 三棵树 — Demo 2: Element 树(跨帧持久化的实例树)
//
// 权威资料(docs.flutter.dev/resources/architectural-overview):
//   "The element tree is persistent from frame to frame, and therefore plays
//    a critical performance role, allowing Flutter to act as if the widget
//    hierarchy is fully disposable while caching its underlying representation."
//
// 关键事实:
//   - Element 持有 widget 在树中特定位置的引用(BuildContext)
//   - Element 在 frame 之间持久存在 — 是 widget 重建 + render 复用的桥梁
//   - Element 的生命周期:create -> mount -> update / activate -> deactivate -> unmount
//   - Element 不重建 = RenderObject 不重建 = 状态保留 + 性能极佳
//
// 跑法:dart dart/element_persistence.dart

// ---------- 模拟 Element 基类 ----------
class FakeElement {
  FakeElement(this.widget);
  dynamic widget;          // 当前指向的 widget(每 build 都换)
  FakeElement? parent;
  List<FakeElement> children = [];
  bool mounted = true;
  int generation = 0;      // 创建时记录,代表"实例 ID"
  void mount() { mounted = true; }
  void unmount() { mounted = false; }
  @override
  String toString() => 'Element#${identityHashCode(this) & 0xffff}(w=$widget)';
}

int identityHashCode(Object o) => o.hashCode;

// 模拟 Flutter 的 updateChild:对比 old.element + new widget,决定复用 or 重建
FakeElement updateChild(FakeElement? oldElement, dynamic newWidget, FakeElement? parent) {
  if (oldElement == null) {
    // 全新子节点 → createElement
    final e = FakeElement(newWidget);
    e.parent = parent;
    e.mount();
    return e;
  }
  if (oldElement.widget.runtimeType == newWidget.runtimeType &&
      oldElement.widget == newWidget) {
    // 类型同 + prop 同 → update (复用 element,只更新内部)
    oldElement.widget = newWidget;
    return oldElement;
  }
  // 类型不同或 prop 变了 → unmount 旧的,mount 新的
  oldElement.unmount();
  final e = FakeElement(newWidget);
  e.parent = parent;
  e.mount();
  return e;
}

// ---------- 3 种 widget(简化) ----------
class W {
  const W(this.name);
  final String name;
  @override
  bool operator ==(Object o) => o is W && o.name == name;
  @override
  int get hashCode => name.hashCode;
  @override
  String toString() => 'W($name)';
}

class ContainerW extends W { const ContainerW(super.name); }
class RowW extends W { const RowW(super.name); }
class TextW extends W { const TextW(super.name); }

// ---------- demo ----------
void main() {
  print('=== Flutter Element Tree Demo ===\n');
  // Flutter 用 widget == 来判定是否复用 element
  // 这里用同一个 widget 实例多次比较,说明 === 命中 = 复用

  // ---------- Build #1:初始 ----------
  final build1Container = ContainerW('container-1');
  final build1Row = RowW('row-1');
  final build1Texts = [TextW('A'), TextW('B'), TextW('C')];

  // 模拟 Element 树挂载
  FakeElement eContainer = updateChild(null, build1Container, null);
  FakeElement eRow = updateChild(null, build1Row, eContainer);
  eContainer.children.add(eRow);
  final List<FakeElement> eTexts = [];
  for (final t in build1Texts) {
    final e = updateChild(null, t, eRow);
    eRow.children.add(e);
    eTexts.add(e);
  }

  print('[Build #1 — initial render] Element tree:');
  void show(FakeElement e, int depth) {
    final pad = '  ' * depth;
    print('$pad${e} mounted=${e.mounted} gen=${e.generation}');
    for (final c in e.children) show(c, depth + 1);
  }

  show(eContainer, 0);

  // ---------- Build #2:仅 container 的 prop 变化,但类型不变 + 不同实例 ----------
  print('\n[Build #2 — parent rebuilt with new Container instance]');
  print('  old: ${eContainer.widget}  mounted=${eContainer.mounted}');
  print('  new: ContainerW(container-1)  (new instance, same props)');
  // 关键:ContainerW('container-1) == ContainerW('container-1') 为 true
  // 所以 updateChild 会走 update 分支,复用 element
  eContainer = updateChild(eContainer, ContainerW('container-1'), null);
  print('  -> element reused: ${eContainer.widget} mounted=${eContainer.mounted}');
  print('  -> same Element#hash: ${identityHashCode(eContainer) & 0xffff}');

  // ---------- Build #3:text[1] 内容变化 → 该子树需要重新挂载 ----------
  print('\n[Build #3 — text[1] changed from "B" to "B-CHANGED"]');
  print('  before: ${eTexts[1]}');
  final newTexts = [TextW('A'), TextW('B-CHANGED'), TextW('C')];
  for (var i = 0; i < newTexts.length; i++) {
    final oldE = eTexts[i];
    final newE = updateChild(oldE, newTexts[i], eRow);
    if (oldE.widget != newTexts[i]) {
      // element 被替换
      eRow.children[i] = newE;
      eTexts[i] = newE;
      print('  text[$i]: reused? ${identical(oldE, newE)}  '
          '(changed → new element mounted)');
    }
  }

  // ---------- 关键 takeaway ----------
  print('\n[Why this matters] (per docs.flutter.dev/resources/architectural-overview):');
  print('  - Element 持久存在 → State (StatefulWidget) 不丢失');
  print('  - RenderObject (RenderObjectElement 持有) 不重建 → layout state 保留');
  print('  - Element 只是 widget holder,真正的成本由 render tree 承担');
  print('  - 这是 Flutter 比"每次 rebuild 都销毁 DOM"框架快得多的根本原因');
}
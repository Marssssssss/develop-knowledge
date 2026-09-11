// Flutter 三棵树 — Demo 5: 渲染管线 build → layout → paint → composite
//
// 权威资料(docs.flutter.dev/resources/architectural-overview):
//   渲染过程分 4 阶段:
//     1. Build     — build() 把 widget 转 element + 同步创建/更新 RenderObject
//     2. Layout    — 深度优先遍历 render tree,父传约束 → 子返 size
//     3. Paint     — 每个 render 对象生成 Layer(绘指令列表)
//     4. Composite — SceneBuilder 把 layer 合成 Scene,交给 Window.render() → GPU
//
// 关键事实:
//   - Frame pipeline 触发: vsync 信号 / Texture 就绪 / 状态变化(动画)
//   - 脏区域(dirty regions)优化: 只重画变化的区域,而不是整个屏幕
//   - RenderView.compositeFrame() 触发整帧合成
//
// 跑法:dart dart/build_to_composite.dart

import 'dart:async';

enum PipelineStage { idle, build, layout, paint, composite, presented }

class PipelineState {
  PipelineStage stage = PipelineStage.idle;
  final List<String> log = [];
  final DateTime start = DateTime.now();
  void advance(PipelineStage next, String note) {
    stage = next;
    final dt = DateTime.now().difference(start).inMicroseconds / 1000.0;
    log.add('[+${dt.toStringAsFixed(2)}ms] ${next.name.padRight(10)} $note');
  }
  @override
  String toString() => log.join('\n');
}

// 模拟 widget → element → render 三棵树节点
class TreeVisualizer {
  static const widgetTree = '''
Widget Tree (immutable, lightweight, recreated often):
  MaterialApp
    └── Scaffold
         ├── AppBar
         ├── Center
         │    └── Text("Hello")
         └── FloatingActionButton
''';

  static const elementTree = '''
Element Tree (persistent, 1:1 with widget):
  MaterialAppElement
    └── ScaffoldElement
         ├── AppBarElement
         ├── CenterElement
         │    └── TextElement     <-- same instance across rebuilds
         └── FABElement
''';

  static const renderTree = '''
Render Tree (mutable, holds layout/paint state):
  RenderView (1080x1920)
    └── RenderShiftedBox (Scaffold)
         ├── RenderAppBar
         ├── RenderPositionedBox (Center)
         │    └── RenderParagraph("Hello")
         └── RenderTransform (FAB)
''';
}

Future<void> simulateOneFrame(PipelineState s, int frameIdx) async {
  s.log.add('\n──── Frame $frameIdx ────');
  // 阶段 1:Build
  s.advance(PipelineStage.build, 'widget.build() → element.update → renderObject.update');
  await Future<void>.delayed(Duration.zero);

  // 阶段 2:Layout
  s.advance(PipelineStage.layout, 'DFS render tree: parent→child constraints, child→parent size');
  await Future<void>.delayed(Duration.zero);

  // 阶段 3:Paint
  s.advance(PipelineStage.paint, 'each RenderObject.paint() → Layer (PictureLayer/ClipLayer/etc.)');
  await Future<void>.delayed(Duration.zero);

  // 阶段 4:Composite
  s.advance(PipelineStage.composite, 'SceneBuilder combines Layers → Scene → Window.render() → GPU');
  s.advance(PipelineStage.presented, 'frame on screen');
}

Future<void> main() async {
  print('=== Flutter Rendering Pipeline Demo ===\n');

  print(TreeVisualizer.widgetTree);
  print(TreeVisualizer.elementTree);
  print(TreeVisualizer.renderTree);

  // 跑 3 个 frame,观察 dirty region 优化(只有 frame 2 改 text[1] 内容)
  for (var f = 1; f <= 3; f++) {
    final state = PipelineState();
    await simulateOneFrame(state, f);
    if (f == 2) {
      state.log.add('  (only Text("Hello") → Text("Hi") — only RenderParagraph needs repaint)');
    }
    print(state.log);
  }

  // 关键事实
  print('\n[Key facts] (per docs.flutter.dev/resources/architectural-overview):');
  print('  - Composite frame: RenderView.compositeFrame() creates SceneBuilder, builds Scene');
  print('  - Engine: "responsible for rasterizing composited scenes whenever a new frame needs to be painted"');
  print('  - Impeller (default on iOS/Android since Flutter 3.10) ships with the app — not device-dependent');
  print('  - Skia used on web (WASM build) and older Android/iOS fallback');
  print('  - Paint phase produces Layers; only dirty layers are repainted in the next frame');
  print('  - A "frame budget" is ~16.6ms (60Hz) or ~8.3ms (120Hz); exceeding it causes jank');

  print('\n[Why Flutter is fast]');
  print('  1. Widget tree is cheap to rebuild — Element/Render tree is NOT rebuilt');
  print('  2. Layout is O(n) in dirty regions, not the whole tree');
  print('  3. Direct GPU rendering via Impeller — no OEM UIKit/Material indirection');
  print('  4. Compile AOT (release) — no JIT overhead, no JS engine');
}
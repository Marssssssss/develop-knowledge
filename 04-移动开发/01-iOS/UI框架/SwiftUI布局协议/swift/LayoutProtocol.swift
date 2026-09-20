// SwiftUI Layout 协议:与 python/layout_protocol.py 同题的 Swift 侧实现(人工审查用)。
// 关键点:自定义容器**只需实现 sizeThatFits 与 placeSubviews**,其余钩子有默认实现。

import SwiftUI

// MARK: - 1. 最小可用的自定义容器

struct BasicVStack: Layout {
    var alignment: HorizontalAlignment = .center

    // 带默认值的参数会让调用点必须写 `BasicVStack() { ... }`:
    // 调用点的 callAsFunction 会去找「零参数 init」,写不出就编译不过。
    init() { self.alignment = .center }
    init(alignment: HorizontalAlignment) { self.alignment = alignment }

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let sizes = subviews.map { $0.sizeThatFits(.unspecified) }
        return CGSize(
            width:  sizes.map(\.width).max() ?? 0,
            height: sizes.reduce(0) { $0 + $1.height }
        )
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize,
                       subviews: Subviews, cache: inout ()) {
        var y = bounds.minY
        for subview in subviews {
            let size = subview.sizeThatFits(.unspecified)
            subview.place(at: CGPoint(x: bounds.minX, y: y),
                          anchor: .topLeading,
                          proposal: ProposedViewSize(size))
            y += size.height
        }
    }
}

// MARK: - 2. 用 cache 避免重复测量

struct EqualWidthHStack: Layout {
    struct Cache {
        var idealSizes: [CGSize] = []
    }

    // makeCache 先于 sizeThatFits 调用,结果同时喂给 sizeThatFits 和 placeSubviews
    func makeCache(subviews: Subviews) -> Cache {
        Cache(idealSizes: subviews.map { $0.sizeThatFits(.unspecified) })
    }

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout Cache) -> CGSize {
        let maxW = cache.idealSizes.map(\.width).max() ?? 0
        let maxH = cache.idealSizes.map(\.height).max() ?? 0
        if let pw = proposal.width, pw < maxW * CGFloat(subviews.count) {
            // 空间不足:按均分后的宽度再问一遍子视图要多大
            let share = pw / CGFloat(max(subviews.count, 1))
            let squeezed = subviews.map { $0.sizeThatFits(ProposedViewSize(width: share, height: proposal.height)) }
            return CGSize(width: squeezed.reduce(0) { $0 + $1.width },
                          height: squeezed.map(\.height).max() ?? 0)
        }
        return CGSize(width: maxW * CGFloat(subviews.count), height: maxH)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize,
                       subviews: Subviews, cache: inout Cache) {
        var x = bounds.minX
        for subview in subviews {
            // 复用 cache,不再量一次
            let size = subview.sizeThatFits(.unspecified)
            subview.place(at: CGPoint(x: x, y: bounds.minY),
                          anchor: .topLeading,
                          proposal: ProposedViewSize(size))
            x += size.width
        }
    }

    // 这两个有默认实现,不写也能编译
    func explicitAlignment(of guide: VerticalAlignment,
                           in bounds: CGRect,
                           proposal: ProposedViewSize,
                           subviews: Subviews, cache: inout Cache) -> CGFloat? {
        nil
    }

    func spacing(subviews: Subviews, cache: inout Cache) -> ViewSpacing { .zero }
}

// MARK: - 3. ViewThatFits:按提供顺序挑第一个放得下的

struct UploadProgressView: View {
    var uploadProgress: Double

    var body: some View {
        ViewThatFits(in: .horizontal) {          // 只约束水平轴
            HStack {
                Text(uploadProgress.formatted(.percent))
                ProgressView(value: uploadProgress).frame(width: 100)
            }
            ProgressView(value: uploadProgress).frame(width: 100)
            Text(uploadProgress.formatted(.percent))
        }
    }
}

// MARK: - 4. AnyLayout:换布局类型不销毁子视图状态

struct DynamicLayoutExample: View {
    @Environment(\.dynamicTypeSize) var dynamicTypeSize

    var body: some View {
        let layout = dynamicTypeSize <= .medium
            ? AnyLayout(HStackLayout())
            : AnyLayout(VStackLayout())
        layout {
            Text("First label")
            Text("Second label")
        }
    }
}

// MARK: - 5. 三种特殊提案的对照

struct ProposalProbe: View {
    var body: some View {
        VStack {
            // .unspecified → 子视图报「理想尺寸」
            Color.clear.frame(width: 0, height: 0)
        }
        // 容器内部对子视图提的三种提案(语义见 README):
        //   ProposedViewSize.zero          → 最小尺寸
        //   ProposedViewSize.infinity      → 最大尺寸
        //   ProposedViewSize.unspecified   → 理想尺寸
        //   proposal.replacingUnspecifiedDimensions(by:) → 只替换 unspecified 的那一轴
    }
}

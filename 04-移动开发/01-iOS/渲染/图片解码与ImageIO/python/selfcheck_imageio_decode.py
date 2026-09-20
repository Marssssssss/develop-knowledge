from imageio_decode import *

# ImageIO 解码 自检:python selfcheck_imageio_decode.py
# 模型语义见 imageio_decode.py

def _ck(label, cond, detail=""):
    if not cond:
        raise AssertionError(f"{label} 失败: {detail}")


def run_selfcheck():
    n = 0

    def ok(label, cond, detail=""):
        nonlocal n
        n += 1
        _ck(label, cond, detail)
        print(f"ok {n:02d} - {label}")

    # --- 1. 解码后位图内存:一个 12MP 的直出图
    photo = ImageFile(4032, 3024, kind="heic")
    src = ImageSource(photo)
    full = src.create_image_at_index(0)
    ok("4032x3024 全尺寸解码 = 48,771,072 字节", full.byte_count == 4032 * 3024 * BYTES_PER_PIXEL,
       full.byte_count)
    ok("约 46.5 MiB", abs(full.mib() - 46.5) < 0.05, full.mib())

    # 下采样到 200px 的网格里显示
    thumb = src.create_thumbnail_at_index(0, {
        "kCGImageSourceCreateThumbnailFromImageAlways": True,
        "kCGImageSourceThumbnailMaxPixelSize": 200})
    ok("MaxPixelSize=200 → 长边不超过 200", max(thumb.width, thumb.height) <= 200,
       (thumb.width, thumb.height))
    ok("保持 4:3 宽高比", abs(thumb.width / thumb.height - 4032 / 3024) < 1e-2,
       (thumb.width, thumb.height))
    ok("下采样后只有 120,000 字节", thumb.byte_count == 120000, thumb.byte_count)
    ratio = full.byte_count / thumb.byte_count
    ok("内存差约 406 倍", abs(ratio - 406.4) < 0.5, ratio)

    # --- 2. 三个 key 的分工
    no_thumb = ImageFile(4032, 3024, kind="jpeg")
    s2 = ImageSource(no_thumb)
    ok("既没内嵌缩略图又没给生成 key → 返回 NULL",
       s2.create_thumbnail_at_index(0) is None)
    gen = s2.create_thumbnail_at_index(0, {
        "kCGImageSourceCreateThumbnailFromImageAlways": True,
        "kCGImageSourceThumbnailMaxPixelSize": 100})
    ok("Always → 一定会从原图生成", gen is not None and gen.source == "full")

    with_thumb = ImageFile(4032, 3024, has_embedded_thumb=True, thumb_size=(160, 120))
    s3 = ImageSource(with_thumb)
    before = ImageSource.decode_count
    emb = s3.create_thumbnail_at_index(0)
    ok("有内嵌缩略图时直接用它,不做全尺寸解码",
       emb.source == "embedded" and ImageSource.decode_count == before)
    ok("内嵌缩略图的尺寸就是文件里那个", (emb.width, emb.height) == (160, 120))
    forced = s3.create_thumbnail_at_index(0, {
        "kCGImageSourceCreateThumbnailFromImageAlways": True,
        "kCGImageSourceThumbnailMaxPixelSize": 100})
    ok("Always 会绕过内嵌缩略图,重新从原图生成",
       forced.source == "full" and ImageSource.decode_count > before)

    # --- 3. MaxPixelSize 是「宽和高的最大值」
    wide = ImageFile(4000, 1000)
    s4 = ImageSource(wide)
    t = s4.create_thumbnail_at_index(0, {
        "kCGImageSourceCreateThumbnailFromImageAlways": True,
        "kCGImageSourceThumbnailMaxPixelSize": 400})
    ok("MaxPixelSize 约束的是长边", t.width == 400 and t.height == 100, (t.width, t.height))
    ok("MaxPixelSize 大于原图时原样返回",
       s4.create_thumbnail_at_index(0, {
           "kCGImageSourceCreateThumbnailFromImageAlways": True,
           "kCGImageSourceThumbnailMaxPixelSize": 99999}).width == 4000)

    # --- 4. WithTransform 与 orientation
    rotated = ImageFile(4032, 3024, exif_orientation=6)
    s5 = ImageSource(rotated)
    plain = s5.create_thumbnail_at_index(0, {
        "kCGImageSourceCreateThumbnailFromImageAlways": True,
        "kCGImageSourceThumbnailMaxPixelSize": 300})
    ok("不加 WithTransform:按存储方向缩(长边是宽)",
       plain.width == 300 and plain.height == 225, (plain.width, plain.height))
    turned = s5.create_thumbnail_at_index(0, {
        "kCGImageSourceCreateThumbnailFromImageAlways": True,
        "kCGImageSourceThumbnailMaxPixelSize": 300,
        "kCGImageSourceCreateThumbnailWithTransform": True})
    ok("加 WithTransform:先按 orientation 换轴,长边变高",
       turned.height == 300 and turned.width == 225, (turned.width, turned.height))
    normal = ImageSource(ImageFile(4032, 3024, exif_orientation=1))
    same = normal.create_thumbnail_at_index(0, {
        "kCGImageSourceCreateThumbnailFromImageAlways": True,
        "kCGImageSourceThumbnailMaxPixelSize": 300,
        "kCGImageSourceCreateThumbnailWithTransform": True})
    ok("orientation=1 时加不加 WithTransform 结果一致",
       (same.width, same.height) == (300, 225), (same.width, same.height))

    # --- 5. ShouldCacheImmediately:解码时机
    ImageSource.decode_count = 0
    s6 = ImageSource(ImageFile(1000, 1000))
    s6.create_image_at_index(0, cache_immediately=True)
    ok("ShouldCacheImmediately:创建时立刻解码", ImageSource.decode_count == 1)
    ImageSource.decode_count = 0
    s7 = ImageSource(ImageFile(1000, 1000))
    s7.create_image_at_index(0, cache_immediately=False)
    ok("不给 ShouldCacheImmediately:创建时不解码(推迟到用时)",
       ImageSource.decode_count == 0)

    # --- 6. 渐进式:必须提交「截至当前的全部」数据
    prog = ImageFile(2000, 1500, total_bytes=10000)
    inc = IncrementalSource(prog, header_bytes=1000)
    ok("初始状态是 statusUnknownType", inc.status == "statusUnknownType", inc.status)
    chunk1 = b"H" * 500
    ok("头部还没读完 → statusReadingHeader",
       inc.update_data(chunk1) == "statusReadingHeader", inc.status)
    acc = b"H" * 3000
    ok("读到 3000 字节 → statusIncomplete", inc.update_data(acc) == "statusIncomplete", inc.status)
    ok("不完整时取图得到 None", inc.image_at(0) is None)
    broken = b"H" * 500
    ok("只提交新块(不是累计数据)→ statusInvalidData",
       inc.update_data(broken) == "statusInvalidData", inc.status)
    inc2 = IncrementalSource(prog, header_bytes=1000)
    inc2.update_data(b"H" * 3000)
    ok("final=True 但数据不齐 → statusUnexpectedEOF",
       inc2.update_data(b"H" * 3000, final=True) == "statusUnexpectedEOF", inc2.status)
    inc3 = IncrementalSource(prog, header_bytes=1000)
    inc3.update_data(b"H" * 3000)
    inc3.update_data(b"H" * 10000)
    ok("累计数据补齐 → statusComplete", inc3.status == "statusComplete", inc3.status)
    ok("statusComplete 之后才取得到图", inc3.image_at(0) is not None)

    # --- 7. 容器层面的两个计数
    heif = ImageFile(4032, 3024, kind="heic", images=3, primary_index=1)
    s8 = ImageSource(heif)
    ok("GetCount 数的是图片数,不含缩略图", s8.count() == 3)
    ok("HEIF 的主图下标可以不是 0", s8.primary_image_index() == 1)

    # --- 8. PDF 的 72 dpi 缩略图必须显式开
    pdf = ImageFile(612, 792, kind="pdf")
    s9 = ImageSource(pdf)
    ok("PDF 不给 FromImage* → NULL", s9.create_thumbnail_at_index(0) is None)
    ok("PDF 给了 Always → 生成 72 dpi 图",
       s9.create_thumbnail_at_index(0, {
           "kCGImageSourceCreateThumbnailFromImageAlways": True}) is not None)

    print(f"\n全部 {n} 条断言通过")
    return n


if __name__ == "__main__":
    run_selfcheck()

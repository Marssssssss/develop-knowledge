"""HBase region 分裂策略与 HFile 版本（口径见 README）。

官方源码口径（apache/hbase@master，本轮实读）：

ConstantSizeRegionSplitPolicy.configureForRegion：
    desiredMaxFileSize = desc.getMaxFileSize();
    if (<=0) desiredMaxFileSize = conf.getLong(HREGION_MAX_FILESIZE, DEFAULT_MAX_FILE_SIZE); // 10GB
    double jitter  = conf.getDouble("hbase.hregion.max.filesize.jitter", 0.25D);
    jitterRate     = (ThreadLocalRandom.current().nextFloat() - 0.5D) * jitter;   // [-0.125, +0.125]
    desiredMaxFileSize += (long)(desiredMaxFileSize * jitterRate);

IncreasingToUpperBoundRegionSplitPolicy：
    initialSize = 2 * memStoreFlushSize（默认 128MB -> 256MB），可被
                  "hbase.increasing.policy.initial.size" 覆盖；
    getSizeToCheck(count) = (count == 0 || count > 100) ? desiredMaxFileSize
                                                        : min(desiredMaxFileSize, initialSize * count^3)

官方注释的例子：flush size 128MB 时，两次 flush 后 256MB 就分裂；分裂出 2 个 region 后
阈值变成 2^3 * 128MB * 2 = 2048MB；3 个 region 时 3^3 * 128MB * 2 = 6912MB，
直到顶到配置的最大文件大小为止。
"""

DEFAULT_MAX_FILE_SIZE = 10 * 1024 * 1024 * 1024  # hbase.hregion.max.filesize = 10737418240
DEFAULT_MEMSTORE_FLUSH = 128 * 1024 * 1024
DEFAULT_JITTER = 0.25
REGION_SPLIT_LIMIT = 1000  # hbase.regionserver.regionSplitLimit

MB = 1024 * 1024


def jitter_rate(rand_float, jitter=DEFAULT_JITTER):
    """源码：jitterRate = (random.nextFloat() - 0.5) * jitter。"""
    return (rand_float - 0.5) * jitter


def constant_size_threshold(max_file_size=DEFAULT_MAX_FILE_SIZE, rand_float=0.5,
                            jitter=DEFAULT_JITTER):
    """ConstantSizeRegionSplitPolicy 的实际阈值：配上抖动后的 maxFileSize。"""
    rate = jitter_rate(rand_float, jitter)
    value = int(max_file_size * rate)
    # 溢出保护：源码在 jitterRate > 0 且抖动值会溢出时直接设为 Long.MAX_VALUE
    if rate > 0 and value > (2 ** 63 - 1) - max_file_size:
        return 2 ** 63 - 1
    return max_file_size + value


class IncreasingToUpperBoundSplitPolicy:
    """默认分裂策略（0.94 起）。"""

    def __init__(self, memstore_flush=DEFAULT_MEMSTORE_FLUSH, max_file_size=DEFAULT_MAX_FILE_SIZE,
                 initial_size_override=None):
        self.max_file_size = max_file_size
        if initial_size_override is not None and initial_size_override > 0:
            self.initial_size = initial_size_override
        else:
            self.initial_size = 2 * memstore_flush

    def size_to_check(self, table_regions_count):
        """region 数为 0 或超过 100 时直接用最大文件大小。"""
        if table_regions_count == 0 or table_regions_count > 100:
            return self.max_file_size
        return min(self.max_file_size, self.initial_size * (table_regions_count ** 3))

    def should_split(self, region_size, table_regions_count):
        return self._can_split(table_regions_count) and region_size > self.size_to_check(
            table_regions_count)

    def _can_split(self, table_regions_count):
        """regionSplitLimit 只是"参考"，不是硬上限；本模型按是否已达 limit 判断。"""
        return table_regions_count < REGION_SPLIT_LIMIT


class ConstantSizeSplitPolicy:
    """简单策略：只看最大文件大小（带抖动）。"""

    def __init__(self, max_file_size=DEFAULT_MAX_FILE_SIZE, rand_float=0.5,
                 jitter=DEFAULT_JITTER):
        self.max_file_size = max_file_size
        self.threshold = constant_size_threshold(max_file_size, rand_float, jitter)

    def should_split(self, region_size, table_regions_count=0):
        return region_size > self.threshold


def split_sequence(policy, start_regions=1, rounds=10):
    """模拟：region 数从 start_regions 开始，每分裂一次 +1，返回每轮的阈值。

    注意本模型把"分裂后每个 region 重新长到阈值"当作一步，不是真实的时间序列。
    """
    seq = []
    count = start_regions
    for _ in range(rounds):
        if not policy._can_split(count):
            break
        threshold = policy.size_to_check(count)
        seq.append((count, threshold))
        count += 1
    return seq


def hfile_default_version():
    """官方 book：默认 hfile.format.version = 3，且 v3 的 trailer 用 protobuf 序列化。"""
    return 3


def can_write_hfile(version):
    """HBase 已不能写早于默认版本（v3）的 HFile，但仍能读 v2。"""
    return version >= 3

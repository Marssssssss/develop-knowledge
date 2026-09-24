"""链式修正模型的统一入口:按层次再导出三个子模块,便于自检与演示引用。

  chainfix_const  : 位域工具与 fixup-chains.h 常量
  chainfix_format : 各 pointer_format 的 parse / write 与超宽校验
  chainfix_walk   : page_start 解码、链遍历、imports 解析
"""

from chainfix_const import (  # noqa: F401
    MASK64, bits, ins, sign_extend,
    START_NONE, START_MULTI, START_LAST,
    PTR_ARM64E, PTR_64, PTR_32, PTR_64_OFFSET, PTR_ARM64E_KERNEL,
    PTR_ARM64E_USERLAND, PTR_ARM64E_USERLAND24, PTR_ARM64E_SHARED_CACHE,
    IMPORT, IMPORT_ADDEND, IMPORT_ADDEND64,
    Fixup, Error,
)
from chainfix_format import (  # noqa: F401
    PointerFormat, Fmt64, Fmt64Offset, Fmt32, FmtArm64e,
    FmtArm64eRebase, FmtArm64eUserland, FmtArm64eKernel, REGISTRY, make_format,
)
from chainfix_walk import (  # noqa: F401
    SegmentStarts, Segment, chain_starts_on_page, for_each_chain_start,
    for_each_fixup_in_chain, parse_import,
)

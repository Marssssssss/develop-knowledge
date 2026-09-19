#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DWARF 编号常量表（单独成文件只为实现「单源文件 ≤ 300 行」）。

取值来源：
  * DW_TAG —— DWARF5 Table 7.3「Tag encodings」（https://dwarfstd.org/doc/DWARF5.pdf）
  * DW_AT / DW_FORM —— binutils include/dwarf2.def 的 enumeration 值，
    与 DWARF5 Appendix 一致：
    https://sourceware.org/git/?p=binutils-gdb.git;a=blob_plain;f=include/dwarf2.def
"""

DW_TAG = {
    0x01: "DW_TAG_array_type", 0x02: "DW_TAG_class_type",
    0x04: "DW_TAG_enumeration_type", 0x05: "DW_TAG_formal_parameter",
    0x0B: "DW_TAG_lexical_block", 0x0D: "DW_TAG_member",
    0x0F: "DW_TAG_pointer_type", 0x11: "DW_TAG_compile_unit",
    0x13: "DW_TAG_structure_type", 0x15: "DW_TAG_subroutine_type",
    0x16: "DW_TAG_typedef", 0x17: "DW_TAG_union_type",
    0x24: "DW_TAG_base_type", 0x2E: "DW_TAG_subprogram",
    0x34: "DW_TAG_variable",
}

DW_AT = {
    0x01: "DW_AT_sibling", 0x02: "DW_AT_location", 0x03: "DW_AT_name",
    0x0B: "DW_AT_byte_size", 0x10: "DW_AT_stmt_list", 0x11: "DW_AT_low_pc",
    0x12: "DW_AT_high_pc", 0x13: "DW_AT_language", 0x1B: "DW_AT_comp_dir",
    0x25: "DW_AT_producer", 0x3B: "DW_AT_decl_line", 0x3E: "DW_AT_encoding",
    0x49: "DW_AT_type",
}

DW_FORM = {
    0x01: "DW_FORM_addr", 0x03: "DW_FORM_block2", 0x04: "DW_FORM_block4",
    0x05: "DW_FORM_data2", 0x06: "DW_FORM_data4", 0x07: "DW_FORM_data8",
    0x08: "DW_FORM_string", 0x09: "DW_FORM_block", 0x0A: "DW_FORM_block1",
    0x0B: "DW_FORM_data1", 0x0C: "DW_FORM_flag", 0x0D: "DW_FORM_sdata",
    0x0E: "DW_FORM_strp", 0x0F: "DW_FORM_udata", 0x11: "DW_FORM_ref1",
    0x13: "DW_FORM_ref4", 0x16: "DW_FORM_indirect", 0x17: "DW_FORM_sec_offset",
    0x18: "DW_FORM_exprloc", 0x19: "DW_FORM_flag_present", 0x1F: "DW_FORM_line_strp",
    0x21: "DW_FORM_implicit_const", 0x25: "DW_FORM_strx1", 0x29: "DW_FORM_addrx1",
}

# DW_CHILDREN：§7.5.3 里紧跟 tag 之后的一个 ubyte
DW_CHILDREN_NO, DW_CHILDREN_YES = 0, 1


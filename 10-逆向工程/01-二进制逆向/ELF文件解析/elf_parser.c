/* 最小 ELF64 解析器(C 版)— 手工定义 elf(5) 的三个核心结构体。
 * 与 Python 版逐字段等价:Elf64_Ehdr(64B) / Elf64_Shdr(64B) / Elf64_Sym(24B)。
 * 编译: gcc -O2 -o elf_parser elf_parser.c
 * 用法: ./elf_parser <ELF 文件>   (在 Linux 上可用它解析自身: ./elf_parser ./elf_parser)
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct {           /* elf(5): Elf64_Ehdr, 自然对齐后共 64 字节 */
    unsigned char e_ident[16];
    uint16_t e_type, e_machine;
    uint32_t e_version;
    uint64_t e_entry, e_phoff, e_shoff;
    uint32_t e_flags;
    uint16_t e_ehsize, e_phentsize, e_phnum, e_shentsize, e_shnum, e_shstrndx;
} Ehdr;

typedef struct {           /* elf(5): Elf64_Shdr, 共 64 字节 */
    uint32_t sh_name, sh_type;
    uint64_t sh_flags, sh_addr, sh_offset, sh_size;
    uint32_t sh_link, sh_info;
    uint64_t sh_addralign, sh_entsize;
} Shdr;

typedef struct {           /* elf(5): Elf64_Sym, 共 24 字节 */
    uint32_t st_name;
    unsigned char st_info, st_other;   /* bind=st_info>>4, type=st_info&0xF */
    uint16_t st_shndx;
    uint64_t st_value, st_size;
} Sym;

static const char *SHT[] = {"NULL","PROGBITS","SYMTAB","STRTAB","RELA","HASH",
                            "DYNAMIC","?","NOBITS","REL","?","DYNSYM"};
static const char *ET[] = {"NONE","REL","EXEC","DYN","CORE"};

int main(int argc, char **argv) {
    if (argc != 2) { fprintf(stderr, "用法: %s <ELF>\n", argv[0]); return 1; }
    FILE *f = fopen(argv[1], "rb");
    if (!f) { perror("fopen"); return 1; }
    fseek(f, 0, SEEK_END); long n = ftell(f); rewind(f);
    unsigned char *d = malloc(n);
    if (fread(d, 1, n, f) != (size_t)n) { fprintf(stderr, "read fail\n"); return 1; }
    fclose(f);

    if (memcmp(d, "\x7f" "ELF", 4) != 0 || d[4] != 2 || d[5] != 1) {
        fprintf(stderr, "不是 ELF64 小端文件\n"); return 1;
    }
    Ehdr eh; memcpy(&eh, d, sizeof eh);   /* 文件即小端内存布局,直接 memcpy */

    printf("类型 %s  入口 0x%lx  节头表 @0x%lx(%u 项 × %uB, shstrndx=%u)\n",
           ET[eh.e_type & 3], (unsigned long)eh.e_entry,
           (unsigned long)eh.e_shoff, eh.e_shnum, eh.e_shentsize, eh.e_shstrndx);

    /* 第一步:先读全部节头 */
    Shdr *sh = malloc(sizeof(Shdr) * eh.e_shnum);
    for (unsigned i = 0; i < eh.e_shnum; i++)
        memcpy(&sh[i], d + eh.e_shoff + i * eh.e_shentsize, sizeof(Shdr));

    /* 第二步:.shstrtab(由 e_shstrndx 指定)承载所有节名 */
    if (eh.e_shstrndx >= eh.e_shnum) { fprintf(stderr, "shstrndx 越界\n"); return 1; }
    Shdr *st = &sh[eh.e_shstrndx];
    char *names = (char *)d + st->sh_offset;

    printf("%3s %-18s %-10s %12s %8s %8s\n", "idx", "name", "type", "addr", "off", "size");
    unsigned symtab_idx = 0;
    for (unsigned i = 0; i < eh.e_shnum; i++) {
        printf("%3u %-18.18s %-10s %12lx %8lx %8lx\n", i,
               names + sh[i].sh_name,
               sh[i].sh_type <= 11 ? SHT[sh[i].sh_type] : "?",
               (unsigned long)sh[i].sh_addr,
               (unsigned long)sh[i].sh_offset, (unsigned long)sh[i].sh_size);
        if (sh[i].sh_type == 2) symtab_idx = i;    /* SHT_SYMTAB */
    }

    /* 第三步:走符号表 .symtab,符号名在其 sh_link 指向的 .strtab 里 */
    if (symtab_idx) {
        Shdr *sym = &sh[symtab_idx];
        char *strs = (char *)d + sh[sym->sh_link].sh_offset;
        unsigned cnt = sym->sh_size / sizeof(Sym);
        printf("\n.symtab: %u 个符号(每项 %zuB), 前 15 个:\n", cnt, sizeof(Sym));
        for (unsigned i = 1; i < cnt && i <= 15; i++) {   /* 索引 0 恒为空符号 */
            Sym s; memcpy(&s, d + sym->sh_offset + i * sizeof(Sym), sizeof s);
            printf("  bind=%u type=%u 0x%12lx %6lu  %s\n",
                   s.st_info >> 4, s.st_info & 15,
                   (unsigned long)s.st_value, (unsigned long)s.st_size,
                   strs + s.st_name);
        }
    } else {
        printf("\n无 .symtab(已 strip): 逆向时需靠反编译 + 签名/启发式命名\n");
    }
    free(sh); free(d);
    return 0;
}

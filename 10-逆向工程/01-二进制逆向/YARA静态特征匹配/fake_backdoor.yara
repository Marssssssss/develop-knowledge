rule fake_backdoor
{
    meta:
        description = "demo rule mirroring mini_yara.py, runnable by real YARA"
        author = "dev-knowledge demo"
        date = "2026-09-13"

    strings:
        $mz  = { 4D 5A }                       // PE 魔数 MZ(半字节通配示例: 4? 5A)
        $s1  = "This program cannot" xor       // 单字节异或混淆字符串(YARA 自动展开 255 个密钥)
        $api = "VirtualAlloc" wide ascii       // ASCII 与宽字符(交错 \x00)两种形态
        $op  = { E8 ?? ?? ?? ?? }              // call rel32 指令指纹
        $jmp = { E9 [4] }                      // 跳变: 恰好 4 字节任意内容
        $mix = { 6A 40 [4-6] 68 ?? ?? 00 00 }  // 变长跳变 + 半字节通配(经典 push 序列)

    condition:
        $mz and ($s1 or $api) and #op >= 2 and filesize < 1MB
}

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""analyzeHeadless 命令行构造器与执行计划模型。

依据(Ghidra 官方 Headless Analyzer README, 本轮实读):
  * 用法: `analyzeHeadless <project_location> <project_name>[/<folder>]`
    或 `ghidra://<server>[:<port>]/<repository>[/<folder>]`,二者必居其一;
  * `-import` 与 `-process` **不能同时出现**;`-process` 只能指定一次、`-import` 可重复;
  * `-preScript` 在分析**之前**执行(可开/关分析),`-postScript` 在分析**之后**执行
    (可查超时);两者都只写脚本名(含扩展名)、不写路径;
  * `-readOnly` 下 `-import` 的文件不保存、`-process` 的改动被丢弃,且 `-overwrite` 被忽略;
  * `-deleteProject` 只对本次 `-import` 新建的项目生效,已存在项目**永不删除**;
  * `-process` 模式下删除程序必须显式给 `-okToDelete`,否则只打印警告;
  * `-max-cpu` 传 0 或负数等价于 1;
  * `-recursive [depth]`:导入目录默认深度 0、导入文件默认深度 1,0 表示不进容器文件。

运行: python headless_semantics.py     退出码 0 表示全部断言通过。
"""

SEP = " "


class CliError(ValueError):
    pass


def _check_script(name):
    """脚本名必须含扩展名,且不能带路径(README 明确要求)。"""
    if "/" in name or "\\" in name:
        raise CliError("脚本名不能含路径: %s" % name)
    if "." not in name:
        raise CliError("脚本名必须带扩展名(如 MyScript.java): %s" % name)
    return name


def build_command(project_location=None, project_name=None, server_url=None,
                  folder=None, imports=None, process=None, pre=None, post=None,
                  script_path=None, properties_path=None, log=None, scriptlog=None,
                  overwrite=False, recursive=None, read_only=False,
                  delete_project=False, noanalysis=False, processor=None,
                  cspec=None, timeout=None, max_cpu=None, loader=None,
                  ok_to_delete=False):
    """把选项拼成一条 analyzeHeadless 命令,并在拼之前做语义校验。

    校验规则全部来自官方 README 的约束表,而不是"看起来合理"的猜测 ——
    这类工具的坑几乎都在**选项组合**上,不在单个选项上。
    """
    if (project_location, project_name) == (None, None) and server_url is None:
        raise CliError("必须给出 project_location + project_name,或 ghidra:// 仓库 URL")
    if imports and process:
        raise CliError("-import 与 -process 不能同时出现")
    if process and delete_project:
        raise CliError("-process 模式下不能删除项目(只对本次 -import 新建项目生效)")
    if read_only and overwrite:
        raise CliError("-readOnly 时 -overwrite 被忽略,不要同时给")
    for script, _args in _pairs(pre) + _pairs(post):
        _check_script(script)
    if max_cpu is not None and max_cpu <= 0:
        max_cpu = 1                             # 官方:0 或负数等价于 1

    cmd = ["analyzeHeadless"]
    if server_url:
        cmd.append(server_url if not folder else "%s/%s" % (server_url, folder))
    else:
        target = project_name if not folder else "%s/%s" % (project_name, folder)
        cmd += [project_location, target]
    if imports is not None:
        cmd.append("-import")
        cmd += list(imports)
    if process is not None:
        cmd.append("-process")
        if process is not True:
            cmd.append(process)
    for script, args in _pairs(pre):
        cmd += ["-preScript", script] + args
    for script, args in _pairs(post):
        cmd += ["-postScript", script] + args
    if script_path:
        cmd += ["-scriptPath", ";".join(script_path)]
    if properties_path:
        cmd += ["-propertiesPath", ";".join(properties_path)]
    if log:
        cmd += ["-log", log]
    if scriptlog:
        cmd += ["-scriptlog", scriptlog]
    if recursive is not None and recursive is not False:
        cmd.append("-recursive")
        if recursive is not True:
            cmd.append(str(recursive))
    if overwrite:
        cmd.append("-overwrite")
    if read_only:
        cmd.append("-readOnly")
    if delete_project:
        cmd.append("-deleteProject")
    if noanalysis:
        cmd.append("-noanalysis")
    if processor:
        cmd += ["-processor", processor]
    if cspec:
        cmd += ["-cspec", cspec]
    if timeout is not None:
        cmd += ["-analysisTimeoutPerFile", str(timeout)]
    if loader:
        cmd += ["-loader", loader]
    if ok_to_delete:
        cmd.append("-okToDelete")
    if max_cpu is not None:
        cmd += ["-max-cpu", str(max_cpu)]
    return SEP.join(cmd)


def _pairs(group):
    """把 ["S.java", ("T.java", ["a1", "a2"])] 归一成 [("S.java", []), ("T.java", ["a1","a2"])]。

    官方命令行把脚本参数**紧跟**在 `-preScript`/`-postScript` 之后,直到下一个选项;
    所以这里用元组表示「该脚本自己的参数」,避免参数被误当成下一个脚本名。
    """
    out = []
    for item in group or []:
        if isinstance(item, (tuple, list)):
            args = list(item[1]) if (len(item) == 2 and
                                     isinstance(item[1], (tuple, list))) else list(item[1:])
            out.append((str(item[0]), [str(x) for x in args]))
        else:
            out.append((str(item), []))
    return out


def wildcard_needs_quote(pattern):
    """-process 的通配符由 headless 自己展开,须用单引号防止 shell 抢跑。"""
    return pattern is not None and any(c in pattern for c in "*?")


def default_recursive_depth(imports):
    """官方:导入目录默认深度 0、导入文件默认深度 1(0 = 不进入容器文件)。"""
    if not imports:
        return None
    return {"dir": 0, "file": 1}


def plan(location, name, imports=None, process=None, pre=None, post=None,
         noanalysis=False, read_only=False, delete_project=False, ok_to_delete=False):
    """产出实际执行序列 —— 顺序本身就是语义(preScript 先于分析,postScript 之后)。"""
    steps = []
    if imports is not None:
        for f in imports:
            steps.append(("import", f))
    elif process is not None:
        steps.append(("process", process if process is not True else "*"))
    for script, args in _pairs(pre):
        steps.append(("preScript", script, tuple(args)))
    if not noanalysis and (imports is not None or process is not None):
        steps.append(("analysis", "-"))
    for script, args in _pairs(post):
        steps.append(("postScript", script, tuple(args)))
    if read_only:
        steps.append(("discard-changes", "-"))
    elif imports is not None and delete_project:
        steps.append(("delete-project", name))
    elif imports is not None:
        steps.append(("save-import", name))
    elif process is not None:
        steps.append(("save-process", name))
    return steps


def parse_properties(text):
    """解析脚本的 .properties 文件(askXxx() 的 headless 取值来源)。

    官方格式:「用空格拼接的参数串 = 值」;以 # 或 ! 开头的首个非空白字符为注释行;
    写出时**不包含** defaultValue 参数(因此 key 与脚本里的默认值串不完全一致)。
    """
    out, order = {}, []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line[0] in "#!":
            continue
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        key, val = key.strip(), val.strip()
        out[key] = val
        order.append(key)
    return out, order


def consume_script_args(values, n_asks):
    """模拟 askXxx() 对 getScriptArgs() 的消费:**按序取,取完即抛 IndexOutOfBounds**。

    官方原文:若已通过脚本参数传入值,askXxx() 优先消费参数数组而非 .properties;
    第 1 个 askXxx 用数组第 1 个值……**若数组里值全被消费完,下一次 askXxx()
    会抛 IndexOutOfBoundsException**。这就是"脚本在 GUI 里能跑、headless 下炸掉"
    的常见原因。
    """
    got = []
    for i in range(n_asks):
        if i >= len(values):
            raise IndexError("IndexOutOfBoundsException: 第 %d 次 askXxx() 已无参数可用" % (i + 1))
        got.append(values[i])
    return got

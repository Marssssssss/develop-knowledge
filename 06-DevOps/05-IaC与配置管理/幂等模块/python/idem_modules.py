"""Ansible 幂等模块最小实现

来源:
- Ansible Official "playbooks_intro.html" (docs.ansible.com/ansible/devel/playbooks_intro.html)
  原文:"Most Ansible modules check whether the desired final state has
  already been achieved and exit without performing any actions if that
  state has been achieved. Repeating the task does not change the final
  state. Modules that behave this way are 'idempotent'."
- "Test Strategies" (docs.ansible.com/projects/ansible/latest/reference_appendices/test_strategies.html)
  "--check mode in Ansible can be used as a layer of testing as well.
  If running a deployment playbook against an existing system, using the
  --check flag to the ansible command will report if Ansible thinks it
  would have had to have made any changes to bring the system into a
  desired state"

实现 3 个核心 module 的幂等行为:
1. file     — 路径是否存在 + mode/owner 比对
2. package  — 是否已安装
3. service  — state 字段比对 (started/stopped/enabled)
"""

import json
import os
import grp  # POSIX
import pwd  # POSIX
from pathlib import Path
from typing import Any

# 模拟一个目标机事实:文件/包/服务
class Host:
    def __init__(self):
        self.fs = {  # path -> {mode, owner, group, content}
            "/etc/nginx/nginx.conf": {
                "mode": 0o644, "owner": "root", "group": "root",
                "content": "user www-data;\n",
            },
            "/var/log/app.log": {
                "mode": 0o600, "owner": "app", "group": "app",
                "content": "",
            },
            "/etc/missing.conf": None,
        }
        self.packages = {"nginx", "curl", "vim"}
        self.services = {  # name -> {running, enabled}
            "nginx": {"running": True,  "enabled": True},
            "sshd":  {"running": True,  "enabled": True},
            "redis": {"running": False, "enabled": False},
        }

    # ----- file ops -----
    def stat_file(self, path):
        if path not in self.fs:
            return None
        return self.fs[path]

    def write_file(self, path, *, mode, owner, group, content):
        self.fs[path] = {"mode": mode, "owner": owner, "group": group, "content": content}

    def remove_file(self, path):
        self.fs.pop(path, None)

    # ----- package -----
    def pkg_is_installed(self, name):
        return name in self.packages

    def pkg_install(self, name):
        self.packages.add(name)

    # ----- service -----
    def svc_state(self, name):
        return self.services.get(name)


# -----------------------------------------------------------------------------
# Module 1: file  (State=present/absent, mode/owner/content 校验)
# -----------------------------------------------------------------------------

def module_file(host: Host, params: dict, check_mode: bool = False) -> dict:
    """实现 ansible.builtin.file 风格:desired-state 检查

    返回 Ansible 经典 result:
        changed   — bool,本次是否真发生修改
        failed   — bool
        msg      — str
        diff     — dict('before', 'after'),仅 --diff 时填
    """
    path = params["path"]
    state = params.get("state", "file")  # file / directory / link / absent / touch
    desired_mode = params.get("mode")
    desired_owner = params.get("owner")
    desired_group = params.get("group")
    desired_content = params.get("_content")  # demo 用

    cur = host.stat_file(path)
    result = {"changed": False, "failed": False, "msg": "", "diff": {}}

    if state == "absent":
        if cur is not None:
            result["changed"] = True
            result["diff"] = {"before": dict(cur), "after": None}
            if not check_mode:
                host.remove_file(path)
            result["msg"] = "file removed"
        else:
            result["msg"] = "file already absent"
        return result

    # state = file / touch / directory (本 demo 仅 file)
    if cur is None:
        # 全新创建
        result["changed"] = True
        new = {"mode": desired_mode, "owner": desired_owner,
               "group": desired_group, "content": desired_content or ""}
        result["diff"] = {"before": None, "after": new}
        if not check_mode:
            host.write_file(path, **new)
        result["msg"] = "file created"
        return result

    # 已存在: 逐字段比对, 任一差异 → changed=True
    diffs = {}
    if desired_mode is not None and cur["mode"] != desired_mode:
        diffs["mode"] = {"before": cur["mode"], "after": desired_mode}
    if desired_owner is not None and cur["owner"] != desired_owner:
        diffs["owner"] = {"before": cur["owner"], "after": desired_owner}
    if desired_group is not None and cur["group"] != desired_group:
        diffs["group"] = {"before": cur["group"], "after": desired_group}
    if desired_content is not None and cur["content"] != desired_content:
        diffs["content"] = {"before": cur["content"], "after": desired_content}
    if diffs:
        result["changed"] = True
        result["diff"] = {"before": {k: v["before"] for k, v in diffs.items()},
                           "after":  {k: v["after"]  for k, v in diffs.items()}}
        if not check_mode:
            new = dict(cur)
            new.update({k: v["after"] for k, v in diffs.items()})
            host.write_file(path, **new)
        result["msg"] = "file updated"
    else:
        result["msg"] = "file already in desired state"
    return result


# -----------------------------------------------------------------------------
# Module 2: package (State=present/absent, name=...)
# -----------------------------------------------------------------------------

def module_package(host: Host, params: dict, check_mode: bool = False) -> dict:
    name = params["name"]
    state = params.get("state", "present")
    result = {"changed": False, "failed": False, "msg": "", "diff": {}}
    installed = host.pkg_is_installed(name)

    if state == "present":
        if installed:
            result["msg"] = f"package {name} already installed"
        else:
            result["changed"] = True
            result["diff"] = {"before": "absent", "after": "present"}
            if not check_mode:
                host.pkg_install(name)
            result["msg"] = f"installed package {name}"
    elif state == "absent":
        if not installed:
            result["msg"] = f"package {name} already absent"
        else:
            result["changed"] = True
            result["diff"] = {"before": "present", "after": "absent"}
            result["msg"] = f"removed package {name}"
    return result


# -----------------------------------------------------------------------------
# Module 3: service (State=started/stopped, enabled=true/false)
# -----------------------------------------------------------------------------

def module_service(host: Host, params: dict, check_mode: bool = False) -> dict:
    name = params["name"]
    desired_running = params.get("state") in ("started", "running")
    desired_enabled = params.get("enabled")
    cur = host.svc_state(name)
    result = {"changed": False, "failed": False, "msg": "", "diff": {}}

    if cur is None:
        result["failed"] = True
        result["msg"] = f"service {name} does not exist on host"
        return result

    diff = {}
    if cur["running"] != desired_running:
        diff["state"] = {"before": cur["running"], "after": desired_running}
    if desired_enabled is not None and cur["enabled"] != desired_enabled:
        diff["enabled"] = {"before": cur["enabled"], "after": desired_enabled}
    if diff:
        result["changed"] = True
        result["diff"] = {"before": {k: v["before"] for k, v in diff.items()},
                          "after":  {k: v["after"]  for k, v in diff.items()}}
        if not check_mode:
            if "state" in diff:
                cur["running"] = desired_running
            if "enabled" in diff:
                cur["enabled"] = desired_enabled
        result["msg"] = f"service {name} updated"
    else:
        result["msg"] = f"service {name} already in desired state"
    return result


# -----------------------------------------------------------------------------
# Demo playbook runner: 同一 playbook 跑三遍,观察 idempotent 是否成立
# -----------------------------------------------------------------------------

def run_playbook(host):
    """play = 一组 module 调用,执行后打印 changed 计数"""
    print("--- Playbook: 3 module 期望收敛到 desired state ---")
    results = []
    results.append(("file: nginx.conf mode=0o640 owner=root",
                    module_file(host, {"path": "/etc/nginx/nginx.conf",
                                       "mode": 0o640, "owner": "root",
                                       "group": "root"})))
    results.append(("file: new file /tmp/x.txt 0o755 content='hello'",
                    module_file(host, {"path": "/tmp/x.txt", "mode": 0o755,
                                       "owner": "root", "group": "root",
                                       "_content": "hello"})))
    results.append(("file: /etc/missing.conf absent",
                    module_file(host, {"path": "/etc/missing.conf",
                                       "state": "absent"})))
    results.append(("package: jq installed",
                    module_package(host, {"name": "jq", "state": "present"})))
    results.append(("service: redis started+enabled",
                    module_service(host, {"name": "redis",
                                          "state": "started",
                                          "enabled": True})))
    results.append(("service: nginx started (already)",
                    module_service(host, {"name": "nginx",
                                          "state": "started",
                                          "enabled": True})))
    for label, r in results:
        flag = "CHANGED" if r["changed"] else "ok"
        print(f"  [{flag:7}] {label:50s}  msg='{r['msg']}'")
    changed = sum(1 for _, r in results if r["changed"])
    print(f"  → 累计 changed = {changed}/{len(results)}\n")
    return results


def demo_idempotency():
    """同一 playbook 跑 3 次,只有第 1 次有 changed"""
    print("=== Ansible 幂等模块 demo ===\n")
    host = Host()
    print("--- 第 1 次执行:期望全部触发 ---")
    r1 = run_playbook(host)
    print("--- 第 2 次执行:幂等性 — 期望全部 ok ---")
    r2 = run_playbook(host)
    print("--- 第 3 次执行:继续幂等 — 全部 ok ---")
    r3 = run_playbook(host)

    # 结论
    c1 = sum(1 for _, r in r1 if r["changed"])
    c2 = sum(1 for _, r in r2 if r["changed"])
    c3 = sum(1 for _, r in r3 if r["changed"])
    print(f"--- 结论 ---")
    print(f"  1st pass: {c1}/6 changed   (期望 ≥ 1)")
    print(f"  2nd pass: {c2}/6 changed   (期望 0,证明幂等)")
    print(f"  3rd pass: {c3}/6 changed   (期望 0,继续幂等)")
    assert c1 >= 1 and c2 == 0 and c3 == 0, "idempotency 失败"
    print("  ✓ idempotency 验证通过\n")


def demo_check_mode():
    """--check:不真改 host,只报 diff"""
    print("--- Demo: --check mode 不真改 ---")
    host = Host()
    pre_nginx = host.stat_file("/etc/nginx/nginx.conf")
    r = module_file(host, {"path": "/etc/nginx/nginx.conf",
                           "mode": 0o600, "owner": "root"}, check_mode=True)
    post_nginx = host.stat_file("/etc/nginx/nginx.conf")
    print(f"  check_mode=True → changed={r['changed']}, msg='{r['msg']}'")
    print(f"  diff (would-be): {json.dumps(r['diff'], default=str)}")
    print(f"  host 真实状态保持一致? {pre_nginx == post_nginx}")
    assert pre_nginx == post_nginx, "--check must not modify"
    print("  ✓ check_mode 未真实修改\n")


def main():
    demo_idempotency()
    demo_check_mode()


if __name__ == "__main__":
    main()

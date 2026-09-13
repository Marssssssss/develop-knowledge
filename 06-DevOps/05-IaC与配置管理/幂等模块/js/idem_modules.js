// Ansible 幂等模块 JavaScript 版
//
// 用 node.js 内置 fs 模拟目标主机文件系统,验证幂等 + --check
//
// 来源:Ansible docs.ansible.com/ansible/devel/playbooks_intro.html
//   "Modules that behave this way are 'idempotent'."
// 关键演示:同一 playbook 三遍 changed 数下降至 0

const fs = require('fs');
const path = require('path');

// -----------------------------------------------------------------------------
// VirtualHost:模拟文件/包/服务状态
// -----------------------------------------------------------------------------

class VirtualHost {
    constructor() {
        this.files = new Map();   // path -> {mode, owner, group, content}
        this.packages = new Set(['nginx', 'curl', 'vim']);
        this.services = new Map([
            ['nginx', {running: true, enabled: true}],
            ['sshd',  {running: true, enabled: true}],
            ['redis', {running: false, enabled: false}],
        ]);
    }

    seedFile(p, info) { this.files.set(p, { ...info }); }
    statFile(p)      { return this.files.has(p) ? { ...this.files.get(p) } : null; }
    writeFile(p, info) { this.files.set(p, info); }
    removeFile(p) { this.files.delete(p); }
    pkgInstalled(name) { return this.packages.has(name); }
    pkgInstall(name)   { this.packages.add(name); }
    svcState(name)     { return this.services.get(name); }
}

function newHost() {
    const h = new VirtualHost();
    h.seedFile('/etc/nginx/nginx.conf', {mode:0o644,owner:'root',group:'root',content:'user www-data;\n'});
    return h;
}

// -----------------------------------------------------------------------------
// Result shape — Ansible 经典 result dict
// -----------------------------------------------------------------------------

function ok(msg, diff={})  { return {changed:false, failed:false, msg, diff}; }
function ch(msg, diff={})  { return {changed:true,  failed:false, msg, diff}; }
function fail(msg)          { return {changed:false, failed:true,  msg, diff:{}}; }

// -----------------------------------------------------------------------------
// Module: file (state=absent / file)
// -----------------------------------------------------------------------------

function moduleFile(host, params, checkMode=false) {
    const p = params.path;
    const state = params.state || 'file';
    const desiredMode    = params.mode;
    const desiredOwner   = params.owner;
    const desiredGroup   = params.group;
    const desiredContent = params._content;

    const cur = host.statFile(p);

    if (state === 'absent') {
        if (cur !== null) {
            const r = ch('file removed', {before:'exists', after:null});
            if (!checkMode) host.removeFile(p);
            return r;
        }
        return ok('file already absent');
    }

    if (cur === null) {
        const newF = {mode:desiredMode,owner:desiredOwner,group:desiredGroup,content:desiredContent||''};
        const r = ch('file created', {before:null, after:newF});
        if (!checkMode) host.writeFile(p, {...newF});
        return r;
    }

    const diffs = {};
    if (desiredMode != null && cur.mode !== desiredMode)
        diffs.mode = {before:cur.mode, after:desiredMode};
    if (desiredOwner && cur.owner !== desiredOwner)
        diffs.owner = {before:cur.owner, after:desiredOwner};
    if (desiredGroup && cur.group !== desiredGroup)
        diffs.group = {before:cur.group, after:desiredGroup};
    if (desiredContent != null && cur.content !== desiredContent)
        diffs.content = {before:cur.content, after:desiredContent};

    if (Object.keys(diffs).length > 0) {
        const before={}, after={};
        for (const [k, v] of Object.entries(diffs)) { before[k]=v.before; after[k]=v.after; }
        const r = ch('file updated', {before, after});
        if (!checkMode) {
            const merged = {...cur, ...after};
            host.writeFile(p, merged);
        }
        return r;
    }
    return ok('file already in desired state');
}

// -----------------------------------------------------------------------------
// Module: package (state=present/absent)
// -----------------------------------------------------------------------------

function modulePackage(host, params) {
    const name = params.name;
    const state = params.state || 'present';
    const installed = host.pkgInstalled(name);
    if (state === 'present') {
        if (installed) return ok(`package ${name} already installed`);
        const r = ch(`installed package ${name}`, {before:'absent', after:'present'});
        host.pkgInstall(name);
        return r;
    }
    if (state === 'absent') {
        if (!installed) return ok(`package ${name} already absent`);
        return ch(`removed package ${name}`, {before:'present', after:'absent'});
    }
    return fail(`unknown state ${state}`);
}

// -----------------------------------------------------------------------------
// Module: service
// -----------------------------------------------------------------------------

function moduleService(host, params, checkMode=false) {
    const name = params.name;
    const desiredRunning = params.state === 'started' || params.state === 'running';
    const desiredEnabled = params.enabled;
    const cur = host.svcState(name);
    if (!cur) return fail(`service ${name} does not exist`);

    const diffs = {};
    if (cur.running !== desiredRunning)
        diffs.state = {before:cur.running, after:desiredRunning};
    if (desiredEnabled != null && cur.enabled !== desiredEnabled)
        diffs.enabled = {before:cur.enabled, after:desiredEnabled};

    if (Object.keys(diffs).length > 0) {
        const before={}, after={};
        for (const [k, v] of Object.entries(diffs)) { before[k]=v.before; after[k]=v.after; }
        const r = ch(`service ${name} updated`, {before, after});
        if (!checkMode) {
            const s = host.svcState(name);
            if ('state' in diffs) s.running = diffs.state.after;
            if ('enabled' in diffs) s.enabled = diffs.enabled.after;
        }
        return r;
    }
    return ok(`service ${name} already in desired state`);
}

// -----------------------------------------------------------------------------
// Playbook runner
// -----------------------------------------------------------------------------

function runPlaybook(host) {
    const tasks = [
        ['file: nginx.conf mode=0o640 owner=root',
         moduleFile(host, {path:'/etc/nginx/nginx.conf', mode:0o640, owner:'root', group:'root'})],
        ['file: /tmp/x.txt new content=hello',
         moduleFile(host, {path:'/tmp/x.txt', mode:0o755, owner:'root', group:'root', _content:'hello'})],
        ['file: /etc/missing.conf absent',
         moduleFile(host, {path:'/etc/missing.conf', state:'absent'})],
        ['package: jq installed',
         modulePackage(host, {name:'jq', state:'present'})],
        ['service: redis started+enabled',
         moduleService(host, {name:'redis', state:'started', enabled:true})],
        ['service: nginx started (already)',
         moduleService(host, {name:'nginx', state:'started', enabled:true})],
    ];
    return tasks;
}

function printPass(label, tasks) {
    console.log(`--- ${label} ---`);
    let changed = 0;
    for (const [name, r] of tasks) {
        const flag = r.changed ? 'CHANGED' : 'ok';
        if (r.changed) changed++;
        console.log(`  [${flag.padEnd(7)}] ${name.padEnd(50)}  msg='${r.msg}'`);
    }
    console.log(`  → changed = ${changed}/${tasks.length}\n`);
    return changed;
}

// -----------------------------------------------------------------------------
// Entry
// -----------------------------------------------------------------------------

function main() {
    console.log("=== Ansible 幂等模块 demo (JavaScript 版) ===\n");
    const host1 = newHost();
    console.log('--- 第 1 次执行:全部触发 ---');
    const t1 = runPlaybook(host1);
    printPass('第 1 pass', t1);

    console.log('--- 第 2 次执行:幂等性验证 ---');
    const t2 = runPlaybook(host1);
    printPass('第 2 pass', t2);

    console.log('--- 第 3 次执行:继续幂等 ---');
    const t3 = runPlaybook(host1);
    printPass('第 3 pass', t3);

    // check_mode demo
    console.log('--- check_mode 不真改 ---');
    const host2 = newHost();
    const before = JSON.stringify(host2.statFile('/etc/nginx/nginx.conf'));
    const r = moduleFile(host2, {path:'/etc/nginx/nginx.conf', mode:0o600, owner:'root'}, true);
    const after = JSON.stringify(host2.statFile('/etc/nginx/nginx.conf'));
    console.log(`  check_mode=true → changed=${r.changed}, msg='${r.msg}'`);
    console.log(`  diff = ${JSON.stringify(r.diff)}`);
    console.log(`  host 状态保持? ${before === after ? '✓' : '✗'}\n`);

    // 汇总
    const c2 = t2.filter(([, r]) => r.changed).length;
    const c3 = t3.filter(([, r]) => r.changed).length;
    console.log('--- 结论 ---');
    console.log(`  2nd pass changed = ${c2} (期望 0)`);
    console.log(`  3rd pass changed = ${c3} (期望 0)`);
    if (c2 === 0 && c3 === 0) {
        console.log('  ✓ idempotency 验证通过');
    }
}

main();

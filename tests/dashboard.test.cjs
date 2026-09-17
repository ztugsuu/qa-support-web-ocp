const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function dashboard() {
    const nodes = new Map();
    function node(tag = '') {
        return {tag, children: [], value: '', textContent: '', disabled: false,
            append(...children) { this.children.push(...children); },
            replaceChildren(fragment) { this.children = fragment.children; },
            setAttribute() {}, addEventListener() {}};
    }
    const document = {
        hidden: false, addEventListener() {},
        getElementById(id) { if (!nodes.has(id)) nodes.set(id, node()); return nodes.get(id); },
        createElement: node, createDocumentFragment: node,
    };
    const context = vm.createContext({document, AbortController, setInterval() {},
        clearTimeout() {}, setTimeout() {}, fetch: async () => { throw new Error('offline'); }});
    vm.runInContext(fs.readFileSync('static/dashboard.js', 'utf8'), context);
    document.getElementById('namespaceSelect').value = 'test-ms';
    document.getElementById('statusFilter').value = 'all';
    document.getElementById('sortSelect').value = 'name';
    const run = code => vm.runInContext(code, context);
    run(`pods = [
        {name:'demo-a', deployment:'demo', status:'Running', restarts:'0', can_restart:true},
        {name:'demo-b', deployment:'demo', status:'Running', restarts:'0', can_restart:true},
        {name:'other-a', deployment:'other', status:'Running', restarts:'0', can_restart:false}
    ]; loaded = true; listFresh = true; render();`);
    const buttons = () => document.getElementById('podsTableBody').children.map(row => row.children[4].children[0].children[2]);
    return {context, run, buttons, document};
}

test('only eligible deployments enable restart; pending request disables every sibling', () => {
    const d = dashboard();
    assert.deepEqual(d.buttons().map(b => b.disabled), [false, false, true]);
    d.run("pendingRestarts.add('test-ms/demo'); render();");
    assert.deepEqual(d.buttons().map(b => b.disabled), [true, true, true]);
});

test('failed refresh blocks stale healthy state even after rerender', async () => {
    const d = dashboard();
    await d.run('loadPods()');
    d.run('render()');
    assert.ok(d.buttons().every(b => b.disabled));
});

test('fresh response can reenable a recovered deployment', async () => {
    const d = dashboard();
    d.run('listFresh = false; render();');
    d.context.fetch = async () => ({ok:true, json:async () => [{
        name:'demo-a', deployment:'demo', status:'Running', restarts:'3', can_restart:true
    }]});
    await d.run('loadPods()');
    assert.equal(d.buttons()[0].disabled, false);
});

test('duplicate clicks submit only once and rollout remains disabled', async () => {
    const d = dashboard();
    let finish, posts = 0;
    d.context.fetch = (url, options) => {
        if (options.method === 'POST') {
            posts++;
            assert.equal(options.headers['X-QA-Request'], 'rollout-restart');
            return new Promise(resolve => { finish = resolve; });
        }
        return Promise.resolve({ok:true, json:async () => [{
            name:'demo-a', deployment:'demo', status:'Running', restarts:'0', can_restart:false
        }]});
    };
    const first = d.run("restartDeployment('demo')");
    assert.ok(d.buttons().every(b => b.disabled));
    await d.run("restartDeployment('demo')");
    assert.equal(posts, 1);
    finish({ok:true, json:async () => ({message:'started'})});
    await first;
    assert.equal(d.buttons()[0].disabled, true);
});

test('namespace switch during restart does not overwrite the new view', async () => {
    const d = dashboard();
    let finish;
    d.context.fetch = () => new Promise(resolve => { finish = resolve; });
    const request = d.run("restartDeployment('demo')");
    d.document.getElementById('namespaceSelect').value = 'pre-new-ms';
    d.document.getElementById('restartStatus').textContent = '';
    finish({ok:true, json:async () => ({message:'old namespace restart'})});
    await request;
    assert.equal(d.document.getElementById('restartStatus').textContent, '');
});

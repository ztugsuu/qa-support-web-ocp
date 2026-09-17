const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function page(script) {
    const nodes = new Map();
    function node(tag = '') {
        const el = {tag, children:[], value:'', textContent:'', checked:false, disabled:false, dataset:{},
            append(...children) { this.children.push(...children); },
            replaceChildren(...children) { this.children = children; },
            addEventListener() {}, setAttribute() {}, classList:{toggle() {}},
            showModal() { this.open = true; }, close() { this.open = false; },
            querySelectorAll(selector) {
                const all = this.children.flatMap(c => [c,...c.querySelectorAll('*')]);
                return selector === 'input:checked' ? all.filter(c => c.tag === 'input' && c.checked) : all;
            }};
        Object.defineProperty(el,'innerHTML',{set() {throw new Error('Unsafe HTML rendering');}});
        return el;
    }
    const body = node('body'); body.dataset = {namespace:'test-ms',pod:'demo-pod'};
    const document = {body,querySelectorAll() {return [];},createElement:node,
        getElementById(id) {if (!nodes.has(id)) nodes.set(id,node()); return nodes.get(id);}};
    const context = vm.createContext({document,AbortController,URLSearchParams,URL,Blob,
        fetch:() => new Promise(() => {}),setTimeout() {}, navigator:{}});
    vm.runInContext(fs.readFileSync(script,'utf8'),context);
    return {context,document,get:id => document.getElementById(id),run:code => vm.runInContext(code,context)};
}

test('comparison normalizes container order and distinguishes unavailable from absent',() => {
    const p = page('static/qa.js');
    const differences = p.run(`
        const a = {images:{app:'a:v1',side:'s:v2'},replicas:1,ready:1};
        const b = {images:{side:'s:v2',app:'a:v1'},replicas:1,ready:1};
        differences(a,b,false);
    `);
    assert.equal(differences.length,0);
    assert.equal(p.run('differences(a,null,true)[0]'),'Харьцуулах мэдээлэл дутуу');
    assert.equal(p.run('differences(a,null,false)[0]'),'Нэг орчинд байхгүй');
});

test('comparison renders literal image strings and filters differences',() => {
    const p = page('static/qa.js');
    p.context.envFixture = {
        'test-ms':{items:[{name:'demo',images:{app:'<script>bad</script>'},replicas:1,ready:1}],error:null},
        'pre-new-ms':{items:[{name:'demo',images:{app:'demo:v2'},replicas:1,ready:1}],error:null},
    };
    p.run('environments = envFixture; renderComparison();');
    assert.equal(p.get('compareRows').children.length,1);
    assert.equal(p.get('compareRows').children[0].children[1].children[0].textContent,'app: <script>bad</script>');
    p.get('compareQuery').value = 'missing'; p.run('renderComparison()');
    assert.equal(p.get('compareRows').children.length,0);
});

test('smoke only submits selected ids and blocks duplicate runs',async () => {
    const p = page('static/qa.js');
    p.get('smokeNamespace').value = 'test-ms';
    p.context.fetch = async () => ({ok:true,json:async () => ({checks:[{id:'health',name:'Health',kind:'HTTP GET'}]})});
    await p.run('loadSmoke()');
    let finish, posts = 0;
    p.context.fetch = (url,options) => {
        posts++; assert.deepEqual(JSON.parse(options.body),{ids:['health']});
        return new Promise(resolve => {finish = resolve;});
    };
    const request = p.run('runSmoke()');
    assert.equal(p.get('smokeNamespace').disabled,true);
    await p.run('runSmoke()'); assert.equal(posts,1);
    finish({ok:true,json:async () => ({ok:true,results:[{id:'health',name:'Health',ok:true,status:200}],audit_warning:'History unavailable'})});
    await request;
    assert.equal(p.get('smokeNamespace').disabled,false);
    assert.equal(p.get('smokeRows').children[0].children[1].textContent,'PASS');
    assert.match(p.get('smokeStatus').textContent,/History unavailable/);
});

test('history ignores an older namespace response and clears failed refresh',async () => {
    const p = page('static/qa.js');
    const responses = [];
    p.context.fetch = () => new Promise(resolve => responses.push(resolve));
    p.get('historyNamespace').value = 'test-ms'; const first = p.run('loadHistory()');
    p.get('historyNamespace').value = 'pre-new-ms'; const second = p.run('loadHistory()');
    responses[1]({ok:true,json:async () => ({items:[{namespace:'pre-new-ms',action:'smoke',details:{checks:[]}}],page:1,has_more:false})});
    await second;
    responses[0]({ok:true,json:async () => ({items:[{namespace:'test-ms',action:'smoke',details:{checks:[]}}],page:1,has_more:true})});
    await first;
    assert.equal(p.get('historyRows').children[0].children[1].textContent,'pre-new-ms');
    assert.equal(p.get('historyNext').disabled,true);
    p.context.fetch = async () => ({ok:false,json:async () => ({error:'DB unavailable'})});
    await p.run('loadHistory()');
    assert.equal(p.get('historyRows').children.length,0);
    assert.equal(p.get('historyStatus').textContent,'DB unavailable');
});

test('log rendering treats HTML as text and failed refresh removes stale download',async () => {
    const p = page('static/pod-details.js');
    for (const [id,value] of Object.entries({logContainer:'app',logSource:'1',logSince:'15m',logTail:'1000',logLevel:'ERROR',logQuery:'trace-A'})) p.get(id).value = value;
    p.context.fetch = async url => {
        assert.match(url,/previous=1/); assert.match(url,/q=trace-A/);
        return {ok:true,json:async () => ({text:'<script>literal log</script>',containers:['app'],container:'app',fetched_lines:10,matched_lines:1,previous:true})};
    };
    await p.run('loadLogs()');
    assert.equal(p.get('logOutput').textContent,'<script>literal log</script>');
    assert.equal(p.get('downloadLogs').disabled,false);
    p.context.fetch = async () => ({ok:false,json:async () => ({error:'Logs unavailable'})});
    await p.run('loadLogs()');
    assert.equal(p.get('downloadLogs').disabled,true);
    assert.equal(p.get('logOutput').textContent,'');
});

test('bug report opens an editable text snapshot with current filters',async () => {
    const p = page('static/pod-details.js');
    p.get('logQuery').value = 'trace-A';
    p.context.fetch = async url => {
        assert.match(url,/api\/bug-report\/test-ms\/demo-pod/); assert.match(url,/q=trace-A/);
        return {ok:true,text:async () => '# Report\n<script>literal</script>'};
    };
    await p.run('buildReport()');
    assert.equal(p.get('reportModal').open,true);
    assert.match(p.get('reportText').value,/<script>literal<\/script>/);
});

"""Read-only QA diagnostics and explicitly configured smoke checks."""
import json
import os
import re
import ssl
import time
from http.client import HTTPException
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, HTTPSHandler, ProxyHandler

from flask import Blueprint, Response, jsonify, render_template, request
from rollout import deployment_states, pod_summary

NAME = re.compile(r'[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?')
LOG_LIMIT = 1024 * 1024


def deployment_info(deployment):
    metadata = deployment.get('metadata', {})
    spec, status = deployment.get('spec', {}), deployment.get('status', {})
    template = spec.get('template', {})
    annotations = {**metadata.get('annotations', {}), **template.get('metadata', {}).get('annotations', {})}
    return {
        'name': metadata.get('name'),
        'images': {c['name']: c['image'] for c in template.get('spec', {}).get('containers', [])},
        'build': annotations.get('qa.m-bank.mn/build-number'),
        'deployed_at': annotations.get('qa.m-bank.mn/deployed-at'),
        'restarted_at': annotations.get('kubectl.kubernetes.io/restartedAt'),
        'created_at': metadata.get('creationTimestamp'),
        'revision': metadata.get('annotations', {}).get('deployment.kubernetes.io/revision'),
        'replicas': spec.get('replicas', 1), 'ready': status.get('readyReplicas', 0),
        'updated': status.get('updatedReplicas', 0), 'available': status.get('availableReplicas', 0),
        'observed': status.get('observedGeneration', 0) >= metadata.get('generation', 1),
        'paused': spec.get('paused', False),
    }


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class SmokeConfigurationError(ValueError):
    pass


def smoke_config(namespace):
    path = os.environ.get('QA_SMOKE_CONFIG', 'config/smoke-tests.json')
    with open(path, encoding='utf-8') as stream:
        data = json.load(stream)
    if not isinstance(data, dict):
        raise ValueError('Invalid smoke configuration')
    checks = data.get(namespace, [])
    if not isinstance(checks, list) or len(checks) > 10:
        raise ValueError('Invalid smoke checks')
    ids = set()
    for check in checks:
        if not isinstance(check, dict):
            raise ValueError('Invalid smoke check')
        if any(not isinstance(check.get(key, ''), str) for key in ('url', 'id', 'bearer_token_env')):
            raise ValueError('Invalid smoke check')
        parsed = urlsplit(check.get('url', ''))
        if (not NAME.fullmatch(check.get('id', '')) or check['id'] in ids
                or check['id'] == 'deployment-readiness'
                or parsed.scheme not in ('http', 'https') or not parsed.hostname
                or parsed.username or parsed.password or parsed.fragment
                or not isinstance(check.get('name'), str)
                or type(check.get('expected_status', 200)) is not int
                or not 100 <= check.get('expected_status', 200) <= 599
                or not isinstance(check.get('body_contains', ''), str)):
            raise ValueError('Invalid smoke check')
        ids.add(check['id'])
    return checks


def run_http_check(check):
    start = time.monotonic()
    result = {'id': check['id'], 'name': check['name'], 'ok': False,
              'expected_status': check.get('expected_status', 200), 'status': None}
    try:
        headers = {'User-Agent': 'QA-Console-Smoke/1.0', 'Accept': 'application/json'}
        if check.get('bearer_token_env'):
            token = os.environ.get(check['bearer_token_env'])
            if not token:
                raise SmokeConfigurationError('Smoke token тохируулаагүй байна.')
            if '\r' in token or '\n' in token:
                raise SmokeConfigurationError('Smoke token-ийн формат буруу байна.')
            if urlsplit(check['url']).scheme != 'https':
                raise SmokeConfigurationError('Token ашиглах URL HTTPS байх шаардлагатай.')
            headers['Authorization'] = 'Bearer ' + token
        context = ssl.create_default_context(cafile=os.environ.get('QA_SMOKE_CA_FILE'))
        opener = build_opener(ProxyHandler({}), NoRedirect(), HTTPSHandler(context=context))
        try:
            response = opener.open(Request(check['url'], headers=headers, method='GET'), timeout=5)
        except HTTPError as error:
            response = error
        with response:
            result['status'] = response.code
            body = response.read(65536).decode('utf-8', errors='replace') if check.get('body_contains') else ''
        result['ok'] = (result['status'] == result['expected_status']
                        and check.get('body_contains', '') in body)
        result['message'] = 'Шалгалт амжилттай.' if result['ok'] else 'HTTP status эсвэл хариуны агуулга тохирсонгүй.'
    except SmokeConfigurationError as error:
        result['message'] = str(error)
    except ValueError:
        result['message'] = 'Smoke test тохиргооны утга буруу байна.'
    except (URLError, OSError, TimeoutError, HTTPException):
        result['message'] = 'Холболт, TLS эсвэл timeout алдаа.'
    result['duration_ms'] = round((time.monotonic() - start) * 1000)
    return result


def register_qa(app, oc_json, run_oc_command, namespaces):
    bp = Blueprint('qa', __name__)

    def valid(namespace, name=None):
        return namespace in namespaces and (name is None or NAME.fullmatch(name))

    def events(namespace, pod):
        uid = pod['metadata']['uid']
        raw = run_oc_command(['oc', 'get', 'events', '--field-selector=involvedObject.uid=' + uid,
                              '-o', 'json', '--request-timeout=10s'], namespace)
        try:
            items = json.loads(raw)['items']
            return sorted([{
                'type': e.get('type', 'Normal'), 'reason': e.get('reason', ''),
                'message': e.get('message', ''), 'count': e.get('count', e.get('series', {}).get('count', 1)),
                'time': e.get('lastTimestamp') or e.get('series', {}).get('lastObservedTime')
                        or e.get('eventTime') or e.get('metadata', {}).get('creationTimestamp', ''),
            } for e in items], key=lambda e: e['time'], reverse=True)[:100], None
        except (ValueError, KeyError, TypeError):
            return [], 'Events унших боломжгүй. Холболт болон events get/list эрхийг шалгана уу.'

    def details(namespace, name):
        pod = oc_json('pod', namespace, name)
        result = {'pod': pod_summary(pod), 'deployment': None, 'deployment_error': None}
        result['containers'] = []
        for spec_key, status_key in [('containers', 'containerStatuses'), ('initContainers', 'initContainerStatuses')]:
            statuses = {c['name']: c for c in pod.get('status', {}).get(status_key, [])}
            for c in pod.get('spec', {}).get(spec_key, []):
                state = statuses.get(c['name'], {})
                result['containers'].append({'name': c['name'], 'image': c['image'],
                    'image_id': state.get('imageID'), 'ready': state.get('ready', False),
                    'restarts': state.get('restartCount', 0), 'state': state.get('state', {}),
                    'last_state': state.get('lastState', {}), 'init': spec_key == 'initContainers'})
        owner = next((o for o in pod.get('metadata', {}).get('ownerReferences', [])
                      if o.get('kind') == 'ReplicaSet' and o.get('controller')), None)
        if owner:
            try:
                rs = oc_json('replicaset', namespace, owner['name'])
                if rs['metadata']['uid'] != owner['uid']:
                    raise ValueError('ReplicaSet replaced')
                dep_owner = next((o for o in rs['metadata'].get('ownerReferences', [])
                                  if o.get('kind') == 'Deployment' and o.get('controller')), None)
                if dep_owner:
                    deployment = oc_json('deployment', namespace, dep_owner['name'])
                    if deployment['metadata']['uid'] != dep_owner['uid']:
                        raise ValueError('Deployment replaced')
                    result['deployment'] = deployment_info(deployment)
            except (ValueError, KeyError, TypeError):
                result['deployment_error'] = 'Deployment мэдээлэл боломжгүй байна.'
        result['events'], result['events_error'] = events(namespace, pod)
        return result

    def logs(namespace, name):
        container = request.args.get('container', '')
        if container and not NAME.fullmatch(container):
            raise ValueError('Container нэр буруу.')
        previous = request.args.get('previous', '0')
        since = request.args.get('since', '1h')
        level = request.args.get('level', 'all')
        query = request.args.get('q', '')
        tail = int(request.args.get('tail', '1000'))
        if previous not in ('0', '1') or since not in ('all', '5m', '15m', '1h', '6h', '24h'):
            raise ValueError('Логийн хугацаа эсвэл төрөл буруу.')
        if not 1 <= tail <= 5000 or len(query) > 200 or level not in ('all', 'ERROR', 'WARN', 'INFO', 'DEBUG', 'TRACE'):
            raise ValueError('Логийн шүүлтүүр буруу.')
        try:
            pod = oc_json('pod', namespace, name)
        except (ValueError, KeyError, TypeError):
            raise RuntimeError('Логийн pod мэдээлэл боломжгүй. Холболт болон pod унших эрхийг шалгана уу.')
        names = [c['name'] for c in pod.get('spec', {}).get('containers', []) + pod.get('spec', {}).get('initContainers', [])]
        container = container or (names[0] if names else '')
        if container not in names:
            raise ValueError('Container олдсонгүй.')
        cmd = ['oc', 'logs', name, '-c', container, '--timestamps=true', f'--tail={tail}',
               f'--limit-bytes={LOG_LIMIT}', '--request-timeout=10s']
        if previous == '1':
            cmd.append('--previous=true')
        if since != 'all':
            cmd.append('--since=' + since)
        raw = run_oc_command(cmd, namespace)
        if raw.startswith('ERROR:'):
            raise RuntimeError('Лог боломжгүй. Өмнөх container-ийн лог хадгалагдаагүй эсвэл унших эрхгүй байж болно.')
        lines = raw.splitlines()
        expression = re.compile(r'\b(?:WARN|WARNING)\b' if level == 'WARN' else r'\b' + level + r'\b', re.I)
        selected = [line for line in lines if query.casefold() in line.casefold()
                    and (level == 'all' or expression.search(line))]
        return {'text': '\n'.join(selected), 'fetched_lines': len(lines), 'matched_lines': len(selected),
                'container': container, 'containers': names, 'previous': previous == '1',
                'limit_bytes': LOG_LIMIT, 'tail': tail, 'since': since,
                'possibly_truncated': len(raw.encode('utf-8')) >= LOG_LIMIT or len(lines) >= tail}

    @bp.get('/healthz')
    def health():
        return jsonify(status='ok')

    @bp.get('/qa')
    def workspace():
        return render_template('qa.html', namespaces=namespaces)

    @bp.get('/api/deployments/compare')
    def compare():
        def read(namespace):
            try:
                return namespace, [deployment_info(d) for d in oc_json('deployments', namespace)], None
            except (ValueError, KeyError, TypeError):
                return namespace, [], 'Deployment мэдээлэл татаж чадсангүй.'
        with ThreadPoolExecutor(max_workers=2) as pool:
            snapshots = list(pool.map(read, namespaces))
        return jsonify(environments={ns: {'items': items, 'error': error} for ns, items, error in snapshots},
                       captured_at=datetime.now(timezone.utc).isoformat())

    @bp.get('/api/pod-details/<namespace>/<name>')
    def pod_details(namespace, name):
        if not valid(namespace, name):
            return jsonify(error='Namespace эсвэл pod нэр буруу.'), 400
        try:
            return jsonify(details(namespace, name))
        except (ValueError, KeyError, TypeError):
            return jsonify(error='Pod мэдээлэл татаж чадсангүй.'), 502

    @bp.get('/api/pod-logs/<namespace>/<name>')
    def pod_logs(namespace, name):
        if not valid(namespace, name):
            return jsonify(error='Namespace эсвэл pod нэр буруу.'), 400
        try:
            data = logs(namespace, name)
            if request.args.get('download') == '1':
                return Response(data['text'], mimetype='text/plain', headers={
                    'Content-Disposition': f'attachment; filename="{namespace}-{name}-{data["container"]}.log"'})
            return jsonify(data)
        except ValueError as error:
            return jsonify(error=str(error)), 400
        except RuntimeError as error:
            return jsonify(error=str(error)), 502

    @bp.get('/api/bug-report/<namespace>/<name>')
    def bug_report(namespace, name):
        if not valid(namespace, name):
            return jsonify(error='Namespace эсвэл pod нэр буруу.'), 400
        try:
            data = details(namespace, name)
        except (ValueError, KeyError, TypeError):
            return jsonify(error='Bug report-ийн pod мэдээлэл боломжгүй.'), 502
        log_error, log_data = None, None
        try:
            log_data = logs(namespace, name)
        except (ValueError, RuntimeError) as error:
            log_error = str(error)
        report = '\n'.join([
            '# QA bug report', '', '## Асуудлын тайлбар', '[Энд бичнэ үү]',
            '', '## Давтах алхам', '1. ', '', '## Хүлээгдсэн үр дүн', '',
            '## Бодит үр дүн', '', f'Namespace: {namespace}', f'Pod: {name}',
            'Captured at (UTC): ' + datetime.now(timezone.utc).isoformat(),
            '', '## Deployment / Pod / Events',
            '    ' + json.dumps(data, ensure_ascii=False, indent=2).replace('\n', '\n    '),
            '', '## Сонгосон лог',
            'Шүүлтүүр: ' + json.dumps({k: v for k, v in (log_data or {}).items() if k != 'text'}, ensure_ascii=False),
            '    ' + (log_error or (log_data or {}).get('text', '') or 'Лог алга.').replace('\n', '\n    '),
        ])
        return Response(report, mimetype='text/markdown', headers={
            'Content-Disposition': f'attachment; filename="{namespace}-{name}-bug-report.md"'})

    @bp.get('/api/smoke/<namespace>')
    def smoke_list(namespace):
        if not valid(namespace):
            return jsonify(error='Namespace буруу.'), 400
        try:
            checks = smoke_config(namespace)
            return jsonify(checks=[{'id': 'deployment-readiness', 'name': 'Deployment / Pod readiness', 'kind': 'cluster'}] +
                           [{'id': c['id'], 'name': c['name'], 'kind': 'HTTP GET',
                             'expected_status': c.get('expected_status', 200)} for c in checks])
        except (OSError, ValueError, TypeError):
            return jsonify(error='Smoke test тохиргоо буруу эсвэл боломжгүй байна.'), 503

    @bp.post('/api/smoke/<namespace>')
    def smoke_run(namespace):
        if request.headers.get('X-QA-Request') != 'smoke' or request.headers.get('Sec-Fetch-Site') == 'cross-site':
            return jsonify(error='Хүсэлт зөвшөөрөгдөөгүй.'), 403
        if not valid(namespace):
            return jsonify(error='Namespace буруу.'), 400
        if request.content_length and request.content_length > 4096:
            return jsonify(error='Хүсэлт хэт том байна.'), 413
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or not isinstance(payload.get('ids'), list) or not payload['ids'] or len(payload['ids']) > 11:
            return jsonify(error='Шалгалтаа сонгоно уу.'), 400
        try:
            configured = {c['id']: c for c in smoke_config(namespace)}
        except (OSError, ValueError, TypeError):
            return jsonify(error='Smoke test тохиргоо боломжгүй.'), 503
        if any(not isinstance(i, str) or i not in configured and i != 'deployment-readiness' for i in payload['ids']):
            return jsonify(error='Тохируулаагүй smoke test.'), 400
        ids = list(dict.fromkeys(payload['ids']))
        results = []
        if 'deployment-readiness' in ids:
            try:
                states, _ = deployment_states(oc_json('deployments', namespace), oc_json('replicasets', namespace), oc_json('pods', namespace))
                blocked = [s for s in states.values() if not s['can_restart']]
                results.append({'id': 'deployment-readiness', 'name': 'Deployment / Pod readiness',
                                'ok': bool(states) and not blocked,
                                'message': f'{len(states) - len(blocked)}/{len(states)} deployment бэлэн.',
                                'blocked': blocked})
            except (ValueError, KeyError, TypeError):
                results.append({'id': 'deployment-readiness', 'name': 'Deployment / Pod readiness',
                                'ok': False, 'message': 'Cluster төлөв татаж чадсангүй.'})
        with ThreadPoolExecutor(max_workers=4) as pool:
            results.extend(pool.map(run_http_check, [configured[i] for i in ids if i in configured]))
        return jsonify(results=results, ok=all(r['ok'] for r in results),
                       completed_at=datetime.now(timezone.utc).isoformat())

    app.register_blueprint(bp)

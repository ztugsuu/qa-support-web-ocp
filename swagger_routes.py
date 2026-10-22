"""Read route directories using isolated, read-only cluster contexts."""
import json
import os
import re
import subprocess
from urllib.parse import quote

from flask import Blueprint, jsonify, render_template, request

swagger_bp = Blueprint('swagger', __name__)
CLUSTERS = {'testocp': 'Unitel', 'testmogul': 'Mogul'}
NAMESPACES = ('test-ms', 'pre-new-ms')


def route_link(route, template):
    spec = route['spec']
    host = spec['host']
    if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?', host):
        raise ValueError('Invalid route host')
    service = spec['to']['name']
    path = template.format(service=quote(service, safe=''), route=quote(route['metadata']['name'], safe=''))
    if not path.startswith('/') or path.startswith('//') or any(c in path for c in '\r\n?#'):
        raise ValueError('Invalid Swagger path')
    return {
        'name': route['metadata']['name'], 'service': service,
        'url': ('https' if spec.get('tls') else 'http') + '://' + host + path,
        'route_path': spec.get('path', ''),
    }


@swagger_bp.get('/swagger')
def swagger_page():
    return render_template('swagger.html', clusters=CLUSTERS, namespaces=NAMESPACES)


@swagger_bp.get('/api/swagger-routes')
def swagger_routes():
    cluster = request.args.get('cluster', 'testocp')
    namespace = request.args.get('namespace', 'test-ms')
    if cluster not in CLUSTERS or namespace not in NAMESPACES:
        return jsonify(error='Cluster эсвэл namespace буруу.'), 400
    kubeconfig = os.environ.get('QA_SWAGGER_KUBECONFIG')
    if not kubeconfig:
        return jsonify(error='Swagger cluster холболт тохируулаагүй байна (QA_SWAGGER_KUBECONFIG).'), 503
    template = os.environ.get('QA_SWAGGER_PATH_TEMPLATE', '/{service}/swagger-ui/swagger-ui/index.html')
    try:
        result = subprocess.check_output(
            ['oc', '--kubeconfig', kubeconfig, '--context', cluster, 'get', 'routes',
             '-n', namespace, '-o', 'json', '--request-timeout=10s'],
            stderr=subprocess.PIPE, timeout=15,
        )
        routes = json.loads(result)['items']
        links = [route_link(route, template) for route in routes]
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        return jsonify(error='Route авах боломжгүй. Cluster холболт, context болон routes get/list эрхийг шалгана уу.'), 502
    return jsonify(routes=sorted(links, key=lambda r: (r['service'], r['name'])))

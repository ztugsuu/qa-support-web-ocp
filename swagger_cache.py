"""Bounded background refresh shared by request threads in a worker."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
import re
import subprocess
from threading import Lock
from time import monotonic
from urllib.parse import quote


def load_routes(cluster, namespace):
    raw = subprocess.check_output(
        ['oc', '--kubeconfig', os.environ['QA_SWAGGER_KUBECONFIG'],
         '--context', cluster, 'get', 'routes', '-n', namespace,
         '-o', 'json', '--request-timeout=8s'],
        stderr=subprocess.PIPE, timeout=12,
    )
    template = os.environ.get('QA_SWAGGER_PATH_TEMPLATE', '/{service}/swagger-ui/swagger-ui/index.html')
    links = []
    for route in json.loads(raw)['items']:
        spec = route['spec']
        host = spec['host']
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]*', host):
            raise ValueError('Invalid route host')
        service = spec['to']['name']
        path = template.format(service=quote(service, safe=''), route=quote(route['metadata']['name'], safe=''))
        if not path.startswith('/') or path.startswith('//') or any(c in path for c in '\r\n?#'):
            raise ValueError('Invalid Swagger path')
        links.append({'name': route['metadata']['name'], 'service': service,
                      'url': ('https' if spec.get('tls') else 'http') + '://' + host + path})
    return sorted(links, key=lambda r: (r['service'], r['name']))


class RouteCache:
    def __init__(self, loader=load_routes, ttl=60, retry=15):
        self.loader, self.ttl, self.retry = loader, ttl, retry
        self.lock = Lock()
        self.entries = {}
        self.pool = ThreadPoolExecutor(max_workers=4)

    def snapshot(self, cluster, namespace):
        key = (cluster, namespace)
        with self.lock:
            entry = self.entries.setdefault(key, {
                'routes': [], 'updated_at': None, 'error': None,
                'refreshing': False, 'next_refresh': 0,
            })
            if not entry['refreshing'] and monotonic() >= entry['next_refresh']:
                entry['refreshing'] = True
                self.pool.submit(self._refresh, key)
            return {k: v for k, v in entry.items() if k != 'next_refresh'}

    def _refresh(self, key):
        try:
            routes = self.loader(*key)
        except Exception:
            with self.lock:
                self.entries[key].update(
                    error='Route шинэчлэх боломжгүй. Cluster холболт болон унших эрхийг шалгана уу.',
                    refreshing=False, next_refresh=monotonic() + self.retry,
                )
        else:
            with self.lock:
                self.entries[key].update(
                    routes=routes, updated_at=datetime.now(timezone.utc).isoformat(),
                    error=None, refreshing=False, next_refresh=monotonic() + self.ttl,
                )

import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch
import json
from swagger_cache import RouteCache, load_routes


class CacheTests(unittest.TestCase):
    def test_thirty_concurrent_visitors_share_one_query(self):
        started, release = Event(), Event()
        calls = []
        def loader(*key):
            calls.append(key)
            started.set()
            release.wait(3)
            return [{'service': 'demo'}]
        cache = RouteCache(loader)
        try:
            with ThreadPoolExecutor(max_workers=30) as clients:
                snapshots = list(clients.map(lambda _: cache.snapshot('testocp', 'test-ms'), range(30)))
            self.assertTrue(started.wait(1))
            self.assertEqual(len(calls), 1)
            self.assertTrue(all(s['refreshing'] for s in snapshots))
        finally:
            release.set()
            cache.pool.shutdown()
        self.assertEqual(cache.snapshot('testocp', 'test-ms')['routes'], [{'service': 'demo'}])

    def test_failure_keeps_previous_routes_and_backs_off(self):
        cache = RouteCache(lambda *_: [{'service': 'cached'}])
        cache.snapshot('testocp', 'test-ms')
        cache.pool.shutdown()
        cache.pool = ThreadPoolExecutor(max_workers=1)
        def fail(*_):
            raise RuntimeError('private-token')
        cache.loader = fail
        cache.entries[('testocp', 'test-ms')]['next_refresh'] = 0
        cache.snapshot('testocp', 'test-ms')
        cache.pool.shutdown()
        result = cache.snapshot('testocp', 'test-ms')
        self.assertEqual(result['routes'], [{'service': 'cached'}])
        self.assertNotIn('private-token', result['error'])
        self.assertFalse(result['refreshing'])

    @patch.dict('os.environ', {'QA_SWAGGER_KUBECONFIG': '/secret/config'})
    @patch('swagger_cache.subprocess.check_output')
    def test_real_host_tls_and_service_path(self, run):
        run.return_value = json.dumps({'items': [{'metadata': {'name': 'alias'}, 'spec': {
            'host': 'actual.example.local', 'tls': {'termination': 'edge'},
            'to': {'name': 'm-tms-gateway-service'}}}]}).encode()
        result = load_routes('testmogul', 'pre-new-ms')
        self.assertEqual(result[0]['url'], 'https://actual.example.local/m-tms-gateway-service/swagger-ui/swagger-ui/index.html')
        args = run.call_args.args[0]
        self.assertIn('testmogul', args)
        self.assertIn('pre-new-ms', args)
        self.assertEqual(run.call_args.kwargs['timeout'], 12)

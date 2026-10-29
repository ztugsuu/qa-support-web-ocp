from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic
import unittest
from unittest.mock import patch
from app import app
from swagger_cache import RouteCache


class SwaggerAppTests(unittest.TestCase):
    def test_only_swagger_and_health_routes_registered(self):
        with app.test_client() as client:
            for path in ('/', '/swagger', '/healthz'):
                self.assertEqual(client.get(path).status_code, 200)
            for path in ('/qa', '/api/pods', '/allowlist'):
                self.assertEqual(client.get(path).status_code, 404)
            self.assertNotIn(b'dashboard.js', client.get('/').data)
            self.assertEqual(client.get('/api/swagger-routes?cluster=other').status_code, 400)

    @patch.dict('os.environ', {'QA_SWAGGER_KUBECONFIG': '/secret/config'})
    def test_thirty_http_clients_do_not_wait_for_cluster(self):
        release = Event()
        calls = []
        def loader(*key):
            calls.append(key)
            release.wait(5)
            return []
        cache = RouteCache(loader)
        def visit(_):
            with app.test_client() as client:
                return client.get('/api/swagger-routes?cluster=testocp&namespace=test-ms')
        try:
            with patch('app.cache', cache):
                start = monotonic()
                with ThreadPoolExecutor(max_workers=30) as clients:
                    responses = list(clients.map(visit, range(30)))
                self.assertLess(monotonic() - start, 3)
                self.assertTrue(all(r.status_code == 200 for r in responses))
                self.assertTrue(all(r.json['refreshing'] for r in responses))
        finally:
            release.set()
            cache.pool.shutdown()
        self.assertEqual(len(calls), 1)

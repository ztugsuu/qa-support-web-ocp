import json
import os
import subprocess
import unittest
from unittest.mock import patch

from flask import Flask
from swagger_routes import route_link, swagger_bp


class SwaggerTests(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__, template_folder='../templates')
        app.register_blueprint(swagger_bp)
        self.client = app.test_client()
        self.route = {'metadata': {'name': 'route-alias'}, 'spec': {
            'host': 'custom.apps.ocp.mbank.local',
            'to': {'name': 'm-tms-gateway-service'}}}

    def test_real_host_and_service_path(self):
        link = route_link(self.route, '/{service}/swagger-ui/swagger-ui/index.html')
        self.assertEqual(link['url'], 'http://custom.apps.ocp.mbank.local/m-tms-gateway-service/swagger-ui/swagger-ui/index.html')
        self.route['spec']['tls'] = {'termination': 'edge'}
        self.assertTrue(route_link(self.route, '/docs')['url'].startswith('https://'))

    @patch.dict(os.environ, {'QA_SWAGGER_KUBECONFIG': '/private/config'})
    @patch('swagger_routes.subprocess.check_output')
    def test_explicit_context_and_namespace(self, run):
        run.return_value = json.dumps({'items': [self.route]}).encode()
        response = self.client.get('/api/swagger-routes?cluster=testmogul&namespace=pre-new-ms')
        self.assertEqual(response.status_code, 200)
        args = run.call_args.args[0]
        self.assertEqual(args[1:5], ['--kubeconfig', '/private/config', '--context', 'testmogul'])
        self.assertIn('pre-new-ms', args)
        self.assertEqual(len(response.json['routes']), 1)

    @patch('swagger_routes.subprocess.check_output')
    def test_invalid_cluster_never_queries(self, run):
        self.assertEqual(self.client.get('/api/swagger-routes?cluster=other').status_code, 400)
        run.assert_not_called()

    @patch.dict(os.environ, {}, clear=True)
    def test_missing_configuration(self):
        self.assertEqual(self.client.get('/api/swagger-routes').status_code, 503)

    @patch.dict(os.environ, {'QA_SWAGGER_KUBECONFIG': '/private/config'})
    @patch('swagger_routes.subprocess.check_output')
    def test_failure_hides_credentials(self, run):
        run.side_effect = subprocess.CalledProcessError(1, 'oc', stderr=b'secret-token')
        response = self.client.get('/api/swagger-routes')
        self.assertEqual(response.status_code, 502)
        self.assertNotIn('secret-token', response.get_data(as_text=True))


if __name__ == '__main__':
    unittest.main()

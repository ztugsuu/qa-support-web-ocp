import copy
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import psycopg
from flask import Flask
from app import app as dashboard
from audit import history_page, save_event
from qa_features import deployment_info, register_qa, run_http_check, smoke_config, NoRedirect


def fixture():
    deployment = {
        'metadata': {'name': 'demo', 'uid': 'dep-1', 'generation': 2, 'creationTimestamp': '2025-01-01T00:00:00Z',
                     'annotations': {'deployment.kubernetes.io/revision': '3'}},
        'spec': {'replicas': 1, 'template': {'metadata': {'annotations': {'qa.m-bank.mn/build-number': '101',
                 'qa.m-bank.mn/deployed-at': '2026-09-01T00:00:00Z'}}, 'spec': {'containers': [{'name': 'app', 'image': 'demo:v1'}]}}},
        'status': {'observedGeneration': 2, 'replicas': 1, 'updatedReplicas': 1, 'readyReplicas': 1, 'availableReplicas': 1},
    }
    rs = {'metadata': {'uid': 'rs-1', 'ownerReferences': [{'kind': 'Deployment', 'controller': True, 'uid': 'dep-1', 'name': 'demo'}]}}
    pod = {'metadata': {'name': 'demo-pod', 'uid': 'pod-1', 'ownerReferences': [{'kind': 'ReplicaSet', 'controller': True, 'uid': 'rs-1', 'name': 'demo-rs'}]},
           'spec': {'containers': [{'name': 'app', 'image': 'demo:v1', 'env': [{'name': 'SECRET', 'value': 'do-not-export'}]}, {'name': 'sidecar', 'image': 'side:v1'}]},
           'status': {'phase': 'Running', 'conditions': [{'type': 'Ready', 'status': 'True'}], 'containerStatuses': [
               {'name': 'app', 'ready': True, 'restartCount': 0, 'imageID': 'sha256:abc', 'state': {'running': {}}},
               {'name': 'sidecar', 'ready': True, 'restartCount': 0, 'state': {'running': {}}}]}}
    return deployment, rs, pod


class FeaturesTests(unittest.TestCase):
    def setUp(self):
        self.deployment, self.rs, self.pod = fixture()
        self.oc = MagicMock(side_effect=self.read)
        self.command = MagicMock(side_effect=self.run_command)
        self.app = Flask(__name__)
        self.app.testing = True
        register_qa(self.app, self.oc, self.command, ['test-ms', 'pre-new-ms'])
        self.client = self.app.test_client()

    def read(self, resource, namespace, name=None):
        if resource == 'pod': return self.pod
        if resource == 'replicaset': return self.rs
        if resource == 'deployment': return self.deployment
        return {'deployments': [self.deployment], 'replicasets': [self.rs], 'pods': [self.pod]}[resource]

    def run_command(self, command, namespace):
        if command[1] == 'logs':
            return '2026-09-17T00:00:00Z INFO trace-A ok\n2026-09-17T00:01:00Z ERROR trace-A broken\n2026-09-17T00:02:00Z WARNING trace-B slow\n<script>alert(1)</script>\n'
        return json.dumps({'items': [{'type': 'Warning', 'reason': 'BackOff', 'message': 'Restarting', 'count': 3, 'lastTimestamp': '2026-09-17T00:00:00Z'}]})

    def test_version_metadata_does_not_invent_deployment_date(self):
        data = deployment_info(self.deployment)
        self.assertEqual(data['build'], '101')
        self.assertEqual(data['images'], {'app': 'demo:v1'})
        del self.deployment['spec']['template']['metadata']['annotations']
        data = deployment_info(self.deployment)
        self.assertIsNone(data['build'])
        self.assertIsNone(data['deployed_at'])
        self.assertEqual(data['created_at'], '2025-01-01T00:00:00Z')

    def test_compare_partial_failure_is_not_an_empty_healthy_environment(self):
        def read(resource, namespace, name=None):
            if namespace == 'pre-new-ms': raise ValueError('Forbidden')
            return [self.deployment]
        self.oc.side_effect = read
        data = self.client.get('/api/deployments/compare').json
        self.assertIsNone(data['environments']['test-ms']['error'])
        self.assertEqual(data['environments']['test-ms']['items'][0]['images']['app'], 'demo:v1')
        self.assertTrue(data['environments']['pre-new-ms']['error'])

    def test_details_and_events_use_uid_without_exporting_env(self):
        response = self.client.get('/api/pod-details/test-ms/demo-pod')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['deployment']['name'], 'demo')
        self.assertEqual(response.json['containers'][0]['image_id'], 'sha256:abc')
        self.assertEqual(response.json['events'][0]['reason'], 'BackOff')
        self.assertIn('--field-selector=involvedObject.uid=pod-1', self.command.call_args.args[0])
        self.assertNotIn('do-not-export', response.text)

    def test_events_permission_failure_keeps_pod_details(self):
        self.command.side_effect = None
        self.command.return_value = 'ERROR: forbidden'
        response = self.client.get('/api/pod-details/test-ms/demo-pod')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json['events_error'])
        self.assertTrue(response.json['containers'])

    def test_recreated_owner_not_misidentified(self):
        self.rs['metadata']['uid'] = 'replaced'
        data = self.client.get('/api/pod-details/test-ms/demo-pod').json
        self.assertIsNone(data['deployment'])
        self.assertTrue(data['deployment_error'])

    def test_previous_logs_filter_container_time_level_and_trace(self):
        data = self.client.get('/api/pod-logs/test-ms/demo-pod?container=sidecar&previous=1&since=15m&tail=500&q=trace-A&level=ERROR').json
        self.assertEqual(data['matched_lines'], 1)
        self.assertIn('broken', data['text'])
        self.assertNotIn('INFO', data['text'])
        command = self.command.call_args.args[0]
        for arg in ['--previous=true', '--since=15m', '--tail=500', '--limit-bytes=1048576', 'sidecar']:
            self.assertIn(arg, command)

    def test_warning_alias_and_download_same_filtered_content(self):
        response = self.client.get('/api/pod-logs/test-ms/demo-pod?level=WARN&download=1')
        self.assertIn('WARNING trace-B', response.text)
        self.assertNotIn('ERROR', response.text)
        self.assertIn('attachment;', response.headers['Content-Disposition'])
        self.assertEqual(response.mimetype, 'text/plain')

    def test_log_error_is_not_downloaded_as_success(self):
        self.command.side_effect = None
        self.command.return_value = 'ERROR: previous instance missing'
        response = self.client.get('/api/pod-logs/test-ms/demo-pod?previous=1&download=1')
        self.assertEqual(response.status_code, 502)
        self.assertNotIn('Content-Disposition', response.headers)

    def test_bad_log_filters_cannot_inject_oc_flags(self):
        for suffix in ['?tail=5001', '?tail=-1', '?tail=no', '?container=--all', '?previous=yes', '?since=2years', '?level=INVALID']:
            response = self.client.get('/api/pod-logs/test-ms/demo-pod' + suffix)
            self.assertEqual(response.status_code, 400, suffix)
        for path in ['/api/pod-logs/prod/demo', '/api/pod-details/test-ms/--all', '/api/bug-report/prod/demo']:
            self.assertEqual(self.client.get(path).status_code, 400)
        self.command.assert_not_called()

    def test_bug_report_preserves_filters_and_excludes_env(self):
        response = self.client.get('/api/bug-report/test-ms/demo-pod?level=ERROR&q=trace-A')
        self.assertEqual(response.status_code, 200)
        self.assertIn('demo:v1', response.text)
        self.assertIn('BackOff', response.text)
        self.assertIn('ERROR trace-A broken', response.text)
        self.assertNotIn('INFO trace-A', response.text)
        self.assertNotIn('do-not-export', response.text)
        self.assertIn('bug-report.md', response.headers['Content-Disposition'])

    def test_smoke_default_readiness_and_rejected_arbitrary_url(self):
        self.assertEqual(self.client.get('/api/smoke/test-ms').json['checks'][0]['id'], 'deployment-readiness')
        headers = {'X-QA-Request': 'smoke'}
        response = self.client.post('/api/smoke/test-ms', headers=headers, json={'ids': ['deployment-readiness']})
        self.assertTrue(response.json['ok'])
        self.pod['status']['containerStatuses'][0]['ready'] = False
        self.assertFalse(self.client.post('/api/smoke/test-ms', headers=headers, json={'ids': ['deployment-readiness']}).json['ok'])
        with patch('qa_features.run_http_check') as http:
            response = self.client.post('/api/smoke/test-ms', headers=headers, json={'ids': ['http://unexpected/']})
            self.assertEqual(response.status_code, 400)
            http.assert_not_called()

    def test_smoke_validation_rejects_cross_site_and_invalid_requests(self):
        self.assertEqual(self.client.post('/api/smoke/test-ms', json={'ids':['deployment-readiness']}).status_code, 403)
        for payload in [{}, {'ids':[]}, {'ids':[[1]]}, {'ids':['unknown']}]:
            self.assertEqual(self.client.post('/api/smoke/test-ms', json=payload, headers={'X-QA-Request':'smoke'}).status_code, 400)
        self.oc.assert_not_called()

    def test_configured_smoke_ids_hide_url_token_and_run_selected_only(self):
        checks = [{'id':'health','name':'Health','url':'https://example.invalid/health?private=value','bearer_token_env':'TEST_SECRET'}]
        with patch('qa_features.smoke_config', return_value=checks), patch('qa_features.run_http_check', return_value={'id':'health','name':'Health','ok':True}) as run:
            response = self.client.get('/api/smoke/test-ms')
            self.assertNotIn('private',response.text)
            self.assertNotIn('TEST_SECRET',response.text)
            result = self.client.post('/api/smoke/test-ms',json={'ids':['health','health']},headers={'X-QA-Request':'smoke'})
            self.assertTrue(result.json['ok'])
            run.assert_called_once()


class HttpSmokeTests(unittest.TestCase):
    def test_status_and_body_assertions(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.code = 200
        response.read.return_value = b'{"status":"UP"}'
        opener = MagicMock()
        opener.open.return_value = response
        check = {'id':'health','name':'Health','url':'https://example.invalid/health','body_contains':'UP'}
        with patch('qa_features.build_opener',return_value=opener):
            result = run_http_check(check)
            self.assertTrue(result['ok'])
            self.assertNotIn('body',result)
            check['expected_status'] = 204
            self.assertFalse(run_http_check(check)['ok'])
        self.assertEqual(opener.open.call_args.kwargs['timeout'],5)

    def test_timeout_and_missing_token_report_failure(self):
        check = {'id':'health','name':'Health','url':'https://example.invalid/health'}
        opener = MagicMock()
        opener.open.side_effect = TimeoutError()
        with patch('qa_features.build_opener',return_value=opener):
            self.assertFalse(run_http_check(check)['ok'])
        check['bearer_token_env'] = 'NON_EXISTENT_QA_TOKEN'
        with patch.dict(os.environ,{},clear=True):
            self.assertFalse(run_http_check(check)['ok'])
        self.assertIsNone(NoRedirect().redirect_request(None,None,302,'',{},'https://other.invalid'))

    def test_token_never_appears_in_invalid_header_error(self):
        check = {'id':'health','name':'Health','url':'https://example.invalid/health','bearer_token_env':'QA_TEST_TOKEN'}
        with patch.dict(os.environ,{'QA_TEST_TOKEN':'private-token\ninvalid'}):
            result = run_http_check(check)
        self.assertFalse(result['ok'])
        self.assertNotIn('private-token',json.dumps(result))

    def test_invalid_config_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder,'smoke.json')
            for check in [{'id':'x','name':'X','url':'file:///etc/passwd'},
                          {'id':'x','name':'X','url':'https://user:password@example.invalid'},
                          {'id':'x','name':'X','url':'https://example.invalid','expected_status':True}]:
                path.write_text(json.dumps({'test-ms':[check]}),encoding='utf-8')
                with patch.dict(os.environ,{'QA_SMOKE_CONFIG':str(path)}):
                    with self.assertRaises(ValueError): smoke_config('test-ms')


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.client = dashboard.test_client()

    def test_allowlist_partial_outcomes_without_pii(self):
        with patch('allowlist.add_entries',side_effect=[{'ok':True,'entries':{'email':'added'}},{'ok':False,'error':'offline'}]), patch('audit.save_event') as save:
            response = self.client.post('/api/allowlist',json={'environments':['test','pre'],'email':'private@example.com'},headers={'X-QA-Request':'allowlist'})
        self.assertEqual(response.status_code,200)
        events = [c.args[0] for c in save.call_args_list]
        self.assertEqual({e['namespace']:e['outcome'] for e in events},{'test-ms':'success','pre-new-ms':'failed'})
        self.assertNotIn('private@example.com',json.dumps(events))

    def test_history_failure_does_not_turn_success_into_failure(self):
        with patch('allowlist.add_entries',return_value={'ok':True,'entries':{'phone':'added'}}), patch('audit.save_event',side_effect=psycopg.OperationalError('private connection')):
            response = self.client.post('/api/allowlist',json={'environments':['test'],'phone':'12345678'},headers={'X-QA-Request':'allowlist'})
        self.assertTrue(response.json['results']['test']['ok'])
        self.assertTrue(response.json['audit_warning'])
        self.assertNotIn('private connection',response.text)

    def test_history_filters_and_unavailable_response(self):
        with patch('audit.history_page',return_value={'items':[],'page':2,'has_more':False}) as history:
            self.assertEqual(self.client.get('/api/history?namespace=test-ms&action=smoke&page=2').status_code,200)
            history.assert_called_once_with('test-ms','smoke',2)
            for query in ['page=0','namespace=prod','action=delete','page=no']:
                self.assertEqual(self.client.get('/api/history?' + query).status_code,400)
        with patch('audit.history_page',side_effect=psycopg.OperationalError('private db')):
            response = self.client.get('/api/history')
            self.assertEqual(response.status_code,503)
            self.assertNotIn('private db',response.text)

    def test_sql_parameters_and_pagination(self):
        connection = MagicMock()
        database = MagicMock()
        database.return_value.__enter__.return_value = connection
        connection.execute.return_value.fetchall.return_value = [
            ('id',datetime.now(timezone.utc),'smoke','test-ms','','success',200,{}) for _ in range(51)]
        with patch('audit.audit_database',database):
            data = history_page('test-ms','smoke',2)
        self.assertTrue(data['has_more'])
        self.assertEqual(len(data['items']),50)
        self.assertEqual(connection.execute.call_args.args[1],('test-ms','smoke',50))
        self.assertNotIn('test-ms',connection.execute.call_args.args[0])

    def test_pages_and_assets_render(self):
        for path in ['/qa','/logs/test-ms/demo','/static/qa.js','/static/pod-details.js','/healthz']:
            with self.client.get(path) as response:
                self.assertEqual(response.status_code,200,path)
        self.assertEqual(self.client.get('/logs/prod/demo').status_code,400)

    def test_migration_is_explicit_and_idempotent_ddl_only(self):
        database = MagicMock()
        with patch('audit.audit_database',database):
            result = dashboard.test_cli_runner().invoke(args=['init-audit-db'])
        self.assertEqual(result.exit_code,0,result.output)
        ddl = database.return_value.__enter__.return_value.execute.call_args.args[0]
        self.assertIn('CREATE TABLE IF NOT EXISTS qa_support.action_history',ddl)
        self.assertNotIn('DROP',ddl)


if __name__ == '__main__': unittest.main()

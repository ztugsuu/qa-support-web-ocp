import copy
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app import app
from rollout import deployment_states, pod_block_reason, pod_summary


def fixture():
    deployment = {
        'metadata': {'name': 'demo', 'uid': 'deployment-1', 'generation': 4, 'resourceVersion': '42'},
        'spec': {'replicas': 1, 'template': {'metadata': {'annotations': {'existing': 'keep'}}}},
        'status': {'observedGeneration': 4, 'replicas': 1, 'updatedReplicas': 1,
                   'readyReplicas': 1, 'availableReplicas': 1},
    }
    replica_set = {'metadata': {'uid': 'rs-1', 'ownerReferences': [
        {'kind': 'Deployment', 'uid': 'deployment-1', 'controller': True}]}}
    pod = {
        'metadata': {'name': 'demo-pod', 'ownerReferences': [
            {'kind': 'ReplicaSet', 'uid': 'rs-1', 'controller': True}]},
        'spec': {'containers': [{'name': 'app'}]},
        'status': {'phase': 'Running', 'conditions': [{'type': 'Ready', 'status': 'True'}],
                   'containerStatuses': [{'name': 'app', 'ready': True, 'restartCount': 0,
                                          'state': {'running': {'startedAt': '2025-01-01T00:00:00Z'}}}]},
    }
    return deployment, replica_set, pod


class RolloutTests(unittest.TestCase):
    def setUp(self):
        self.deployment, self.rs, self.pod = fixture()
        self.client = app.test_client()

    def state(self, pods=None):
        states, _ = deployment_states([self.deployment], [self.rs], pods if pods is not None else [self.pod])
        return states['deployment-1']

    def test_healthy_deployment_enabled(self):
        self.assertTrue(self.state()['can_restart'])

    def test_rollout_status_blocks_until_every_replica_is_ready(self):
        for key in ['replicas', 'updatedReplicas', 'readyReplicas', 'availableReplicas', 'observedGeneration']:
            with self.subTest(key=key):
                saved = self.deployment['status'][key]
                self.deployment['status'][key] = 0
                self.assertFalse(self.state()['can_restart'])
                self.deployment['status'][key] = saved
        for key in ['unavailableReplicas', 'terminatingReplicas']:
            self.deployment['status'][key] = 1
            self.assertFalse(self.state()['can_restart'])
            del self.deployment['status'][key]

    def test_running_pod_with_crash_loop_blocks_restart(self):
        container = self.pod['status']['containerStatuses'][0]
        container.update(ready=False, restartCount=10, state={'waiting': {'reason': 'CrashLoopBackOff'}})
        self.assertFalse(self.state()['can_restart'])
        self.assertEqual(pod_summary(self.pod)['status'], 'CrashLoopBackOff')

    def test_recent_restarts_block_even_when_ready_then_recover(self):
        now = datetime.now(timezone.utc)
        container = self.pod['status']['containerStatuses'][0]
        container['restartCount'] = 12
        for seconds in [0, 60, 299]:
            container['state']['running']['startedAt'] = (now - timedelta(seconds=seconds)).isoformat()
            self.assertIsNotNone(pod_block_reason(self.pod, now))
        container['state']['running']['startedAt'] = (now - timedelta(seconds=300)).isoformat()
        self.assertIsNone(pod_block_reason(self.pod, now))
        container['state']['running'] = {}
        self.assertIsNotNone(pod_block_reason(self.pod, now))

    def test_all_pods_and_containers_required(self):
        self.pod['spec']['containers'].append({'name': 'second'})
        self.assertFalse(self.state()['can_restart'])
        self.pod['spec']['containers'].pop()
        other = copy.deepcopy(self.pod)
        other['metadata']['name'] = 'second-pod'
        other['status']['conditions'][0]['status'] = 'False'
        self.deployment['spec']['replicas'] = 2
        for key in ['replicas', 'updatedReplicas', 'readyReplicas', 'availableReplicas']:
            self.deployment['status'][key] = 2
        self.assertFalse(self.state([self.pod, other])['can_restart'])
        other['status']['conditions'][0]['status'] = 'True'
        self.assertTrue(self.state([self.pod, other])['can_restart'])

    def test_readiness_gate_and_pending_failed_terminating_block(self):
        for phase in ['Pending', 'Failed', 'Unknown', 'Succeeded']:
            self.pod['status']['phase'] = phase
            self.assertFalse(self.state()['can_restart'])
        self.pod['status']['phase'] = 'Running'
        self.pod['status']['conditions'][0]['status'] = 'False'
        self.assertFalse(self.state()['can_restart'])
        self.assertEqual(pod_summary(self.pod)['status'], 'NotReady')
        self.pod['status']['conditions'][0]['status'] = 'True'
        self.pod['metadata']['deletionTimestamp'] = '2026-01-01T00:00:00Z'
        self.assertFalse(self.state()['can_restart'])

    def test_old_terminating_or_missing_pods_block(self):
        self.assertFalse(self.state([])['can_restart'])
        old = copy.deepcopy(self.pod)
        old['metadata'].update(name='old-pod', deletionTimestamp='2026-01-01T00:00:00Z')
        self.assertFalse(self.state([self.pod, old])['can_restart'])

    def test_paused_zero_and_failed_deployment_block(self):
        self.deployment['spec']['paused'] = True
        self.assertFalse(self.state()['can_restart'])
        del self.deployment['spec']['paused']
        self.deployment['spec']['replicas'] = 0
        self.assertFalse(self.state()['can_restart'])
        self.deployment['spec']['replicas'] = 1
        self.deployment['status']['conditions'] = [{'type': 'Progressing', 'status': 'False'}]
        self.assertFalse(self.state()['can_restart'])

    def test_completed_init_and_native_sidecar(self):
        self.pod['spec']['initContainers'] = [{'name': 'setup'}]
        self.pod['status']['initContainerStatuses'] = [
            {'name': 'setup', 'state': {'terminated': {'exitCode': 0}}, 'ready': False}]
        self.assertTrue(self.state()['can_restart'])
        self.pod['spec']['initContainers'][0]['restartPolicy'] = 'Always'
        self.assertFalse(self.state()['can_restart'])
        self.pod['status']['initContainerStatuses'][0].update(ready=True, state={'running': {}})
        self.assertTrue(self.state()['can_restart'])

    def test_owner_uids_not_name_prefixes(self):
        self.rs['metadata']['ownerReferences'][0]['uid'] = 'another-deployment'
        self.assertFalse(self.state()['can_restart'])

    def oc(self, cmd, namespace):
        if cmd[1] == 'patch':
            return 'deployment.apps/demo patched'
        resource = cmd[2]
        if resource == 'deployment':
            return json.dumps(self.deployment)
        return json.dumps({'items': {'deployments': [self.deployment],
                                    'replicasets': [self.rs], 'pods': [self.pod]}[resource]})

    def restart(self, **kwargs):
        return self.client.post('/api/deployments/test-ms/demo/restart',
                                headers={'X-QA-Request': 'rollout-restart'}, **kwargs)

    def test_list_and_restart_share_checks(self):
        with patch('app.run_oc_command', side_effect=self.oc) as oc:
            self.assertTrue(self.client.get('/api/pods').json[0]['can_restart'])
            self.pod['status']['containerStatuses'][0]['ready'] = False
            self.assertFalse(self.client.get('/api/pods').json[0]['can_restart'])
            self.assertEqual(self.restart().status_code, 409)
            self.assertFalse(any(call.args[0][1] == 'patch' for call in oc.call_args_list))

    def test_restart_preserves_annotations_and_tests_resource_version(self):
        with patch('app.run_oc_command', side_effect=self.oc) as oc:
            self.assertEqual(self.restart().status_code, 202)
        cmd = oc.call_args.args[0]
        operations = json.loads(cmd[cmd.index('-p') + 1])
        self.assertEqual(operations[0], {'op': 'test', 'path': '/metadata/resourceVersion', 'value': '42'})
        self.assertEqual(operations[1]['value']['existing'], 'keep')
        self.assertIn('kubectl.kubernetes.io/restartedAt', operations[1]['value'])

    def test_duplicate_after_patch_blocks_before_controller_observes(self):
        def oc(cmd, namespace):
            result = self.oc(cmd, namespace)
            if cmd[1] == 'patch':
                self.deployment['metadata']['generation'] += 1
            return result
        with patch('app.run_oc_command', side_effect=oc) as mocked:
            self.assertEqual(self.restart().status_code, 202)
            self.assertEqual(self.restart().status_code, 409)
            self.assertEqual(sum(c.args[0][1] == 'patch' for c in mocked.call_args_list), 1)

    def test_patch_conflict_or_permission_failure_not_success(self):
        with patch('app.run_oc_command', side_effect=lambda cmd, ns: 'ERROR: conflict' if cmd[1] == 'patch' else self.oc(cmd, ns)):
            self.assertEqual(self.restart().status_code, 502)

    def test_cluster_failures_disable_restart(self):
        for failure in ['ERROR: forbidden', 'not-json', '{}']:
            with patch('app.run_oc_command', return_value=failure) as oc:
                self.assertEqual(self.restart().status_code, 502)
                self.assertFalse(any(c.args[0][1] == 'patch' for c in oc.call_args_list))
        with patch('app.run_oc_command', side_effect=lambda cmd, ns: self.oc(cmd, ns) if cmd[2] == 'pods' else 'ERROR: forbidden'):
            response = self.client.get('/api/pods')
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.json[0]['can_restart'])

    def test_invalid_or_cross_site_requests_never_contact_cluster(self):
        with patch('app.run_oc_command') as oc:
            self.assertEqual(self.client.get('/api/deployments/test-ms/demo/restart').status_code, 405)
            self.assertEqual(self.client.post('/api/deployments/test-ms/demo/restart').status_code, 403)
            self.assertEqual(self.client.post('/api/deployments/test-ms/demo/restart', headers={
                'X-QA-Request': 'rollout-restart', 'Sec-Fetch-Site': 'cross-site'}).status_code, 403)
            for path in ['/api/deployments/prod/demo/restart', '/api/deployments/test-ms/--all/restart']:
                self.assertEqual(self.client.post(path, headers={'X-QA-Request': 'rollout-restart'}).status_code, 400)
            oc.assert_not_called()


if __name__ == '__main__':
    unittest.main()

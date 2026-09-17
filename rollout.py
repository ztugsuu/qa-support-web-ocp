"""Deployment restart eligibility, shared by the dashboard and write endpoint."""
from datetime import datetime, timezone

# A recovered container must remain running this long before another rollout.
RESTART_STABILITY_SECONDS = 300


def controller_uid(resource, kind):
    for owner in resource.get('metadata', {}).get('ownerReferences', []):
        if owner.get('kind') == kind and owner.get('controller') is True:
            return owner.get('uid')
    return None


def pod_block_reason(pod, now=None):
    now = now or datetime.now(timezone.utc)
    status = pod.get('status', {})
    if pod.get('metadata', {}).get('deletionTimestamp'):
        return 'Pod устаж байна.'
    if status.get('phase') != 'Running':
        return 'Pod бүрэн асаагүй байна.'
    if not any(c.get('type') == 'Ready' and c.get('status') == 'True'
               for c in status.get('conditions', [])):
        return 'Pod Ready болоогүй байна.'
    for spec_key, status_key in [('containers', 'containerStatuses'),
                                 ('initContainers', 'initContainerStatuses')]:
        statuses = {c['name']: c for c in status.get(status_key, [])}
        containers = pod.get('spec', {}).get(spec_key, [])
        if spec_key == 'containers' and not containers:
            return 'Container мэдээлэл дутуу байна.'
        for container in containers:
            current = statuses.get(container['name'], {})
            state = current.get('state', {})
            # Regular init containers finish; native sidecars keep running.
            if spec_key == 'initContainers' and container.get('restartPolicy') != 'Always':
                if state.get('terminated', {}).get('exitCode') == 0:
                    continue
                return 'Init container бүрэн асаагүй байна.'
            if not current.get('ready') or 'running' not in state or current.get('started') is False:
                return 'Container бэлэн биш эсвэл дахин асаж байна.'
            if current.get('restartCount', 0) > 0:
                try:
                    started = datetime.fromisoformat(state['running']['startedAt'].replace('Z', '+00:00'))
                    stable = (now - started).total_seconds() >= RESTART_STABILITY_SECONDS
                except (KeyError, ValueError, TypeError):
                    stable = False
                if not stable:
                    return 'Restart хийсэн container 5 минут тогтвортой ажиллахыг хүлээж байна.'
    return None


def deployment_states(deployments, replica_sets, pods):
    rs_owners = {rs['metadata']['uid']: controller_uid(rs, 'Deployment') for rs in replica_sets}
    pod_owners = {p['metadata']['name']: rs_owners.get(controller_uid(p, 'ReplicaSet')) for p in pods}
    states = {}
    for deployment in deployments:
        metadata = deployment['metadata']
        spec, status = deployment.get('spec', {}), deployment.get('status', {})
        desired = spec.get('replicas', 1)
        owned = [p for p in pods if pod_owners[p['metadata']['name']] == metadata['uid']]
        reason = None
        if metadata.get('deletionTimestamp') or spec.get('paused'):
            reason = 'Deployment түр зогссон эсвэл устаж байна.'
        elif desired < 1:
            reason = 'Deployment-ийн replica 0 байна.'
        elif (status.get('observedGeneration', 0) < metadata.get('generation', 1)
              or any(status.get(key, 0) != desired for key in
                     ('replicas', 'updatedReplicas', 'readyReplicas', 'availableReplicas'))
              or status.get('unavailableReplicas', 0) > 0
              or status.get('terminatingReplicas', 0) > 0):
            reason = 'Rollout дуусаагүй эсвэл бүх replica бэлэн болоогүй байна.'
        elif any((c.get('type') == 'ReplicaFailure' and c.get('status') == 'True')
                 or (c.get('type') == 'Progressing' and c.get('status') == 'False')
                 for c in status.get('conditions', [])):
            reason = 'Deployment алдаатай байна.'
        elif len(owned) != desired:
            reason = 'Бүх pod бүрэн асаж, хуучин pod устахыг хүлээж байна.'
        else:
            reason = next((reason for p in owned if (reason := pod_block_reason(p))), None)
        states[metadata['uid']] = {
            'deployment': metadata['name'], 'can_restart': reason is None,
            'restart_reason': reason or 'Бүх pod бэлэн. Rollout restart хийх боломжтой.',
        }
    return states, pod_owners


def pod_summary(pod):
    status = pod.get('status', {})
    containers = status.get('initContainerStatuses', []) + status.get('containerStatuses', [])
    phase = status.get('phase', 'Unknown')
    reason = next((c.get('state', {}).get('waiting', {}).get('reason') for c in containers
                   if c.get('state', {}).get('waiting', {}).get('reason')), None)
    if pod.get('metadata', {}).get('deletionTimestamp'):
        phase = 'Terminating'
    elif reason:
        phase = reason
    elif phase == 'Running' and pod_block_reason(pod):
        phase = 'NotReady' if not all(c.get('ready') for c in status.get('containerStatuses', [])) else phase
        if not any(c.get('type') == 'Ready' and c.get('status') == 'True' for c in status.get('conditions', [])):
            phase = 'NotReady'
    return {'name': pod['metadata']['name'], 'status': phase,
            'restarts': str(sum(c.get('restartCount', 0) for c in containers)),
            'age': pod['metadata'].get('creationTimestamp', '')}

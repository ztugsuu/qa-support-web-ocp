from flask import Flask, render_template, jsonify, request
import subprocess
import json
import re
from datetime import datetime, timezone
from rollout import deployment_states, pod_summary

app = Flask(__name__)
from allowlist import allowlist_bp
app.register_blueprint(allowlist_bp)

NAMESPACES = ["test-ms", "pre-new-ms"]

def run_oc_command(cmd, namespace):
    try:
        full_cmd = cmd + ["-n", namespace]
        # "oc top" заримдаа stderr руу мэдээлэл хаядаг тул үүнийг анхаарна уу
        return subprocess.check_output(full_cmd, stderr=subprocess.STDOUT).decode("utf-8")
    except Exception as e:
        return f"ERROR: {str(e)}"

@app.route('/')
def index():
    return render_template('index.html', namespaces=NAMESPACES)


def oc_json(resource, namespace, name=None):
    cmd = ["oc", "get", resource]
    if name:
        cmd.append(name)
    result = run_oc_command(cmd + ["-o", "json", "--request-timeout=10s"], namespace)
    if result.startswith("ERROR:"):
        raise ValueError("Cluster data unavailable")
    data = json.loads(result)
    if not isinstance(data, dict) or (not name and not isinstance(data.get('items'), list)):
        raise ValueError("Invalid cluster response")
    return data if name else data['items']


@app.route('/api/pods')
def api_get_pods():
    namespace = request.args.get('namespace', 'test-ms')
    if namespace not in NAMESPACES:
        return jsonify(error='Namespace буруу.'), 400
    try:
        # Read Deployment generation first so a new rollout cannot inherit an
        # old pod snapshot and appear complete before the controller observes it.
        deployments = oc_json('deployments', namespace)
        replica_sets = oc_json('replicasets', namespace)
        states_available = True
    except (ValueError, KeyError, TypeError):
        deployments, replica_sets, states_available = [], [], False
    try:
        raw_pods = oc_json('pods', namespace)
        states, owners = deployment_states(deployments, replica_sets, raw_pods)
        pods = []
        for pod in raw_pods:
            summary = pod_summary(pod)
            summary.update(states.get(owners[summary['name']], {
                'deployment': None, 'can_restart': False,
                'restart_reason': 'Deployment мэдээлэл боломжгүй байна.' if not states_available
                                  else 'Энэ pod Deployment-д харьяалагдахгүй байна.',
            }))
            pods.append(summary)
        return jsonify(pods)
    except (ValueError, KeyError, TypeError):
        return jsonify(error='Pod list is unavailable'), 502


@app.post('/api/deployments/<namespace>/<deployment_name>/restart')
def api_restart_deployment(namespace, deployment_name):
    if (request.headers.get('X-QA-Request') != 'rollout-restart'
            or request.headers.get('Sec-Fetch-Site') == 'cross-site'):
        return jsonify(error='Хүсэлт зөвшөөрөгдөөгүй.'), 403
    if namespace not in NAMESPACES or not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?', deployment_name):
        return jsonify(error='Namespace эсвэл Deployment нэр буруу.'), 400
    try:
        deployment = oc_json('deployment', namespace, deployment_name)
        replica_sets = oc_json('replicasets', namespace)
        pods = oc_json('pods', namespace)
        states, _ = deployment_states([deployment], replica_sets, pods)
        state = states[deployment['metadata']['uid']]
        if not state['can_restart']:
            return jsonify(error=state['restart_reason']), 409
        annotations = dict(deployment['spec']['template']['metadata'].get('annotations') or {})
        annotations['kubectl.kubernetes.io/restartedAt'] = datetime.now(timezone.utc).isoformat()
        # The same template annotation as rollout restart, with an atomic
        # resourceVersion test to reject concurrent clicks across workers/tabs.
        patch = [
            {'op': 'test', 'path': '/metadata/resourceVersion',
             'value': deployment['metadata']['resourceVersion']},
            {'op': 'add', 'path': '/spec/template/metadata/annotations', 'value': annotations},
        ]
    except (ValueError, KeyError, TypeError):
        return jsonify(error='Deployment төлөв шалгах боломжгүй байна.'), 502
    result = run_oc_command(['oc', 'patch', 'deployment', deployment_name,
                             '--type=json', '-p', json.dumps(patch), '--request-timeout=10s'], namespace)
    if result.startswith('ERROR:'):
        return jsonify(error='Restart амжилтгүй. Deployment төлөв болон patch эрхийг шалгаж дахин шинэчилнэ үү.'), 502
    return jsonify(message=f'{deployment_name}: rollout restart эхэллээ.'), 202


@app.route('/api/pod-stats/<namespace>/<pod_name>')
def api_get_pod_stats(namespace, pod_name):
    # Top stats (Usage)
    top_cmd = ["oc", "adm", "top", "pod", pod_name, "--no-headers"]
    top_result = run_oc_command(top_cmd, namespace)
    
    # Limits stats - бүх Pod-ын тодорхойлолтыг JSON-оор авах
    limit_cmd = ["oc", "get", "pod", pod_name, "-o", "json"]
    limit_result = run_oc_command(limit_cmd, namespace)
    
    cpu_usage, mem_usage = "N/A", "N/A"
    cpu_limit, mem_limit = "N/A", "N/A"
    
    # Parse Usage
    if top_result and "ERROR" not in top_result:
        parts = top_result.split()
        if len(parts) >= 3:
            cpu_usage, mem_usage = parts[1], parts[2]
            
    # Parse Limits - JSON-оор боловсруулах
    if limit_result and "ERROR" not in limit_result:
        try:
            pod_json = json.loads(limit_result)
            # Эхний контейнерын limit-ийг авна (хэрэв олон бол эхнийхийг нь)
            limits = pod_json.get("spec", {}).get("containers", [{}])[0].get("resources", {}).get("limits", {})
            cpu_limit = limits.get("cpu", "N/A")
            mem_limit = limits.get("memory", "N/A")
        except:
            pass
            
    return jsonify({
        "cpu": cpu_usage, "mem": mem_usage,
        "cpu_limit": cpu_limit, "mem_limit": mem_limit
    })

@app.route('/logs/<namespace>/<pod_name>')
def get_logs(namespace, pod_name):
    logs = run_oc_command(["oc", "logs", pod_name, "--tail=500"], namespace)
    return render_template('logs.html', namespace=namespace, pod_name=pod_name,
                           logs=logs, failed=logs.startswith("ERROR:"))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)

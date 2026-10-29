"""Swagger-only application: cluster queries run in the background."""
import os
from flask import Flask, jsonify, render_template, request
from swagger_cache import RouteCache

app = Flask(__name__)
cache = RouteCache()

@app.get('/')
@app.get('/swagger')
def index():
    return render_template('swagger.html')

@app.get('/healthz')
def health():
    return jsonify(status='ok')

@app.get('/api/swagger-routes')
def routes():
    cluster = request.args.get('cluster', 'testocp')
    namespace = request.args.get('namespace', 'test-ms')
    if cluster not in ('testocp', 'testmogul') or namespace not in ('test-ms', 'pre-new-ms'):
        return jsonify(error='Cluster эсвэл namespace буруу.'), 400
    if not os.environ.get('QA_SWAGGER_KUBECONFIG'):
        return jsonify(error='Swagger kubeconfig тохируулаагүй байна.'), 503
    return jsonify(cache.snapshot(cluster, namespace))

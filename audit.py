"""Durable action history; no user identity is inferred from client headers."""
import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import click
import psycopg
from flask import jsonify, request


@contextmanager
def audit_database():
    def setting(key, default=None):
        return os.environ.get('QA_AUDIT_DB_' + key, os.environ.get('QA_PRE_DB_' + key, default))
    if not setting('PASSWORD'):
        raise ValueError('Audit DB тохируулаагүй байна.')
    with psycopg.connect(host=setting('HOST', '192.168.11.59'), port=setting('PORT', '5432'),
                         dbname=setting('NAME', 'app'), user=setting('USER', 'admin_release'),
                         password=setting('PASSWORD'), connect_timeout=3,
                         options='-c statement_timeout=3000 -c lock_timeout=2000',
                         application_name='qa-support-history') as connection:
        yield connection


def save_event(event):
    with audit_database() as connection:
        connection.execute(
            'INSERT INTO qa_support.action_history '
            '(id, created_at, action, namespace, target, outcome, http_status, details) '
            'VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)',
            (event['id'], event['created_at'], event['action'], event['namespace'], event['target'],
             event['outcome'], event['http_status'], json.dumps(event['details'])))


def history_page(namespace, action, page):
    clauses, params = [], []
    for key, value in [('namespace', namespace), ('action', action)]:
        if value:
            clauses.append(key + ' = %s')
            params.append(value)
    where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
    with audit_database() as connection:
        # Fetch one extra row instead of a separate count that could race writes.
        rows = connection.execute(
            'SELECT id, created_at, action, namespace, target, outcome, http_status, details '
            'FROM qa_support.action_history' + where + ' ORDER BY created_at DESC, id DESC LIMIT 51 OFFSET %s',
            (*params, (page - 1) * 50)).fetchall()
    keys = ['id', 'created_at', 'action', 'namespace', 'target', 'outcome', 'http_status', 'details']
    items = []
    for row in rows[:50]:
        item = dict(zip(keys, row))
        item['id'] = str(item['id'])
        item['created_at'] = item['created_at'].isoformat()
        items.append(item)
    return {'items': items, 'page': page, 'has_more': len(rows) > 50}


def register_audit(app, namespaces):
    @app.cli.command('init-audit-db')
    def init_audit_db():
        """Create only the dedicated QA history schema/table (idempotent)."""
        with audit_database() as connection:
            connection.execute(Path(__file__).with_name('sql').joinpath('audit.sql').read_text(encoding='utf-8'))
        click.echo('QA action history initialized.')

    @app.get('/api/history')
    def history():
        namespace = request.args.get('namespace', '')
        action = request.args.get('action', '')
        try:
            page = int(request.args.get('page', '1'))
            if namespace and namespace not in namespaces or action not in ('', 'rollout_restart', 'allowlist', 'smoke') or not 1 <= page <= 10000:
                raise ValueError()
        except ValueError:
            return jsonify(error='Түүхийн шүүлтүүр буруу.'), 400
        try:
            return jsonify(history_page(namespace, action, page))
        except (psycopg.Error, ValueError):
            return jsonify(error='Үйлдлийн түүх боломжгүй. Audit DB холболт, хүснэгт болон эрхийг шалгана уу.'), 503

    @app.after_request
    def record_action(response):
        actions = {'api_restart_deployment': 'rollout_restart',
                   'allowlist.create_entries': 'allowlist', 'qa.smoke_run': 'smoke'}
        action = actions.get(request.endpoint)
        # Rejected unauthenticated/cross-site and malformed requests have no action.
        if request.method != 'POST' or not action or response.status_code in (400, 403, 413):
            return response
        body = response.get_json(silent=True) or {}
        args = request.view_args or {}
        base = {'id': str(uuid4()), 'created_at': datetime.now(timezone.utc).isoformat(),
                'action': action, 'namespace': args.get('namespace', ''),
                'target': args.get('deployment_name', ''),
                'outcome': 'success' if response.status_code < 400 else 'failed',
                'http_status': response.status_code, 'details': {}}
        events = [base]
        if action == 'allowlist' and body.get('results'):
            events = []
            for environment, result in body['results'].items():
                events.append({**base, 'id': str(uuid4()),
                               'namespace': {'test': 'test-ms', 'pre': 'pre-new-ms'}[environment],
                               'target': 'Email / SMS', 'outcome': 'success' if result.get('ok') else 'failed',
                               'details': {'entries': result.get('entries', {})}})
        if action == 'smoke':
            base['outcome'] = 'success' if body.get('ok') else 'failed'
            base['details'] = {'checks': [{'id': r['id'], 'ok': r['ok'], 'status': r.get('status'),
                                          'duration_ms': r.get('duration_ms')} for r in body.get('results', [])]}
        try:
            for event in events:
                save_event(event)
        except (psycopg.Error, ValueError):
            # Do not misreport a completed restart or DB update as failed.
            app.logger.warning('Action history write failed for action=%s', action)
            if response.is_json:
                body['audit_warning'] = 'Үйлдлийн түүх хадгалагдсангүй. Audit DB тохиргоог шалгана уу.'
                response.set_data(app.json.dumps(body))
        return response

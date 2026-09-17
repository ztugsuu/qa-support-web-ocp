import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import psycopg
from app import app
from allowlist import add_entries

HEADERS = {'X-QA-Request': 'allowlist'}


class AllowlistTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def post(self, body, headers=HEADERS):
        return self.client.post('/api/allowlist', json=body, headers=headers)

    def test_pages_and_assets(self):
        for path in ['/', '/allowlist', '/static/allowlist.js', '/static/dashboard.css']:
            with self.client.get(path) as response:
                self.assertEqual(response.status_code, 200)
        self.assertIn('/allowlist', self.client.get('/').text)

    def test_validation_never_opens_database(self):
        invalid = [None, [], {}, {'environments':['prod'], 'phone':'89554255'},
                   {'environments':['test'], 'phone':'123'},
                   {'environments':['test'], 'email':'invalid'},
                   {'environments':['test'], 'phone':89554255},
                   {'environments':['test'], 'phone':'１２３４５６７８'},
                   {'environments':[['test']], 'phone':'89554255'}]
        with patch('allowlist.database') as database:
            for body in invalid:
                self.assertEqual(self.post(body).status_code, 400)
            database.assert_not_called()

    def test_cross_site_requests_rejected(self):
        body = {'environments':['test'], 'phone':'89554255'}
        self.assertEqual(self.post(body, {}).status_code, 403)
        self.assertEqual(self.post(body, {**HEADERS, 'Sec-Fetch-Site':'cross-site'}).status_code, 403)

    def test_partial_success_and_trim(self):
        with patch('allowlist.add_entries', side_effect=[{'ok':True,'entries':{'phone':'added'}}, {'ok':False,'error':'offline'}]) as add:
            response = self.post({'environments':['test','pre'], 'phone':' 89554255 '})
            self.assertTrue(response.json['results']['test']['ok'])
            self.assertFalse(response.json['results']['pre']['ok'])
            self.assertEqual(add.call_args_list[0].args, ('test', {'phone':'89554255'}))

    def test_missing_secret(self):
        with patch.dict('os.environ', {}, clear=True):
            response = self.client.get('/api/allowlist/test/phone')
            self.assertEqual(response.status_code, 503)
            self.assertIn('Secret', response.json['error'])

    def test_bad_list_parameters(self):
        for path in ['/api/allowlist/prod/email','/api/allowlist/test/unknown','/api/allowlist/pre/phone?page=0','/api/allowlist/pre/phone?page=abc']:
            self.assertEqual(self.client.get(path).status_code, 400)

    def mock_database(self, cursor):
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        database = MagicMock()
        database.return_value.__enter__.return_value = connection
        return database

    def test_paginated_search_is_parameterized(self):
        cursor = MagicMock()
        cursor.description = [SimpleNamespace(name='phone_number')]
        cursor.fetchone.return_value = (51,)
        cursor.fetchall.return_value = [('89554255',)]
        with patch('allowlist.database', self.mock_database(cursor)):
            response = self.client.get('/api/allowlist/pre/phone?page=2&q=8955')
        self.assertEqual(response.json['items'], ['89554255'])
        self.assertEqual(response.json['total'], 51)
        self.assertEqual(cursor.execute.call_args.args[1], ('8955',50))

    def test_add_and_existing_use_lock_and_parameters(self):
        cursor = MagicMock()
        cursor.description = [SimpleNamespace(name='value')]
        cursor.fetchone.side_effect = [(1,), None]
        value = "o'connor@m-bank.mn"
        with patch('allowlist.database', self.mock_database(cursor)):
            result = add_entries('test', {'email':value,'phone':'89554255'})
        self.assertEqual(result, {'ok':True,'entries':{'email':'exists','phone':'added'}})
        commands = [(call.args[0].as_string(), call.args[1:] ) for call in cursor.execute.call_args_list]
        self.assertEqual(sum(command.startswith('LOCK TABLE') for command, _ in commands),2)
        inserts = [(command,args) for command,args in commands if command.startswith('INSERT')]
        self.assertEqual(len(inserts),1)
        self.assertEqual(inserts[0][1], (('89554255',),))
        self.assertFalse(any(value in command for command,_ in commands))

    def test_failure_rolls_back_environment_and_hides_database_detail(self):
        cursor = MagicMock()
        cursor.description = [SimpleNamespace(name='value')]
        cursor.fetchone.return_value = None
        events = []
        def execute(query, params=None):
            if 'tb_phone_check_list' in query.as_string() and query.as_string().startswith('INSERT'):
                raise psycopg.OperationalError('private database detail')
        cursor.execute.side_effect = execute
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        @contextmanager
        def transaction(environment):
            try:
                yield connection
                events.append('commit')
            except Exception:
                events.append('rollback')
                raise
        with patch('allowlist.database', transaction):
            result = add_entries('pre', {'email':'test@m-bank.mn','phone':'89554255'})
        self.assertFalse(result['ok'])
        self.assertEqual(events, ['rollback'])
        self.assertNotIn('private database detail', result['error'])
        self.assertNotIn('entries',result)

    def test_unexpected_schema_prevents_insert(self):
        cursor = MagicMock()
        cursor.description = [SimpleNamespace(name='id'),SimpleNamespace(name='phone')]
        with patch('allowlist.database', self.mock_database(cursor)):
            self.assertFalse(add_entries('test',{'phone':'89554255'})['ok'])
        self.assertEqual(cursor.execute.call_count,1)


if __name__ == '__main__':
    unittest.main()

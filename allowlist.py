"""Email/SMS allowlists. Each environment is committed independently."""
import os
import re
from contextlib import contextmanager

import psycopg
from psycopg import sql
from flask import Blueprint, jsonify, render_template, request

allowlist_bp = Blueprint('allowlist', __name__)
ENVIRONMENTS = {'test': '10.90.0.134', 'pre': '192.168.11.59'}
TABLES = {
    'email': ('m_email_gateway_service', 'tb_email_check_list'),
    'phone': ('m_sms_gateway_service', 'tb_phone_check_list'),
}


class ConfigurationError(Exception):
    pass


@contextmanager
def database(environment):
    prefix = 'QA_' + environment.upper() + '_DB_'
    password = os.environ.get(prefix + 'PASSWORD')
    if not password:
        raise ConfigurationError('DB холболтын Secret тохируулаагүй байна.')
    with psycopg.connect(
        host=os.environ.get(prefix + 'HOST', ENVIRONMENTS[environment]),
        port=os.environ.get(prefix + 'PORT', '5432'),
        dbname=os.environ.get(prefix + 'NAME', 'app'),
        user=os.environ.get(prefix + 'USER', 'admin_release'),
        password=password, connect_timeout=5,
        options='-c statement_timeout=5000 -c lock_timeout=3000',
        application_name='qa-support-allowlist',
    ) as connection:
        yield connection


def table_column(cursor, kind):
    table = sql.Identifier(*TABLES[kind])
    cursor.execute(sql.SQL('SELECT * FROM {} LIMIT 0').format(table))
    # The supplied INSERT statements describe a single-column table. Discover
    # its actual name instead of guessing email/phone column names.
    if len(cursor.description) != 1:
        raise ConfigurationError('Хүснэгтийн баганын тохиргоог шалгах шаардлагатай.')
    return table, sql.Identifier(cursor.description[0].name)


def error_message(error):
    if isinstance(error, ConfigurationError):
        return str(error)
    return 'DB үйлдэл амжилтгүй. Холболт болон эрхийг шалгаад дахин оролдоно уу.'


@allowlist_bp.get('/allowlist')
def allowlist_page():
    return render_template('allowlist.html')


@allowlist_bp.get('/api/allowlist/<environment>/<kind>')
def list_entries(environment, kind):
    if environment not in ENVIRONMENTS or kind not in TABLES:
        return jsonify(error='Орчин эсвэл жагсаалтын төрөл буруу.'), 400
    try:
        page = int(request.args.get('page', '1'))
        if page < 1 or page > 1000000:
            raise ValueError()
    except ValueError:
        return jsonify(error='Хуудасны дугаар буруу.'), 400
    query = request.args.get('q', '').strip()
    if len(query) > 254:
        return jsonify(error='Хайх утга хэт урт байна.'), 400
    try:
        with database(environment) as connection, connection.cursor() as cursor:
            table, column = table_column(cursor, kind)
            # strpos treats percent and underscore literally, unlike LIKE.
            condition = sql.SQL('strpos(lower({}::text), lower(%s)) > 0').format(column)
            cursor.execute(sql.SQL('SELECT COUNT(*) FROM {} WHERE {}').format(table, condition), (query,))
            total = cursor.fetchone()[0]
            cursor.execute(sql.SQL('SELECT {} FROM {} WHERE {} ORDER BY {} LIMIT 50 OFFSET %s').format(column, table, condition, column), (query, (page - 1) * 50))
            values = [str(row[0]) for row in cursor.fetchall()]
        return jsonify(items=values, total=total, page=page, page_size=50)
    except (psycopg.Error, ConfigurationError) as error:
        return jsonify(error=error_message(error)), 503


def add_entries(environment, entries):
    results = {}
    try:
        with database(environment) as connection, connection.cursor() as cursor:
            for kind, value in entries.items():
                table, column = table_column(cursor, kind)
                # Serializes check + insert even if the existing table has no
                # unique constraint, including concurrent ordinary INSERTs.
                cursor.execute(sql.SQL('LOCK TABLE {} IN SHARE ROW EXCLUSIVE MODE').format(table))
                cursor.execute(sql.SQL('SELECT 1 FROM {} WHERE {} = %s LIMIT 1').format(table, column), (value,))
                if cursor.fetchone():
                    results[kind] = 'exists'
                else:
                    cursor.execute(sql.SQL('INSERT INTO {} ({}) VALUES (%s)').format(table, column), (value,))
                    results[kind] = 'added'
        return {'ok': True, 'entries': results}
    except (psycopg.Error, ConfigurationError) as error:
        # The connection context rolls back both values in this environment.
        return {'ok': False, 'error': error_message(error)}


@allowlist_bp.post('/api/allowlist')
def create_entries():
    # Require same-site JS/JSON requests; cross-site forms cannot set this
    # header and this app does not enable cross-origin access.
    if request.headers.get('X-QA-Request') != 'allowlist' or request.headers.get('Sec-Fetch-Site') == 'cross-site':
        return jsonify(error='Хүсэлтийг энэ цонхноос илгээнэ үү.'), 403
    if request.content_length and request.content_length > 4096:
        return jsonify(error='Хүсэлт хэт том байна.'), 413
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(error='Хүсэлтийн өгөгдөл буруу.'), 400
    environments = data.get('environments')
    if not isinstance(environments, list) or not environments or len(environments) > 2 or any(not isinstance(e, str) or e not in ENVIRONMENTS for e in environments):
        return jsonify(error='Test эсвэл Pre орчноо сонгоно уу.'), 400
    if any(not isinstance(data.get(key, ''), str) for key in TABLES):
        return jsonify(error='Email болон дугаар текст байх ёстой.'), 400
    email, phone = data.get('email', '').strip(), data.get('phone', '').strip()
    if not email and not phone:
        return jsonify(error='Email эсвэл утасны дугаар оруулна уу.'), 400
    if email and (len(email) > 254 or not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+', email)):
        return jsonify(error='Email хаягийн формат буруу.'), 400
    if phone and not re.fullmatch(r'[0-9]{8}', phone):
        return jsonify(error='Утасны дугаар 8 оронтой байх ёстой.'), 400
    entries = {key: value for key, value in [('email', email), ('phone', phone)] if value}
    results = {environment: add_entries(environment, entries) for environment in dict.fromkeys(environments)}
    return jsonify(results=results)

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg.rows import dict_row


DEFAULT_DB_NAME = os.environ.get('DRONE_DB_NAME', 'drone_app')


def database_dsn() -> str:
    host = os.environ.get('PGHOST', '')
    port = os.environ.get('PGPORT', '5432')
    user = os.environ.get('PGUSER', '')
    password = os.environ.get('PGPASSWORD', '')
    dbname = os.environ.get('PGDATABASE', DEFAULT_DB_NAME)
    parts = [f'dbname={dbname}']
    if host:
        parts.append(f'host={host}')
    if port:
        parts.append(f'port={port}')
    if user:
        parts.append(f'user={user}')
    if password:
        parts.append(f'password={password}')
    return ' '.join(parts)


@contextmanager
def get_connection() -> Iterator[psycopg.Connection]:
    conn = psycopg.connect(database_dsn(), row_factory=dict_row)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

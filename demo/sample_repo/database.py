# demo/sample_repo/database.py
import os
import psycopg2

_conn = None

def get_connection():
    global _conn
    if _conn is None:
        _conn = psycopg2.connect(os.environ["DATABASE_URL"])
    return _conn

def query_one(sql: str, params: tuple = ()):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(sql, params)
    return cur.fetchone()

def execute(sql: str, params: tuple = ()):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(sql, params)
    conn.commit()

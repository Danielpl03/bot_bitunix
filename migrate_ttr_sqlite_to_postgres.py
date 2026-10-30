"""One-time migration of TTR live SQLite tables to Supabase PostgreSQL.

Usage:
  TTR_SQLITE_SOURCE=/path/to/ttr_live.sqlite3 DATABASE_URL='postgresql://...' \
      python migrate_ttr_sqlite_to_postgres.py

The script preserves row IDs and event/trade history. It is safe to re-run:
rows with existing primary keys are skipped (DO NOTHING).
"""
from __future__ import annotations
import os, sqlite3, sys

TABLES = ('ttr_live_config', 'ttr_live_events', 'ttr_live_trades')


def main():
    source=os.environ.get('TTR_SQLITE_SOURCE') or os.environ.get('TTR_LIVE_DB')
    url=os.environ.get('DATABASE_URL') or os.environ.get('SUPABASE_DB_URL')
    if not source or not os.path.isfile(source):
        raise SystemExit('No encuentro el SQLite. Define TTR_SQLITE_SOURCE con la ruta del archivo .sqlite3.')
    if not url:
        raise SystemExit('Define DATABASE_URL o SUPABASE_DB_URL con la conexión PostgreSQL de Supabase.')
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError:
        raise SystemExit('Instala psycopg[binary] antes de ejecutar la migración.')

    src=sqlite3.connect(source); src.row_factory=sqlite3.Row
    dst_url=url + (('&' if '?' in url else '?') + 'sslmode=require' if 'sslmode=' not in url else '')
    dst=psycopg.connect(dst_url, row_factory=dict_row, connect_timeout=20, prepare_threshold=0)
    try:
        # Importing the store initializes the destination schema.
        os.environ['TTR_DB_BACKEND']='postgres'
        os.environ['DATABASE_URL']=url
        from ttr_live import TTRLiveStore
        TTRLiveStore(backend='postgres', database_url=url)
        with dst:
            for table in TABLES:
                rows=src.execute(f'SELECT * FROM {table}').fetchall()
                if not rows:
                    print(f'{table}: 0 filas')
                    continue
                cols=list(rows[0].keys())
                col_sql=','.join(cols)
                placeholders=','.join(['%s']*len(cols))
                sql=f'INSERT INTO {table} ({col_sql}) VALUES ({placeholders}) ON CONFLICT DO NOTHING'
                for row in rows:
                    dst.execute(sql, tuple(row[c] for c in cols))
                print(f'{table}: {len(rows)} filas procesadas')
            # Keep BIGSERIAL sequences ahead of imported IDs.
            for table in ('ttr_live_events','ttr_live_trades'):
                dst.execute(f"SELECT setval(pg_get_serial_sequence('{table}','id'), COALESCE((SELECT MAX(id) FROM {table}),1), true)")
        print('Migración completada. Verifica /ttr_history y /ttr_status antes de retirar SQLite.')
    finally:
        src.close(); dst.close()

if __name__=='__main__':
    main()

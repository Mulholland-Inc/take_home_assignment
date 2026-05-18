import db

with db.connect() as conn, conn.cursor() as cur:
    cur.execute("select 1")
    assert cur.fetchone()[0] == 1

print("postgres is ready")

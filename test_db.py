import sqlite3

def query():
    c = sqlite3.connect('data/catalog.db')
    c.row_factory = sqlite3.Row
    rows = c.execute("SELECT * FROM catalog WHERE tipo='SENTENCIA' LIMIT 5").fetchall()
    print('Rows count in table (total SENTENCIA):', c.execute("SELECT COUNT(*) FROM catalog WHERE tipo='SENTENCIA'").fetchone()[0])
    for r in rows:
        print(dict(r))

if __name__ == '__main__':
    query()

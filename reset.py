import sqlite3
c = sqlite3.connect('data/catalog.db')
c.execute("UPDATE catalog SET scrape_status='pending', resolve_status='pending', suin_id=NULL WHERE tipo='SENTENCIA'")
c.commit()

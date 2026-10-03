import psycopg2

try:
    psycopg2.connect(dbname='kofrim', user='postgres', password='1234', host='localhost', port='5432')
    print('OK')
except Exception as e:
    import traceback
    traceback.print_exc()

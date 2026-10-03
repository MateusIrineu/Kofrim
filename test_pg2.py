import psycopg2
import sys

try:
    psycopg2.connect(dbname='postgres', user='postgres', password='1234', host='localhost', port='5432')
    print('OK_POSTGRES')
except Exception as e:
    import traceback
    traceback.print_exc()

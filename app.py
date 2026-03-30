from flask import Flask, render_template
import pymysql

app = Flask(__name__)

# DB 접속 정보
DB_CONFIG = {
    'host': '172.16.18.11',
    'user': 'root',
    'password': '1234',
    'database': 'ecs_dat1',
    'port': 3306,
    'charset': 'utf8mb4'
}


def get_db_connection():
    return pymysql.connect(**DB_CONFIG, cursorclass=pymysql.cursors.DictCursor)


@app.route('/')
def index():
    error = None
    rows = []
    columns = []

    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT *
                FROM daily_data_count
                ORDER BY date DESC
                LIMIT 5
            """)
            rows = cursor.fetchall()
            if rows:
                columns = list(rows[0].keys())
        conn.close()
    except Exception as e:
        error = str(e)

    return render_template('index.html', rows=rows, columns=columns, error=error)


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)

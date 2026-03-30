from flask import Flask
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

    # 테이블 행 HTML 생성
    if error:
        content = f'<div class="error">DB 오류: {error}</div>'
    elif not rows:
        content = '<p>데이터가 없습니다.</p>'
    else:
        header = ''.join(f'<th>{col}</th>' for col in columns)
        body = ''
        for row in rows:
            cells = ''.join(f'<td>{row[col]}</td>' for col in columns)
            body += f'<tr>{cells}</tr>'
        content = f'''
        <table>
            <thead><tr>{header}</tr></thead>
            <tbody>{body}</tbody>
        </table>
        '''

    html = f'''<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <title>Daily Data Count - 최근 5건</title>
    <style>
        body {{
            font-family: Arial, sans-serif;
            max-width: 900px;
            margin: 40px auto;
            padding: 0 20px;
            background-color: #f5f5f5;
        }}
        h1 {{
            color: #333;
            border-bottom: 2px solid #4CAF50;
            padding-bottom: 10px;
        }}
        .error {{
            background-color: #ffdddd;
            border: 1px solid #f44336;
            color: #f44336;
            padding: 12px;
            border-radius: 4px;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            background-color: white;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        th {{
            background-color: #4CAF50;
            color: white;
            padding: 12px 16px;
            text-align: left;
        }}
        td {{
            padding: 10px 16px;
            border-bottom: 1px solid #ddd;
        }}
        tr:hover {{ background-color: #f1f1f1; }}
        .info {{ color: #666; font-size: 0.9em; margin-bottom: 10px; }}
    </style>
</head>
<body>
    <h1>Daily Data Count</h1>
    <p class="info">테이블: <strong>daily_data_count</strong> &nbsp;|&nbsp; date 기준 최근 5건</p>
    {content}
</body>
</html>'''

    return html


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)

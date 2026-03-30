from flask import Flask, jsonify, request
import pymysql

app = Flask(__name__)

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
    return HTML_PAGE


@app.route('/api/data')
def get_data():
    period = request.args.get('period', '7')  # '7', '30', '90'
    try:
        days = int(period)
    except ValueError:
        days = 7

    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT date,
                       sum_Total, sum_TEM, sum_SP,
                       inc_Total, inc_TEM, inc_SP
                FROM daily_data_count
                ORDER BY date DESC
                LIMIT %s
            """, (days,))
            rows = cursor.fetchall()
        conn.close()

        # 날짜 오름차순으로 정렬 (차트용)
        rows = list(reversed(rows))

        labels = [str(r['date']) for r in rows]
        return jsonify({
            'labels': labels,
            'sum_Total': [r['sum_Total'] for r in rows],
            'sum_TEM':   [r['sum_TEM']   for r in rows],
            'sum_SP':    [r['sum_SP']    for r in rows],
            'inc_Total': [r['inc_Total'] for r in rows],
            'inc_TEM':   [r['inc_TEM']   for r in rows],
            'inc_SP':    [r['inc_SP']    for r in rows],
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


HTML_PAGE = '''<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <title>Daily Data Count 추이</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: Arial, sans-serif;
            background: #f0f2f5;
            padding: 30px 20px;
        }
        h1 {
            text-align: center;
            color: #2c3e50;
            margin-bottom: 24px;
            font-size: 1.6em;
        }
        .period-bar {
            display: flex;
            justify-content: center;
            gap: 10px;
            margin-bottom: 28px;
        }
        .period-bar button {
            padding: 8px 24px;
            border: 2px solid #3498db;
            background: white;
            color: #3498db;
            border-radius: 20px;
            cursor: pointer;
            font-size: 0.95em;
            font-weight: bold;
            transition: all 0.2s;
        }
        .period-bar button.active,
        .period-bar button:hover {
            background: #3498db;
            color: white;
        }
        .charts {
            display: flex;
            flex-direction: column;
            gap: 24px;
            max-width: 1000px;
            margin: 0 auto;
        }
        .card {
            background: white;
            border-radius: 10px;
            padding: 24px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }
        .card h2 {
            color: #34495e;
            margin-bottom: 16px;
            font-size: 1.1em;
            border-left: 4px solid #3498db;
            padding-left: 10px;
        }
        .card h2.green { border-color: #27ae60; }
        .error-msg {
            text-align: center;
            color: #e74c3c;
            background: #fdecea;
            padding: 12px;
            border-radius: 6px;
            margin-bottom: 20px;
            max-width: 1000px;
            margin: 0 auto 20px;
            display: none;
        }
    </style>
</head>
<body>
    <h1>Daily Data Count 일별 추이</h1>

    <div class="period-bar">
        <button onclick="loadData(7)"  id="btn7"  class="active">최근 7일</button>
        <button onclick="loadData(30)" id="btn30">1개월</button>
        <button onclick="loadData(90)" id="btn90">3개월</button>
    </div>

    <div class="error-msg" id="errorMsg"></div>

    <div class="charts">
        <div class="card">
            <h2>Sum 합계 추이 (sum_Total / sum_TEM / sum_SP)</h2>
            <canvas id="sumChart"></canvas>
        </div>
        <div class="card">
            <h2 class="green">Inc 증가량 추이 (inc_Total / inc_TEM / inc_SP)</h2>
            <canvas id="incChart"></canvas>
        </div>
    </div>

    <script>
        let sumChart = null;
        let incChart = null;

        function setActiveButton(days) {
            ['btn7','btn30','btn90'].forEach(id => document.getElementById(id).classList.remove('active'));
            const map = {7:'btn7', 30:'btn30', 90:'btn90'};
            if (map[days]) document.getElementById(map[days]).classList.add('active');
        }

        function buildChart(canvasId, labels, datasets, existing) {
            if (existing) existing.destroy();
            const ctx = document.getElementById(canvasId).getContext('2d');
            return new Chart(ctx, {
                type: 'line',
                data: { labels, datasets },
                options: {
                    responsive: true,
                    interaction: { mode: 'index', intersect: false },
                    plugins: {
                        legend: { position: 'top' },
                        tooltip: { callbacks: {
                            label: ctx => ` ${ctx.dataset.label}: ${ctx.parsed.y.toLocaleString()}`
                        }}
                    },
                    scales: {
                        x: { ticks: { maxTicksLimit: 15, maxRotation: 45 } },
                        y: { beginAtZero: false,
                             ticks: { callback: v => v.toLocaleString() } }
                    }
                }
            });
        }

        async function loadData(days) {
            setActiveButton(days);
            const errEl = document.getElementById('errorMsg');
            errEl.style.display = 'none';

            try {
                const res = await fetch('/api/data?period=' + days);
                const d = await res.json();

                if (d.error) {
                    errEl.textContent = 'DB 오류: ' + d.error;
                    errEl.style.display = 'block';
                    return;
                }

                const labels = d.labels;

                sumChart = buildChart('sumChart', labels, [
                    { label: 'sum_Total', data: d.sum_Total,
                      borderColor: '#3498db', backgroundColor: 'rgba(52,152,219,0.1)',
                      tension: 0.3, fill: false, pointRadius: 3 },
                    { label: 'sum_TEM',   data: d.sum_TEM,
                      borderColor: '#e74c3c', backgroundColor: 'rgba(231,76,60,0.1)',
                      tension: 0.3, fill: false, pointRadius: 3 },
                    { label: 'sum_SP',    data: d.sum_SP,
                      borderColor: '#9b59b6', backgroundColor: 'rgba(155,89,182,0.1)',
                      tension: 0.3, fill: false, pointRadius: 3 }
                ], sumChart);

                incChart = buildChart('incChart', labels, [
                    { label: 'inc_Total', data: d.inc_Total,
                      borderColor: '#27ae60', backgroundColor: 'rgba(39,174,96,0.1)',
                      tension: 0.3, fill: false, pointRadius: 3 },
                    { label: 'inc_TEM',   data: d.inc_TEM,
                      borderColor: '#f39c12', backgroundColor: 'rgba(243,156,18,0.1)',
                      tension: 0.3, fill: false, pointRadius: 3 },
                    { label: 'inc_SP',    data: d.inc_SP,
                      borderColor: '#1abc9c', backgroundColor: 'rgba(26,188,156,0.1)',
                      tension: 0.3, fill: false, pointRadius: 3 }
                ], incChart);

            } catch (err) {
                errEl.textContent = '데이터 로드 실패: ' + err.message;
                errEl.style.display = 'block';
            }
        }

        // 초기 로드
        loadData(7);
    </script>
</body>
</html>'''


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)

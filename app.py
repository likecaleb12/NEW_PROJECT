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
    start = request.args.get('start')
    end   = request.args.get('end')
    period = request.args.get('period')

    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            if start and end:
                cursor.execute("""
                    SELECT date,
                           sum_Total, sum_TEM, sum_SP,
                           inc_Total, inc_TEM, inc_SP
                    FROM daily_data_count
                    WHERE date BETWEEN %s AND %s
                    ORDER BY date ASC
                """, (start, end))
            else:
                days = int(period) if period else 7
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

        if not (start and end):
            rows = list(reversed(rows))

        labels = [str(r['date']) for r in rows]
        return jsonify({
            'labels':    labels,
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

        /* ── 컨트롤 전체 박스 ── */
        .control-box {
            max-width: 1000px;
            margin: 0 auto 28px;
            background: white;
            border-radius: 10px;
            padding: 18px 24px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.08);
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            gap: 12px;
        }
        .control-label {
            font-size: 0.85em;
            color: #888;
            width: 100%;
            margin-bottom: -4px;
        }

        /* 빠른 선택 버튼 */
        .btn-group {
            display: flex;
            gap: 8px;
        }
        .btn-group button {
            padding: 7px 20px;
            border: 2px solid #3498db;
            background: white;
            color: #3498db;
            border-radius: 20px;
            cursor: pointer;
            font-size: 0.9em;
            font-weight: bold;
            transition: all 0.2s;
        }
        .btn-group button.active,
        .btn-group button:hover {
            background: #3498db;
            color: white;
        }

        /* 구분선 */
        .divider {
            width: 1px;
            height: 32px;
            background: #ddd;
            margin: 0 4px;
        }

        /* 날짜 범위 선택 */
        .date-range {
            display: flex;
            align-items: center;
            gap: 8px;
            flex-wrap: wrap;
        }
        .date-range label {
            font-size: 0.9em;
            color: #555;
        }
        .date-range input[type="date"] {
            padding: 6px 10px;
            border: 1px solid #ccc;
            border-radius: 6px;
            font-size: 0.9em;
            color: #333;
            cursor: pointer;
        }
        .date-range input[type="date"]:focus {
            outline: none;
            border-color: #3498db;
            box-shadow: 0 0 0 2px rgba(52,152,219,0.2);
        }
        .btn-apply {
            padding: 7px 18px;
            background: #27ae60;
            color: white;
            border: none;
            border-radius: 6px;
            cursor: pointer;
            font-size: 0.9em;
            font-weight: bold;
            transition: background 0.2s;
        }
        .btn-apply:hover { background: #219150; }

        /* 선택된 기간 표시 */
        .period-info {
            margin-left: auto;
            font-size: 0.85em;
            color: #888;
        }
        .period-info span {
            color: #3498db;
            font-weight: bold;
        }

        /* 차트 영역 */
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
            max-width: 1000px;
            margin: 0 auto 20px;
            display: none;
        }
    </style>
</head>
<body>
    <h1>Daily Data Count 일별 추이</h1>

    <div class="control-box">
        <div class="control-label">기간 선택</div>

        <!-- 빠른 선택 버튼 -->
        <div class="btn-group">
            <button onclick="loadByPeriod(7)"  id="btn7"  class="active">최근 7일</button>
            <button onclick="loadByPeriod(30)" id="btn30">1개월</button>
            <button onclick="loadByPeriod(90)" id="btn90">3개월</button>
        </div>

        <div class="divider"></div>

        <!-- 날짜 직접 선택 -->
        <div class="date-range">
            <label>시작일</label>
            <input type="date" id="startDate">
            <label>~&nbsp;종료일</label>
            <input type="date" id="endDate">
            <button class="btn-apply" onclick="loadByRange()">조회</button>
        </div>

        <div class="period-info">조회 기간: <span id="periodText">최근 7일</span></div>
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

        // 날짜 입력 기본값: 오늘 기준 최근 7일
        (function initDates() {
            const today = new Date();
            const prior = new Date();
            prior.setDate(today.getDate() - 6);
            document.getElementById('endDate').value   = today.toISOString().slice(0,10);
            document.getElementById('startDate').value = prior.toISOString().slice(0,10);
        })();

        function setActiveButton(id) {
            ['btn7','btn30','btn90'].forEach(b => document.getElementById(b).classList.remove('active'));
            if (id) document.getElementById(id).classList.add('active');
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

        async function fetchAndRender(url, periodText, activeBtnId) {
            setActiveButton(activeBtnId);
            document.getElementById('periodText').textContent = periodText;
            const errEl = document.getElementById('errorMsg');
            errEl.style.display = 'none';

            try {
                const res = await fetch(url);
                const d = await res.json();
                if (d.error) {
                    errEl.textContent = 'DB 오류: ' + d.error;
                    errEl.style.display = 'block';
                    return;
                }

                sumChart = buildChart('sumChart', d.labels, [
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

                incChart = buildChart('incChart', d.labels, [
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

        function loadByPeriod(days) {
            const labelMap = { 7: '최근 7일', 30: '최근 1개월', 90: '최근 3개월' };
            const btnMap   = { 7: 'btn7', 30: 'btn30', 90: 'btn90' };
            fetchAndRender('/api/data?period=' + days, labelMap[days], btnMap[days]);
        }

        function loadByRange() {
            const start = document.getElementById('startDate').value;
            const end   = document.getElementById('endDate').value;
            if (!start || !end) {
                alert('시작일과 종료일을 모두 선택해주세요.');
                return;
            }
            if (start > end) {
                alert('시작일이 종료일보다 클 수 없습니다.');
                return;
            }
            fetchAndRender(
                `/api/data?start=${start}&end=${end}`,
                `${start} ~ ${end}`,
                null   // 버튼 활성화 해제
            );
        }

        // 초기 로드
        loadByPeriod(7);
    </script>
</body>
</html>'''


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)

from flask import Flask, request, jsonify
from flask_cors import CORS
import jwt
import datetime
import functools
import uuid
import json
from typing import Dict, Any, List, Optional
from dateutil.relativedelta import relativedelta

app = Flask(__name__)
CORS(app)

# ==================== 配置 ====================
app.config['SECRET_KEY'] = 'your-secret-key-here-change-in-production'
app.config['JWT_EXPIRATION_MINUTES'] = 60

# ==================== 模拟数据存储 ====================
# 模拟吞吐量历史数据（季度数据）
MOCK_THROUGHPUT_DATA = {
    'hong_kong': {
        'seaborne': {
            'inward': [
                {'date': '2020-Q1', 'value': 16800},
                {'date': '2020-Q2', 'value': 17200},
                {'date': '2020-Q3', 'value': 15800},
                {'date': '2020-Q4', 'value': 16500},
                {'date': '2021-Q1', 'value': 17000},
                {'date': '2021-Q2', 'value': 17500},
                {'date': '2021-Q3', 'value': 16200},
                {'date': '2021-Q4', 'value': 16800},
                {'date': '2022-Q1', 'value': 18000},
                {'date': '2022-Q2', 'value': 18500},
                {'date': '2022-Q3', 'value': 17800},
                {'date': '2022-Q4', 'value': 19000},
                {'date': '2023-Q1', 'value': 19500},
                {'date': '2023-Q2', 'value': 20000},
                {'date': '2023-Q3', 'value': 18800},
                {'date': '2023-Q4', 'value': 19200},
                {'date': '2024-Q1', 'value': 20500},
                {'date': '2024-Q2', 'value': 21000},
                {'date': '2024-Q3', 'value': 19800},
                {'date': '2024-Q4', 'value': 21500},
                {'date': '2025-Q1', 'value': 22000},
                {'date': '2025-Q2', 'value': 22500},
                {'date': '2025-Q3', 'value': 21200},
                {'date': '2025-Q4', 'value': 21800},
            ],
            'outward': [
                {'date': '2020-Q1', 'value': 8400},
                {'date': '2020-Q2', 'value': 8600},
                {'date': '2020-Q3', 'value': 7900},
                {'date': '2020-Q4', 'value': 8200},
                {'date': '2021-Q1', 'value': 8500},
                {'date': '2021-Q2', 'value': 8800},
                {'date': '2021-Q3', 'value': 8100},
                {'date': '2021-Q4', 'value': 8400},
                {'date': '2022-Q1', 'value': 9000},
                {'date': '2022-Q2', 'value': 9200},
                {'date': '2022-Q3', 'value': 8900},
                {'date': '2022-Q4', 'value': 9500},
                {'date': '2023-Q1', 'value': 9800},
                {'date': '2023-Q2', 'value': 10000},
                {'date': '2023-Q3', 'value': 9400},
                {'date': '2023-Q4', 'value': 9600},
                {'date': '2024-Q1', 'value': 10200},
                {'date': '2024-Q2', 'value': 10500},
                {'date': '2024-Q3', 'value': 9900},
                {'date': '2024-Q4', 'value': 10800},
                {'date': '2025-Q1', 'value': 11000},
                {'date': '2025-Q2', 'value': 11200},
                {'date': '2025-Q3', 'value': 10600},
                {'date': '2025-Q4', 'value': 10900},
            ]
        },
        'river': {
            'inward': [
                {'date': '2020-Q1', 'value': 3200},
                {'date': '2020-Q2', 'value': 3400},
                {'date': '2020-Q3', 'value': 3100},
                {'date': '2020-Q4', 'value': 3300},
                {'date': '2021-Q1', 'value': 3500},
                {'date': '2021-Q2', 'value': 3600},
                {'date': '2021-Q3', 'value': 3400},
                {'date': '2021-Q4', 'value': 3550},
                {'date': '2022-Q1', 'value': 3700},
                {'date': '2022-Q2', 'value': 3800},
                {'date': '2022-Q3', 'value': 3600},
                {'date': '2022-Q4', 'value': 3900},
                {'date': '2023-Q1', 'value': 4000},
                {'date': '2023-Q2', 'value': 4100},
                {'date': '2023-Q3', 'value': 3850},
                {'date': '2023-Q4', 'value': 3950},
                {'date': '2024-Q1', 'value': 4200},
                {'date': '2024-Q2', 'value': 4300},
                {'date': '2024-Q3', 'value': 4050},
                {'date': '2024-Q4', 'value': 4400},
                {'date': '2025-Q1', 'value': 4500},
                {'date': '2025-Q2', 'value': 4600},
                {'date': '2025-Q3', 'value': 4350},
                {'date': '2025-Q4', 'value': 4450},
            ],
            'outward': [
                {'date': '2020-Q1', 'value': 2800},
                {'date': '2020-Q2', 'value': 2900},
                {'date': '2020-Q3', 'value': 2600},
                {'date': '2020-Q4', 'value': 2750},
                {'date': '2021-Q1', 'value': 2850},
                {'date': '2021-Q2', 'value': 2950},
                {'date': '2021-Q3', 'value': 2700},
                {'date': '2021-Q4', 'value': 2800},
                {'date': '2022-Q1', 'value': 3000},
                {'date': '2022-Q2', 'value': 3100},
                {'date': '2022-Q3', 'value': 2900},
                {'date': '2022-Q4', 'value': 3200},
                {'date': '2023-Q1', 'value': 3300},
                {'date': '2023-Q2', 'value': 3400},
                {'date': '2023-Q3', 'value': 3150},
                {'date': '2023-Q4', 'value': 3250},
                {'date': '2024-Q1', 'value': 3500},
                {'date': '2024-Q2', 'value': 3600},
                {'date': '2024-Q3', 'value': 3400},
                {'date': '2024-Q4', 'value': 3700},
                {'date': '2025-Q1', 'value': 3800},
                {'date': '2025-Q2', 'value': 3900},
                {'date': '2025-Q3', 'value': 3650},
                {'date': '2025-Q4', 'value': 3750},
            ]
        }
    }
}

# 模拟贸易数据（季度数据）
MOCK_TRADE_DATA = {
    'hong_kong_us': [
        {'date': '2020-Q1', 'value': 10000},
        {'date': '2020-Q2', 'value': 10200},
        {'date': '2020-Q3', 'value': 9800},
        {'date': '2020-Q4', 'value': 10101},
        {'date': '2021-Q1', 'value': 10500},
        {'date': '2021-Q2', 'value': 10800},
        {'date': '2021-Q3', 'value': 10300},
        {'date': '2021-Q4', 'value': 11000},
        {'date': '2022-Q1', 'value': 11500},
        {'date': '2022-Q2', 'value': 12000},
        {'date': '2022-Q3', 'value': 11200},
        {'date': '2022-Q4', 'value': 11800},
        {'date': '2023-Q1', 'value': 12200},
        {'date': '2023-Q2', 'value': 12500},
        {'date': '2023-Q3', 'value': 11900},
        {'date': '2023-Q4', 'value': 12300},
        {'date': '2024-Q1', 'value': 13000},
        {'date': '2024-Q2', 'value': 13500},
        {'date': '2024-Q3', 'value': 12800},
        {'date': '2024-Q4', 'value': 14000},
        {'date': '2025-Q1', 'value': 14200},
        {'date': '2025-Q2', 'value': 14500},
        {'date': '2025-Q3', 'value': 13800},
        {'date': '2025-Q4', 'value': 14300},
    ]
}

# 模拟文本数据（季度数据）
MOCK_TEXT_DATA = [
    {
        'date': '2025-Q1',
        'texts_in_period': [
            '第一季度香港港口吞吐量环比增长2%',
            '中美贸易额在Q1达到新高'
        ]
    },
    {
        'date': '2025-Q2',
        'texts_in_period': [
            '第二季度全球航运需求回升，香港港口繁忙',
            '美国消费季带动亚洲出口增长'
        ]
    },
    {
        'date': '2025-Q3',
        'texts_in_period': [
            '第三季度受台风影响，港口吞吐量小幅下降',
            '全球供应链紧张局势缓解'
        ]
    },
    {
        'date': '2025-Q4',
        'texts_in_period': [
            '第四季度圣诞节前夕香港港口货运量回升',
            '全年贸易总额同比增长5.2%'
        ]
    }
]

# ==================== 认证装饰器 ====================
def token_required(f):
    """JWT Token认证装饰器"""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization')
        
        if not auth_header:
            return jsonify({
                'status': 'error',
                'error': {
                    'code': 401,
                    'message': 'Missing authorization header'
                }
            }), 401
        
        try:
            parts = auth_header.split()
            if len(parts) != 2 or parts[0].lower() != 'bearer':
                return jsonify({
                    'status': 'error',
                    'error': {
                        'code': 401,
                        'message': 'Invalid authorization header format. Use Bearer <token>'
                    }
                }), 401
            
            token = parts[1]
            payload = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
            request.user_id = payload.get('sub')
            
        except jwt.ExpiredSignatureError:
            return jsonify({
                'status': 'error',
                'error': {
                    'code': 401,
                    'message': 'Token expired'
                }
            }), 401
        except jwt.InvalidTokenError:
            return jsonify({
                'status': 'error',
                'error': {
                    'code': 401,
                    'message': 'Invalid token'
                }
            }), 401
        
        return f(*args, **kwargs)
    return decorated

# ==================== 辅助函数 ====================
def generate_request_id() -> str:
    """生成请求ID"""
    return f"req_{datetime.datetime.now().strftime('%Y%m%d')}_{uuid.uuid4().hex[:6]}"

def calculate_change(values: List[Dict]) -> tuple:
    """
    计算环比变化（季度环比）
    返回: (变化值, 变化百分比)
    """
    if len(values) < 2:
        return 0, 0
    
    current = values[-1]['value'] if values else 0
    previous = values[-2]['value'] if len(values) >= 2 else 0
    
    if previous == 0:
        return current, 0
    
    change = current - previous
    change_pct = (change / previous) * 100
    
    return round(change, 2), round(change_pct, 2)

def get_next_quarter(current_quarter: str, quarters_ahead: int = 1) -> str:
    """
    获取当前季度之后第N个季度的字符串
    例如: '2025-Q4' + 1 = '2026-Q1'
    """
    year = int(current_quarter[:4])
    quarter = int(current_quarter[-1])
    
    # 计算新的季度
    total_quarters = year * 4 + (quarter - 1) + quarters_ahead
    new_year = total_quarters // 4
    new_quarter = (total_quarters % 4) + 1
    
    return f"{new_year}-Q{new_quarter}"

def validate_throughput_request(data: Dict) -> Optional[Dict]:
    """验证吞吐量API请求参数"""
    required_fields = ['period', 'port', 'direction', 'type']
    
    for field in required_fields:
        if field not in data:
            return {
                'code': 400,
                'message': f'Missing required field: {field}'
            }
    
    # 验证方向
    if data['direction'] not in ['inward', 'outward']:
        return {
            'code': 400,
            'message': 'direction must be "inward" or "outward"'
        }
    
    # 验证类型
    if data['type'] not in ['seaborne', 'river']:
        return {
            'code': 400,
            'message': 'type must be "seaborne" or "river"'
        }
    
    # 验证季度格式 (YYYY-Q1, YYYY-Q2, YYYY-Q3, YYYY-Q4)
    import re
    pattern = r'^\d{4}-Q[1-4]$'
    if not re.match(pattern, data['period']):
        return {
            'code': 400,
            'message': 'period must be in YYYY-Q1/YYYY-Q2/YYYY-Q3/YYYY-Q4 format (e.g., 2026-Q1)'
        }
    
    return None

# ==================== API 端点 ====================

@app.route('/api/auth/login', methods=['POST'])
def login():
    """获取JWT Token（用于测试）"""
    data = request.get_json()
    
    if not data or not data.get('username'):
        return jsonify({
            'status': 'error',
            'error': {
                'code': 400,
                'message': 'Username required'
            }
        }), 400
    
    token = jwt.encode({
        'sub': data['username'],
        'iat': datetime.datetime.utcnow(),
        'exp': datetime.datetime.utcnow() + datetime.timedelta(minutes=app.config['JWT_EXPIRATION_MINUTES'])
    }, app.config['SECRET_KEY'], algorithm='HS256')
    
    return jsonify({
        'status': 'ok',
        'token': token,
        'expires_in': app.config['JWT_EXPIRATION_MINUTES'] * 60
    })

@app.route('/analytics/predict/throughput', methods=['POST'])
@token_required
def predict_throughput():
    """
    3.1.1 港口货物吞吐量预测API（季度预测）
    预测未来一个季度（3个月）的吞吐量
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({
                'status': 'error',
                'error': {
                    'code': 400,
                    'message': 'Request body must be JSON'
                }
            }), 400
        
        # 验证请求参数
        validation_error = validate_throughput_request(data)
        if validation_error:
            return jsonify({
                'status': 'error',
                'error': validation_error
            }), 400
        
        period = data['period']  # 例如: '2026-Q1'
        port = data['port']
        direction = data['direction']
        transport_type = data['type']
        
        # 获取历史数据
        historical_data = MOCK_THROUGHPUT_DATA.get(port, {}).get(transport_type, {}).get(direction, [])
        
        if not historical_data:
            return jsonify({
                'status': 'error',
                'error': {
                    'code': 404,
                    'message': f'No data found for port={port}, type={transport_type}, direction={direction}'
                }
            }), 404
        
        # 获取最近一个季度的数据
        last_quarter_data = historical_data[-1] if historical_data else None
        last_value = last_quarter_data['value'] if last_quarter_data else 0
        last_quarter = last_quarter_data['date'] if last_quarter_data else period
        
        # 生成未来1个季度的预测
        import random
        change_pct = random.uniform(-0.05, 0.05)  # -5% 到 +5%
        predicted_value = last_value * (1 + change_pct)
        
        predicted_quarter = get_next_quarter(last_quarter, 1)
        
        predicted_data = {
            'date': predicted_quarter,
            'value': round(predicted_value, 2),
            'change_vs_prev_period': None,
            'change_vs_prev_period_pct': None
        }
        
        # 计算环比变化（整体）
        change_value, change_pct = calculate_change(historical_data)
        
        # 构建时间序列（历史最近一个季度 + 预测值）
        timeseries = []
        
        # 添加历史最近一个季度数据（带环比）
        if last_quarter_data:
            last_historical = last_quarter_data.copy()
            # 计算这个历史点的环比
            if len(historical_data) >= 2:
                prev_value = historical_data[-2]['value']
                last_historical['change_vs_prev_period'] = round(last_historical['value'] - prev_value, 2)
                last_historical['change_vs_prev_period_pct'] = round(
                    ((last_historical['value'] - prev_value) / prev_value) * 100, 2
                ) if prev_value != 0 else 0
            else:
                last_historical['change_vs_prev_period'] = 0
                last_historical['change_vs_prev_period_pct'] = 0
            timeseries.append(last_historical)
        
        # 添加预测值，计算环比
        if timeseries:
            prev_value = timeseries[-1]['value']
            predicted_data['change_vs_prev_period'] = round(predicted_data['value'] - prev_value, 2)
            predicted_data['change_vs_prev_period_pct'] = round(
                ((predicted_data['value'] - prev_value) / prev_value) * 100, 2
            ) if prev_value != 0 else 0
        
        timeseries.append(predicted_data)
        
        response = {
            'status': 'ok',
            'request_id': generate_request_id(),
            'data': {
                'unit': 'tonnes',
                'period': 'quarter',  # 预测频率为季度
                'change_vs_prev_period': change_value,
                'change_vs_prev_period_pct': change_pct,
                'timeseries': timeseries
            },
            'meta': {
                'generated_at': datetime.datetime.utcnow().isoformat() + 'Z'
            }
        }
        
        return jsonify(response), 200
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'error': {
                'code': 500,
                'message': f'Internal server error: {str(e)}'
            }
        }), 500

@app.route('/analytics/input/throughput', methods=['POST'])
@token_required
def get_throughput_input():
    """
    3.1.2 获取吞吐量预测输入数据API（季度数据）
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({
                'status': 'error',
                'error': {
                    'code': 400,
                    'message': 'Request body must be JSON'
                }
            }), 400
        
        # 验证请求参数
        validation_error = validate_throughput_request(data)
        if validation_error:
            return jsonify({
                'status': 'error',
                'error': validation_error
            }), 400
        
        period = data['period']
        port = data['port']
        direction = data['direction']
        transport_type = data['type']
        
        # 获取历史吞吐量数据
        throughput_data = MOCK_THROUGHPUT_DATA.get(port, {}).get(transport_type, {}).get(direction, [])
        
        # 构建时序输入数据
        ts_data = []
        
        # 1. 历史吞吐量（季度）
        ts_data.append({
            'series_name': 'throughput',
            'series_unit': 'tonnes',
            'period': 'quarter',
            'timeseries': throughput_data
        })
        
        # 2. 香港与美国贸易额（季度）
        ts_data.append({
            'series_name': 'trade_total_hongkong_us',
            'series_unit': 'million us dollar',
            'period': 'quarter',
            'timeseries': MOCK_TRADE_DATA['hong_kong_us']
        })
        
        # 3. 全球航运指数（季度）
        ts_data.append({
            'series_name': 'global_shipping_index',
            'series_unit': 'index',
            'period': 'quarter',
            'timeseries': [
                {'date': '2024-Q1', 'value': 125.3},
                {'date': '2024-Q2', 'value': 127.8},
                {'date': '2024-Q3', 'value': 124.1},
                {'date': '2024-Q4', 'value': 126.5},
                {'date': '2025-Q1', 'value': 128.2},
                {'date': '2025-Q2', 'value': 130.5},
                {'date': '2025-Q3', 'value': 127.8},
                {'date': '2025-Q4', 'value': 129.3},
            ]
        })
        
        # 构建文本数据（季度）
        text_data = {
            'period': 'quarter',
            'texts': MOCK_TEXT_DATA
        }
        
        response = {
            'status': 'ok',
            'request_id': generate_request_id(),
            'ts_data': ts_data,
            'text_data': text_data,
            'meta': {
                'generated_at': datetime.datetime.utcnow().isoformat() + 'Z'
            }
        }
        
        return jsonify(response), 200
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'error': {
                'code': 500,
                'message': f'Internal server error: {str(e)}'
            }
        }), 500

# ==================== 健康检查 ====================
@app.route('/health', methods=['GET'])
def health_check():
    """健康检查端点"""
    return jsonify({
        'status': 'ok',
        'timestamp': datetime.datetime.utcnow().isoformat() + 'Z'
    })

# ==================== 错误处理 ====================
@app.errorhandler(404)
def not_found(error):
    return jsonify({
        'status': 'error',
        'error': {
            'code': 404,
            'message': 'Endpoint not found'
        }
    }), 404

@app.errorhandler(405)
def method_not_allowed(error):
    return jsonify({
        'status': 'error',
        'error': {
            'code': 405,
            'message': 'Method not allowed'
        }
    }), 405

# ==================== 启动应用 ====================
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
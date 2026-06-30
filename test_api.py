import requests
import json

BASE_URL = 'http://localhost:5000'

def test_login():
    """测试登录获取token"""
    response = requests.post(
        f'{BASE_URL}/api/auth/login',
        json={'username': 'test_user'}
    )
    print('Login Response:', response.json())
    return response.json().get('token')

def test_predict_throughput(token):
    """测试吞吐量预测API（季度）"""
    headers = {'Authorization': f'Bearer {token}'}
    data = {
        'period': '2026-Q1',  # 季度格式
        'port': 'hong_kong',
        'direction': 'outward',
        'type': 'seaborne'
    }
    response = requests.post(
        f'{BASE_URL}/analytics/predict/throughput',
        headers=headers,
        json=data
    )
    print('\n' + '='*50)
    print('Predict Throughput Response (Quarterly):')
    print('='*50)
    print(json.dumps(response.json(), indent=2, ensure_ascii=False))

def test_get_throughput_input(token):
    """测试获取吞吐量输入数据API（季度）"""
    headers = {'Authorization': f'Bearer {token}'}
    data = {
        'period': '2026-Q1',
        'port': 'hong_kong',
        'direction': 'outward',
        'type': 'seaborne'
    }
    response = requests.post(
        f'{BASE_URL}/analytics/input/throughput',
        headers=headers,
        json=data
    )
    print('\n' + '='*50)
    print('Get Throughput Input Response (Quarterly):')
    print('='*50)
    print(json.dumps(response.json(), indent=2, ensure_ascii=False))

if __name__ == '__main__':
    print("测试季度版API")
    print("="*60)
    
    # 1. 获取token
    token = test_login()
    
    if token:
        # 2. 测试预测API（季度）
        test_predict_throughput(token)
        
        # 3. 测试输入数据API（季度）
        test_get_throughput_input(token)
    
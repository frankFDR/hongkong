# Hong Kong Port Throughput Prediction API

## 项目简介

本项目实现了香港港口货物吞吐量季度预测API，基于Flask框架提供RESTful服务。系统支持季度级别的吞吐量预测，包含历史数据查询和预测结果返回。

### 功能特性

- ✅ 季度预测：支持未来1个季度的吞吐量预测
- ✅ 历史数据：包含2020-Q1至2025-Q4共24个季度的历史数据
- ✅ 多维度查询：支持海运/河运、进出港方向
- ✅ JWT认证：安全的API访问控制
- ✅ 环比计算：自动计算季度环比变化
- ✅ 输入数据：提供预测所用的时序数据和文本数据

### 对应需求

- 3.1.1 `/analytics/predict/throughput` - 吞吐量预测API
- 3.1.2 `/analytics/input/throughput` - 吞吐量输入数据API
- 3.5 JWT认证机制

---

## 快速开始

### 环境要求

- Python 3.8+
- pip

### 安装依赖

```bash
pip install -r requirements.txt
```

### 启动服务

```bash
python app.py
```

服务启动后，默认运行在 `http://localhost:5000`

### 测试API

另开一个终端运行测试脚本：

```bash
python test_api.py
```

---

## API文档

### 认证

所有API（除登录外）都需要JWT Token认证。

#### 获取Token

请求：
```http
POST /api/auth/login
Content-Type: application/json

{
    "username": "test_user"
}
```

响应：
```json
{
    "status": "ok",
    "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
    "expires_in": 3600
}
```

使用Token：
```
Authorization: Bearer <your_token>
```

---

### 1. 吞吐量预测 API

#### `POST /analytics/predict/throughput`

预测未来1个季度的港口货物吞吐量。

请求参数：

| 参数 | 类型 | 必填 | 说明 | 可选值 |
|------|------|------|------|--------|
| period | string | ✅ | 预测目标季度 | YYYY-Q1 ~ YYYY-Q4 |
| port | string | ✅ | 港口名称 | hong_kong |
| direction | string | ✅ | 进出方向 | inward / outward |
| type | string | ✅ | 运输类型 | seaborne / river |

请求示例：
```json
{
    "period": "2026-Q1",
    "port": "hong_kong",
    "direction": "outward",
    "type": "seaborne"
}
```

响应示例：
```json
{
    "status": "ok",
    "request_id": "req_20260623_4f8681",
    "data": {
        "unit": "tonnes",
        "period": "quarter",
        "change_vs_prev_period": 300,
        "change_vs_prev_period_pct": 2.83,
        "timeseries": [
            {
                "date": "2025-Q4",
                "value": 10900,
                "change_vs_prev_period": 300,
                "change_vs_prev_period_pct": 2.83
            },
            {
                "date": "2026-Q1",
                "value": 10851.38,
                "change_vs_prev_period": -48.62,
                "change_vs_prev_period_pct": -0.45
            }
        ]
    },
    "meta": {
        "generated_at": "2026-06-23T13:20:36.376131Z"
    }
}
```

错误响应：
```json
{
    "status": "error",
    "error": {
        "code": 400,
        "message": "period must be in YYYY-Q1/YYYY-Q2/YYYY-Q3/YYYY-Q4 format (e.g., 2026-Q1)"
    }
}
```

---

### 2. 获取输入数据 API

#### `POST /analytics/input/throughput`

获取预测所用的输入数据（时序数据 + 文本数据）。

请求参数： 同预测API

请求示例：
```json
{
    "period": "2026-Q1",
    "port": "hong_kong",
    "direction": "outward",
    "type": "seaborne"
}
```

响应示例：
```json
{
    "status": "ok",
    "request_id": "req_20260623_6c3518",
    "ts_data": [
        {
            "series_name": "throughput",
            "series_unit": "tonnes",
            "period": "quarter",
            "timeseries": [
                {"date": "2020-Q1", "value": 8400},
                {"date": "2020-Q2", "value": 8600},
                {"date": "2025-Q4", "value": 10900}
            ]
        },
        {
            "series_name": "trade_total_hongkong_us",
            "series_unit": "million us dollar",
            "period": "quarter",
            "timeseries": [
                {"date": "2020-Q1", "value": 10000},
                {"date": "2020-Q2", "value": 10200},
                {"date": "2025-Q4", "value": 14300}
            ]
        },
        {
            "series_name": "global_shipping_index",
            "series_unit": "index",
            "period": "quarter",
            "timeseries": [
                {"date": "2024-Q1", "value": 125.3},
                {"date": "2024-Q2", "value": 127.8},
                {"date": "2025-Q4", "value": 129.3}
            ]
        }
    ],
    "text_data": {
        "period": "quarter",
        "texts": [
            {
                "date": "2025-Q1",
                "texts_in_period": [
                    "第一季度香港港口吞吐量环比增长2%",
                    "中美贸易额在Q1达到新高"
                ]
            },
            {
                "date": "2025-Q2",
                "texts_in_period": [
                    "第二季度全球航运需求回升，香港港口繁忙",
                    "美国消费季带动亚洲出口增长"
                ]
            },
            {
                "date": "2025-Q3",
                "texts_in_period": [
                    "第三季度受台风影响，港口吞吐量小幅下降",
                    "全球供应链紧张局势缓解"
                ]
            },
            {
                "date": "2025-Q4",
                "texts_in_period": [
                    "第四季度圣诞节前夕香港港口货运量回升",
                    "全年贸易总额同比增长5.2%"
                ]
            }
        ]
    },
    "meta": {
        "generated_at": "2026-06-23T13:20:38.407062Z"
    }
}
```

---

### 3. 健康检查

#### `GET /health`

检查服务状态。

响应：
```json
{
    "status": "ok",
    "timestamp": "2026-06-23T13:20:38.407062Z"
}
```

---

## 配置说明

### 修改端口

编辑 `app.py` 文件最后一行：

```python
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=True)  # 改为 8080
```

### 修改JWT密钥

编辑 `app.py` 中的配置：

```python
app.config['SECRET_KEY'] = 'your-secret-key-here-change-in-production'
```

生产环境务必修改此密钥！

### 修改JWT过期时间

```python
app.config['JWT_EXPIRATION_MINUTES'] = 60  # 单位：分钟
```

---

## 部署指南

### 1. 开发环境部署

```bash
# 进入项目目录
cd hongkong

# 安装依赖
pip install -r requirements.txt

# 启动服务
python app.py
```

### 2. 生产环境部署（使用Gunicorn）

```bash
# 安装Gunicorn
pip install gunicorn

# 启动服务（4个worker进程）
gunicorn -w 4 -b 0.0.0.0:5000 app:app

# 指定端口
gunicorn -w 4 -b 0.0.0.0:8080 app:app
```

### 3. Docker部署

Dockerfile：
```dockerfile
FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5000

CMD ["python", "app.py"]
```

构建和运行：
```bash
# 构建镜像
docker build -t throughput-api .

# 运行容器（映射到8080端口）
docker run -d -p 8080:5000 throughput-api
```

### 4. 华为云部署（ARM架构）

```bash
# 在华为云服务器上
# 安装依赖（ARM架构适配）
pip install -r requirements.txt

# 使用Gunicorn启动
gunicorn -w 4 -b 0.0.0.0:5000 app:app
```

---

## 项目结构

```
hongkong/
├── app.py              # 主服务程序
├── test_api.py         # API测试脚本
├── requirements.txt    # Python依赖
├── README.md          # 项目文档
└── Dockerfile         # Docker构建文件（可选）
```

---

## 常见问题

### Q1: 端口被占用怎么办？

A: 修改 `app.py` 中的端口号：
```python
app.run(host='0.0.0.0', port=8080, debug=True)
```

### Q2: Token过期怎么办？

A: 重新调用 `/api/auth/login` 获取新Token。

### Q3: 如何添加更多历史数据？

A: 修改 `app.py` 中的 `MOCK_THROUGHPUT_DATA` 字典，按季度格式添加数据。

### Q4: 如何集成真实ML模型？

A: 在 `predict_throughput()` 函数中，替换预测逻辑部分：
```python
# 当前是随机预测
change_pct = random.uniform(-0.05, 0.05)

# 替换为你的模型调用
# predicted_value = your_model.predict(features)
```

---

## 错误码说明

| HTTP状态码 | 说明 |
|-----------|------|
| 200 | 请求成功 |
| 400 | 请求参数错误 |
| 401 | 未授权（Token无效或过期） |
| 404 | 资源未找到 |
| 405 | 方法不允许 |
| 500 | 服务器内部错误 |

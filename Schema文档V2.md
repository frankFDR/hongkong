# Schema文档V2

### 命名规范



- 数据库名、表名、字段名统一使用英文。

- 命名格式建议采用小写英文加下划线，例如 `throughput_forecast`、`river_export_pred`。

- 不在数据库对象名中使用中文、空格、特殊符号或括号。

    

### 时间字段规范

- 与业务时间相关的字段统一使用 `DATETIME` 类型。

- 季度时间需要转换为该季度首日`00:00:00`的 `DATETIME`。

- 月度时间需要转换为该月首日`00:00:00`的 `DATETIME`。

- 日度或新闻发布时间可直接使用实际日期时间对应的 `DATETIME`。

---

## 元数据表：`meta_table_info`



### 建表语句



```SQL
CREATE TABLE meta_table_info (
    id                  INT AUTO_INCREMENT PRIMARY KEY,
    table_name          VARCHAR(255) NOT NULL UNIQUE,
    original_table_name VARCHAR(255),
    source_csv          TEXT,
    columns_json        TEXT,
    column_mapping_json TEXT,
    unit                VARCHAR(255) DEFAULT '',
    frequency           VARCHAR(50),
    start_time          DATETIME,
    end_time            DATETIME,
    row_count           INT,
    import_time         DATETIME
);
```



### 字段说明



|字段名|类型|含义|
|---|---|---|
|`id`|`INT`|自增主键，唯一标识一条元数据记录。|
|`table_name`|`VARCHAR(255)`|实际落库的业务表名称。|
|`original_table_name`|`VARCHAR(255)`|原始表名。|
|`source_csv`|`TEXT`|原始数据文件路径或文件标识。|
|`columns_json`|`TEXT`|字段清单或字段元信息，建议使用 JSON 字符串存储。|
|`column_mapping_json`|`TEXT`|原始字段名到实际英文字段名的映射关系，建议使用 JSON 字符串存储。|
|`unit`|`VARCHAR(255)`|数据集量纲或单位。|
|`frequency`|`VARCHAR(50)`|数据频率，例如 `yearly`、`quarterly`、`monthly`、`daily`。|
|`start_time`|`DATETIME`|数据集起始时间。|
|`end_time`|`DATETIME`|数据集结束时间。|
|`row_count`|`INT`|数据总行数。|
|`import_time`|`DATETIME`|导入时间。|



---



## 吞吐量数据表：`sea_river_import_export_throughput`



### 建表语句



```SQL
CREATE TABLE sea_river_import_export_throughput (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    river_export  DOUBLE,
    river_import  DOUBLE,
    sea_export    DOUBLE,
    sea_import    DOUBLE,
    timestamp     DATETIME NOT NULL
);
```



### 字段说明



|字段名|类型|含义|
|---|---|---|
|`id`|`INT`|自增主键，唯一标识一条记录。|
|`river_export`|`DOUBLE`|河运出口吞吐量。|
|`river_import`|`DOUBLE`|河运进口吞吐量。|
|`sea_export`|`DOUBLE`|海运出口吞吐量。|
|`sea_import`|`DOUBLE`|海运进口吞吐量。|
|`timestamp`|`DATETIME`|标准化后的业务时间。季度数据入库时转换为该季度首日的 `DATETIME`。季度输入如 `2026-Q1`，入库后应为 `2026-01-01 00:00:00`。|



---



## 吞吐量预测结果表：`throughput_forecast`



### 建表语句



```SQL
CREATE TABLE throughput_forecast (
    id                  INT AUTO_INCREMENT PRIMARY KEY,
    timestamp           DATETIME NOT NULL,
    prediction_time     DATETIME NOT NULL,
    horizon_step        INT NOT NULL,
    river_export_pred   DOUBLE,
    river_import_pred   DOUBLE,
    sea_export_pred     DOUBLE,
    sea_import_pred     DOUBLE,
    forecast_method     VARCHAR(50)
);
```



### 字段说明



|字段名|类型|含义|
|---|---|---|
|`id`|`INT`|自增主键，唯一标识一条预测结果记录。|
|`timestamp`|`DATETIME`|被预测目标季度对应的标准化时间。季度输入如 `2026-Q1`，入库后应为 `2026-01-01 00:00:00`。|
|`prediction_time`|`DATETIME`|预测任务执行时间。|
|`horizon_step`|`INT`|预测步长。例如基于历史窗口预测未来四个季度时，目标季度对应第几个预测步。|
|`river_export_pred`|`DOUBLE`|河运出口预测值。|
|`river_import_pred`|`DOUBLE`|河运进口预测值。|
|`sea_export_pred`|`DOUBLE`|海运出口预测值。|
|`sea_import_pred`|`DOUBLE`|海运进口预测值。|
|forecast\_method|VARCHAR\(50\)|预测方法，可以是temporal或者multimodal|



---



## 纺织物贸易预测结果表：`textile_trade_forecast`

### 建表语句

```SQL
CREATE TABLE textile_trade_forecast (
    id                      INT AUTO_INCREMENT PRIMARY KEY,
    timestamp               DATETIME NOT NULL,
    prediction_time         DATETIME NOT NULL,
    horizon_step            INT NOT NULL,
    mainland_export_pred    DOUBLE,
    mainland_import_pred    DOUBLE,
    usa_export_pred         DOUBLE,
    usa_import_pred         DOUBLE,
    vietnam_export_pred     DOUBLE,
    vietnam_import_pred     DOUBLE,
    hongkong_export_pred    DOUBLE,
    hongkong_import_pred    DOUBLE,
    forecast_method         VARCHAR(50)
);
```

### 字段说明

---





## 新闻文本数据表：`news_text`



### 建表语句



```SQL
CREATE TABLE news_text (
    id                      INT AUTO_INCREMENT PRIMARY KEY,
    timestamp               DATETIME NOT NULL,
    news_text               TEXT,
    news_title              TEXT,
    source                  VARCHAR(255),
    throughput_relevance    DOUBLE,
    textile_relevance       DOUBLE,
    throughput_summary      TEXT,
    textile_summary         TEXT,
    throughput_score        JSON,
    textile_score           JSON
);
```

|字段名|类型|含义|
|---|---|---|
|`id`|`INT`|自增主键，唯一标识一条文本记录。|
|`timestamp`|`DATETIME`|新闻或事件的发布时间。|
|`news_text`|`TEXT`|新闻或事件文本内容。|
|`news_title`|`TEXT`|新闻或事件标题。|
|`source`|`VARCHAR(255)`|新闻来源或数据来源。|
|`throughput_relevance`|`DOUBLE`|模型置信度分数，取值范围为 0\\\~1，分值越高代表模型判断该记录与港口货物吞吐量关联性越强。|
|`textile_relevance`|`DOUBLE`|模型置信度分数，取值范围为 0\\\~1，分值越高代表模型判断该记录与纺织物贸易关联性越强。|
|`throughput_summary`|`TEXT`|该新闻在港口吞吐量方面的摘要。|
|`textile_summary`|`TEXT`|该新闻在纺织物贸易方面的摘要。|
|`throughput_score`|`JSON`|未来港口货物吞吐量影响和置信度预测评分|
|`textile_score`|`JSON`|未来纺织物贸易影响和置信度预测评分|

`throughput_score`案例

```JSON
{
  "sea_import": {
    "confidence": 0.0,
    "scores": [0, 0, 0, 0, 0, 0, 0, 0]
  },
  "sea_export": {
    "confidence": 0.0,
    "scores": [0, 0, 0, 0, 0, 0, 0, 0]
  },
  "river_import": {
    "confidence": 0.0,
    "scores": [0, 0, 0, 0, 0, 0, 0, 0]
  },
  "river_export": {
    "confidence": 0.0,
    "scores": [0, 0, 0, 0, 0, 0, 0, 0]
  }
}
```



|字段路径|类型|取值范围|含义|
|---|---|---|---|
|`{sea/river}_{import/export}.confidence`|FLOAT|0\.0 \~ 1\.0|该新闻对对应运输方式及进出口方向未来8个季度影响预测的可靠程度|
|`{sea/river}_{import/export}.scores`|ARRAY\[8\]|\-1\.0/\-0\.5/0/0\.5/1\.0|未来8个季度逐季度影响预测，scores\[0\] 为未来第1季度，scores\[7\] 为未来第8季度|





`textile_score`案例

```JSON
{
  "mainland_export": {"confidence": 0.0, "scores": [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1]},
  "mainland_import": {"confidence": 0.0, "scores": [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1]},
  "usa_export": {"confidence": 0.0, "scores": [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1]},
  "usa_import": {"confidence": 0.0, "scores": [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1]},
  "vietnam_export": {"confidence": 0.0, "scores": [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1]},
  "vietnam_import": {"confidence": 0.0, "scores": [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1]},
  "hongkong_export": {"confidence": 0.0, "scores": [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1]},
  "hongkong_import": {"confidence": 0.0, "scores": [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1]}
}
```



|字段路径|类型|取值范围|含义|
|---|---|---|---|
|`{mainland/usa/vietnam/hongkong}_{import/export}.confidence`|FLOAT|0\.0 \~ 1\.0|该新闻对对应国家或地区及进出口方向未来24个月影响预测的可靠程度|
|`{mainland/usa/vietnam/hongkong}_{import/export}.scores`|ARRAY\[24\]|1 或 \-1|未来24个月逐月影响预测，scores\[0\] 为未来第1个月，scores\[23\] 为未来第24个月|

---



## 纺织物月度进出口数据表：

`vietnam_textile_trade_monthly`

`mainland_textile_trade_monthly`

`hongkong_textile_trade_monthly`

`usa_textile_trade_monthly`

### 建表语句

```SQL
-- 越南大陆纺织物月度进出口数据表
CREATE TABLE vietnam_textile_trade_monthly (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    timestamp       DATETIME NOT NULL,
    flow            VARCHAR(20),
    trade_value     DOUBLE,
);

-- 中国大陆纺织物月度进出口数据表
CREATE TABLE mainland_textile_trade_monthly (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    timestamp       DATETIME NOT NULL,
    flow            VARCHAR(20),
    trade_value     DOUBLE
);

-- 中国香港纺织物月度进出口数据表
CREATE TABLE hongkong_textile_trade_monthly (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    timestamp       DATETIME NOT NULL,
    flow            VARCHAR(20),
    trade_value     DOUBLE
);

-- 美国纺织物月度进出口数据表
CREATE TABLE usa_textile_trade_monthly (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    timestamp       DATETIME NOT NULL,
    flow            VARCHAR(20),
    trade_value     DOUBLE
);
```

### 字段说明

|字段名|类型|含义|
|---|---|---|
|`id`|`INT`|自增主键，唯一标识一条记录。|
|`timestamp`|`DATETIME `|统一业务时间字段|
|`flow`|`VARCHAR(20)`|贸易方向，例如 `export` 或 `import`。|
|`trade_value`|`DOUBLE`|贸易额。|




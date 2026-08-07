# hongkong 待更新代码清单

## 1. 更新 `hongkong/database`

从 `/data5/zhimo/port/database/` 同步：

- `database_utils.py`
- `import_csv.py`
- `example.py`
- `news_text_utils.py`
- `migrate_news_text_schema.py`
- `auto_news_pipeline.py`
- `quarter_throughput_text_index.py`
- `news_crawl_pipeline/__init__.py`
- `news_crawl_pipeline/__main__.py`
- `news_crawl_pipeline/crawlers.py`
- `news_crawl_pipeline/workflow.py`

`hongkong/database` 中以上文件不再使用版本号后缀。

## 2. 新增新闻初筛和打分代码

从 `/data5/zhimo/port/Gdelt/` 同步：

- `news_filter_0622.py`
- `score_top_news_impacts.py`
- `textile/news_filter.py`
- `textile/score_news.py`

## 3. 新增打分基线文件

- `/data5/zhimo/results/historical_diff_quantiles/historical_diff_quantiles.json`
- `/data5/zhimo/port/Gdelt/textile/转移中位数_原始环比_pct.csv`

## 4. 文本指数代码（已迁移）

当前位置：`hongkong/text_index/`

- `textile_text_index.py`
- `throughput_text_index.py`
- `config.json`
- `test_textile_text_index.py`
- `README.md`
- `data/text_index/realtext_actual.csv`

## 5. `hongkong/websearch` 中需保留的最终版

这些文件已在 `hongkong` 中，无需从外部同步：

- `update_all.py`
- `fetch_mainland_china.py`
- `fetch_mainland_china_english.py`
- `fetch_usa.py`
- `fetch_hongkong.py`
- `fetch_hongkong_port.py`
- `fetch_vietnam.py`
- `regional_proxy.py`
- `import_to_db.py`
- `validate_crawl_db.py`

`fetch_vietnam_old.py` 是旧版，打包时不需要。

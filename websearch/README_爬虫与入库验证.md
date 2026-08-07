# 爬虫与入库使用说明

进入项目目录：

```bash
cd /data5/zhimo/port/hongkong/websearch
```

## 1. 自动爬虫并入库

运行：

```bash
/data2/zhimo/miniconda3/envs/BasicTS/bin/python update_all.py --refresh data
```

程序会自动：

1. 并行增量抓取中国大陆、美国、香港、越南贸易及香港港口数据；
2. 更新 `data/` 下的 CSV；
3. 将成功抓取的数据写入 MySQL；
4. 更新数据库中的 `meta_table_info`。

如只更新 CSV、不入库：

```bash
/data2/zhimo/miniconda3/envs/BasicTS/bin/python update_all.py --refresh data --skip-db
```

## 2. 检验爬虫和入库是否有效

运行：

```bash
/data2/zhimo/miniconda3/envs/BasicTS/bin/python \
  validate_crawl_db.py --source hongkong --month 2026-06
```

程序会自动：

1. 删除指定月份的 CSV 数据和数据库记录；
2. 只重新执行指定来源的爬虫和入库；
3. 检查该月份是否重新出现在 CSV 和数据库中；
4. 失败时自动恢复删除前的数据。

支持的数据源：

```text
hongkong  香港
mainland  中国大陆
usa       美国
vietnam   越南
```

`--month` 使用 `YYYY-MM` 格式。不指定月份时，默认检验该来源的最新月份：

```bash
/data2/zhimo/miniconda3/envs/BasicTS/bin/python \
  validate_crawl_db.py --source hongkong
```

出现以下输出即表示检验成功：

```text
[3/3] 验证成功
```

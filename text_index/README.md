# 文本指数计算

- `textile_text_index.py`：输入月份，从 `news_text.textile_score` 计算纺织物八项月度文本指数。
- `throughput_text_index.py`：输入季度，从 `news_text.throughput_score` 计算海运和河运季度文本指数。

运行环境：

```bash
cd /data5/zhimo/port/hongkong/text_index

/data2/zhimo/miniconda3/envs/BasicTS/bin/python \
  textile_text_index.py --month 2025-12

/data2/zhimo/miniconda3/envs/BasicTS/bin/python \
  throughput_text_index.py 2025Q2
```

纺织物指数全区间校验：

```bash
/data2/zhimo/miniconda3/envs/BasicTS/bin/python \
  textile_text_index.py --validate
```

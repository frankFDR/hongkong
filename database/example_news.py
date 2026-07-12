# example_news_text_utils.py
# 演示 news_text_utils.py 的功能：
#   1. 导入新闻 JSON -> 生成文本表 timestamp/news_text/source
#   2. 按年读取新闻 read_news_by_year
#   3. 按月读取新闻 read_news_by_month
#   4. 按季度读取新闻 read_news_by_quarter
#   5. 按任意时间范围读取新闻 read_news_period
#   6. 只返回文本列表 text_only=True

import os
import sys
from pprint import pprint


# 确保能导入同级目录下的模块
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from news_text_utils import (
    load_news_json_to_db,
    read_news_by_month,
    read_news_by_quarter,
    read_news_by_year,
    read_news_period,
)


# 示例配置
TABLE_NAME = "news_text"
JSON_PATH = "/data5/zhimo/port/Gdelt/globalnews2015-2026/news_gov_hk.json"


def setup_data():
    """导入新闻 JSON 作为演示准备"""
    print(f"=== 0. 准备数据: 导入新闻 JSON ({JSON_PATH}) ===")
    if not os.path.exists(JSON_PATH):
        print(f"❌ 文件不存在: {JSON_PATH}")
        return False

    success = load_news_json_to_db(JSON_PATH, table_name=TABLE_NAME, if_exists="replace")
    if not success:
        print("❌ 新闻数据导入失败")
    return success


def print_news_preview(df, max_rows=3, max_chars=160):
    """打印新闻查询结果预览，避免正文太长刷屏"""
    print(f"查询到 {len(df)} 条新闻")
    for i, row in df.head(max_rows).iterrows():
        text_preview = str(row["news_text"]).replace("\n", " ")[:max_chars]
        print(f"\n--- 新闻 {i + 1} ---")
        print(f"时间: {row['timestamp']}")
        if "source" in row:
            print(f"来源: {row['source']}")
        print(f"文本预览: {text_preview}...")


def demo_read_news_by_year():
    """演示按年读取新闻"""
    print("\n=== 1. 按年读取：read_news_by_year ===")
    year = 2024
    print(f"查询: 年份={year}")
    df = read_news_by_year(year, table_name=TABLE_NAME)
    print_news_preview(df)


def demo_read_news_by_month():
    """演示按月读取新闻"""
    print("\n=== 2. 按月读取：read_news_by_month ===")
    year = 2024
    month = 7
    print(f"查询: 年月={year}-{month:02d}")
    df = read_news_by_month(year, month, table_name=TABLE_NAME)
    print_news_preview(df)


def demo_read_news_by_quarter():
    """演示按季度读取新闻"""
    print("\n=== 3. 按季度读取：read_news_by_quarter ===")
    year = 2024
    quarter = "Q3"
    print(f"查询: 季度={year}{quarter}")
    df = read_news_by_quarter(year, quarter, table_name=TABLE_NAME)
    print_news_preview(df)


def demo_read_news_period():
    """演示按任意时间范围读取新闻"""
    print("\n=== 4. 范围读取：read_news_period ===")
    start_ts = "2024-07-01"
    end_ts = "2024-07-31 23:59:59"
    print(f"查询: 时间范围={start_ts} ~ {end_ts}")
    df = read_news_period(start_ts, end_ts, table_name=TABLE_NAME, limit=5)
    print_news_preview(df, max_rows=5)


def demo_text_only():
    """演示只返回新闻文本列表"""
    print("\n=== 5. 只返回文本列表：text_only=True ===")
    texts = read_news_by_quarter(2024, "Q3", table_name=TABLE_NAME, limit=3, text_only=True)
    print(f"返回类型: {type(texts)}")
    print(f"文本数量: {len(texts)}")
    print("--- 前3条文本预览 ---")
    pprint([text.replace("\n", " ")[:120] + "..." for text in texts])

# 在 news_text_utils.py 末尾添加以下函数

def load_news_csv_to_db(csv_file, table_name="news_text", if_exists="replace", 
                        date_col="date", text_col="news_text", 
                        title_col="title", content_col="content",
                        source_col="source"):
    """
    导入 CSV 新闻数据到 MySQL 数据库
    
    支持两种 CSV 格式：
    1. date, news_text（两列格式）
    2. date, title, content（三列格式，自动拼接）
    
    参数:
        csv_file: CSV 文件路径
        table_name: 数据库表名，默认 news_text
        if_exists: 'replace'（删除旧表重建）或 'append'（追加）
        date_col: 日期列名，默认 'date'
        text_col: 文本列名，默认 'news_text'
        title_col: 标题列名，默认 'title'
        content_col: 内容列名，默认 'content'
    
    返回:
        bool: 导入是否成功
    """
    import pandas as pd
    from sqlalchemy import create_engine, text
    
    try:
        print(f"正在读取 CSV 文件: {csv_file}")
        df = pd.read_csv(csv_file)
        
        # 检查列名
        cols = df.columns.tolist()
        print(f"CSV 列名: {cols}")
        
        # 处理不同格式
        if title_col in cols and content_col in cols:
            # 三列格式：date, title, content
            print(f"检测到三列格式 (date, {title_col}, {content_col})，正在拼接...")
            df["news_text"] = df[title_col].fillna("") + "\n\n" + df[content_col].fillna("")
            selected_columns = [date_col, "news_text"]
        elif text_col in cols:
            # 两列格式：date, news_text
            print(f"检测到两列格式 (date, {text_col})")
            selected_columns = [date_col, text_col]
        else:
            raise ValueError(f"CSV 必须包含 '{text_col}' 或 '{title_col}'+'{content_col}' 列")

        if source_col in cols:
            selected_columns.append(source_col)
        df = df[selected_columns]
        if source_col in df.columns and source_col != "source":
            df = df.rename(columns={source_col: "source"})
        
        # 重命名日期列
        df = df.rename(columns={date_col: "timestamp"})
        
        # 转换日期格式
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        
        # 删除空文本
        df = df[df["news_text"].notna() & (df["news_text"].str.strip() != "")]
        
        if len(df) == 0:
            print("⚠️ 没有有效的新闻数据")
            return False
        
        print(f"有效新闻数量: {len(df)}")
        
        # 获取数据库连接
        from news_text_utils import engine
        
        # 写入数据库
        df.to_sql(
            name=table_name,
            con=engine,
            if_exists=if_exists,
            index=False,
            chunksize=1000
        )
        
        print(f"✅ 成功导入 {len(df)} 条新闻到表 {table_name}")
        return True
        
    except Exception as e:
        print(f"❌ CSV 导入失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def load_news_csv_to_db_with_config(csv_file, db_config, table_name="news_text", 
                                   if_exists="replace", **kwargs):
    """
    使用自定义数据库配置导入 CSV
    
    参数:
        csv_file: CSV 文件路径
        db_config: 数据库配置字典，包含 host, user, password, database, port
        table_name: 数据库表名
        if_exists: 'replace' 或 'append'
        **kwargs: 传递给 load_news_csv_to_db 的其他参数
    
    返回:
        bool: 导入是否成功
    """
    import pandas as pd
    from sqlalchemy import create_engine
    
    try:
        # 创建数据库连接
        db_url = f"mysql+pymysql://{db_config['user']}:{db_config['password']}@{db_config['host']}:{db_config.get('port', 3306)}/{db_config['database']}?charset=utf8mb4"
        engine = create_engine(db_url)
        
        print(f"正在读取 CSV 文件: {csv_file}")
        df = pd.read_csv(csv_file)
        
        # 处理不同格式（复用上面的逻辑）
        cols = df.columns.tolist()
        print(f"CSV 列名: {cols}")
        
        date_col = kwargs.get('date_col', 'date')
        text_col = kwargs.get('text_col', 'news_text')
        title_col = kwargs.get('title_col', 'title')
        content_col = kwargs.get('content_col', 'content')
        source_col = kwargs.get('source_col', 'source')
        
        if title_col in cols and content_col in cols:
            print(f"检测到三列格式 (date, {title_col}, {content_col})，正在拼接...")
            df["news_text"] = df[title_col].fillna("") + "\n\n" + df[content_col].fillna("")
            selected_columns = [date_col, "news_text"]
        elif text_col in cols:
            print(f"检测到两列格式 (date, {text_col})")
            selected_columns = [date_col, text_col]
        else:
            raise ValueError(f"CSV 必须包含 '{text_col}' 或 '{title_col}'+'{content_col}' 列")

        if source_col in cols:
            selected_columns.append(source_col)
        df = df[selected_columns]
        if source_col in df.columns and source_col != "source":
            df = df.rename(columns={source_col: "source"})
        
        df = df.rename(columns={date_col: "timestamp"})
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df[df["news_text"].notna() & (df["news_text"].str.strip() != "")]
        
        if len(df) == 0:
            print("⚠️ 没有有效的新闻数据")
            return False
        
        print(f"有效新闻数量: {len(df)}")
        
        df.to_sql(
            name=table_name,
            con=engine,
            if_exists=if_exists,
            index=False,
            chunksize=1000
        )
        
        print(f"✅ 成功导入 {len(df)} 条新闻到表 {table_name}")
        return True
        
    except Exception as e:
        print(f"❌ CSV 导入失败: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    if setup_data():
        demo_read_news_by_year()
        demo_read_news_by_month()
        demo_read_news_by_quarter()
        demo_read_news_period()
        demo_text_only()
    else:
        print("无法进行后续演示，因为新闻数据导入失败。")

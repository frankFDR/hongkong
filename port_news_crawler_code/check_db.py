import sys
sys.path.append('C:/Users/Thinkpad/Desktop/hongkong')
from database.database_utils import engine
from sqlalchemy import text

with engine.connect() as conn:
    result = conn.execute(text('SELECT COUNT(*) FROM news_articles'))
    print(f'新闻文章总数: {result.scalar()}')

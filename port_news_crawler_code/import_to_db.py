import os
import json
import sys
sys.path.append('C:/Users/Thinkpad/Desktop/hongkong')

from database.database_utils import save_news_text

def import_articles():
    articles_dir = "data/articles/hk_marine_dept"
    total = 0
    
    for root, dirs, files in os.walk(articles_dir):
        for file in files:
            if file.endswith('.json'):
                path = os.path.join(root, file)
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                # 转换为数据库格式
                article = {
                    'timestamp': data.get('published') or data.get('fetched_at'),
                    'news_text': data.get('text') or data.get('content', ''),
                    'source': data.get('site', 'hk_marine_dept'),
                }
                
                saved = save_news_text([article])
                if saved:
                    total += 1
                    print(f"✅ 已导入: {data.get('title', '')[:50]}...")
    
    print(f"\n总共导入 {total} 篇文章")

if __name__ == '__main__':
    import_articles()

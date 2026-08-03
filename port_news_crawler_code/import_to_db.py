import os
import json
import sys
sys.path.append('C:/Users/Thinkpad/Desktop/hongkong')

from database.database_utils_v4 import save_news_articles

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
                    'url': data.get('url'),
                    'site': data.get('site', 'hk_marine_dept'),
                    'title': data.get('title', ''),
                    'text': data.get('text', ''),
                    'published': data.get('published'),
                    'author': data.get('author'),
                    'language': data.get('language', 'en'),
                    'fetched_at': data.get('fetched_at')
                }
                
                saved = save_news_articles([article])
                if saved:
                    total += saved
                    print(f"✅ 已导入: {data.get('title', '')[:50]}...")
    
    print(f"\n总共导入 {total} 篇文章")

if __name__ == '__main__':
    import_articles()
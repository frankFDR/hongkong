"""
CNN RSS 爬虫
用法：python cnn_rss.py --max 10
"""

import os
import json
import re
import argparse
from datetime import datetime

import requests
import feedparser

def ensure_dir(path):
    os.makedirs(path, exist_ok=True)

def safe_filename(title, max_len=50):
    safe = re.sub(r'[\\/*?:"<>|]', "", title)
    return safe[:max_len] if safe else "untitled"

def fetch_rss():
    url = "http://rss.cnn.com/rss/cnn_latest.rss"
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        return feedparser.parse(resp.text)
    except Exception as e:
        print(f"❌ 获取 RSS 失败: {e}")
        return None

def extract_articles(feed, max_articles=20):
    articles = []
    for entry in feed.entries[:max_articles]:
        published = entry.get('published', '')
        if published:
            try:
                from dateutil import parser
                dt = parser.parse(published)
                date_str = dt.strftime('%Y-%m-%d')
            except:
                date_str = datetime.now().strftime('%Y-%m-%d')
        else:
            date_str = datetime.now().strftime('%Y-%m-%d')
        
        content = entry.get('summary', '') or entry.get('description', '')
        if 'content' in entry:
            content = entry.content[0].value
        
        articles.append({
            'date': date_str,
            'title': entry.get('title', ''),
            'content': content,
            'source': 'cnn_rss',
            'url': entry.get('link', ''),
            'published': published
        })
    
    return articles

def save_article(article, output_dir):
    date_str = article.get('date') or datetime.now().strftime('%Y-%m-%d')
    save_dir = os.path.join(output_dir, date_str)
    ensure_dir(save_dir)
    
    title = article.get('title', 'untitled')
    safe_name = safe_filename(title)
    timestamp = datetime.now().strftime('%H%M%S')
    filename = f"{safe_name}_{timestamp}.json"
    filepath = os.path.join(save_dir, filename)
    
    counter = 1
    while os.path.exists(filepath):
        filename = f"{safe_name}_{timestamp}_{counter}.json"
        filepath = os.path.join(save_dir, filename)
        counter += 1
    
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(article, f, ensure_ascii=False, indent=2)
    
    return filepath

def crawl_cnn(max_articles=20, output_dir="data/articles/cnn_rss"):
    print("=" * 60)
    print("📰 CNN RSS 爬虫启动")
    print(f"   最大抓取: {max_articles} 篇")
    print("=" * 60)
    
    feed = fetch_rss()
    if not feed:
        return
    
    articles = extract_articles(feed, max_articles)
    print(f"\n🔗 发现 {len(articles)} 篇文章")
    
    success = 0
    for i, article in enumerate(articles, 1):
        filepath = save_article(article, output_dir)
        print(f"   ✅ [{i}/{len(articles)}] 已保存: {article.get('title', '')[:50]}...")
        success += 1
    
    print("\n" + "=" * 60)
    print(f"📊 成功保存 {success} 篇 CNN 文章")
    print(f"   保存目录: {output_dir}")
    print("=" * 60)

def main():
    parser = argparse.ArgumentParser(description="CNN RSS 爬虫")
    parser.add_argument("--max", type=int, default=20, help="最大抓取篇数（默认 20）")
    parser.add_argument("--output", type=str, default="data/articles/cnn_rss", help="输出目录")
    args = parser.parse_args()
    
    crawl_cnn(args.max, args.output)

if __name__ == "__main__":
    main()
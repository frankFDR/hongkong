#!/usr/bin/env python3
"""
大公报（takungpao）纯 requests 爬虫
基于学长验证：SSR 服务端渲染，直接解析 HTML 获取正文

用法：
    python takungpao_requests.py --once      # 爬取一次
    python takungpao_requests.py --max 20    # 指定抓取数量
"""

import os
import sys
import json
import time
import random
import argparse
import re
from datetime import datetime
from typing import List, Dict, Optional

import requests
from bs4 import BeautifulSoup


# ==================== 配置 ====================
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

INDEX_URL = "https://www.takungpao.com/"
OUTPUT_DIR = "data/articles/takungpao_requests"


# ==================== 工具函数 ====================
def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


def get_date_path() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def safe_filename(title: str, max_len: int = 50) -> str:
    safe = re.sub(r'[\\/*?:"<>|]', "", title)
    return safe[:max_len] if safe else "untitled"


def random_delay(min_sec: float = 1.0, max_sec: float = 3.0):
    time.sleep(random.uniform(min_sec, max_sec))


# ==================== 核心爬取函数 ====================
def fetch_index_html() -> Optional[str]:
    try:
        resp = requests.get(INDEX_URL, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        resp.encoding = "utf-8"
        return resp.text
    except Exception as e:
        print(f"❌ 获取首页失败: {e}")
        return None


def extract_article_links(html: str) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    article_urls = set()
    
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "epaper.tkww.hk/a/" in href and href.endswith(".html"):
            if href.startswith("/"):
                href = "https://www.takungpao.com" + href
            elif not href.startswith("http"):
                href = "https://www.takungpao.com/" + href.lstrip("/")
            article_urls.add(href)
    
    return list(article_urls)


def fetch_article_html(html_url: str) -> Optional[Dict]:
    """直接获取文章 HTML 并解析正文"""
    try:
        resp = requests.get(html_url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")
        
        # 提取标题
        title = ""
        title_tag = soup.find("title")
        if title_tag:
            title = title_tag.get_text(strip=True)
        
        # 提取正文：优先找文章内容容器
        content = ""
        
        # 尝试多种可能的正文容器
        content_candidates = [
            soup.find("div", class_=re.compile(r"content|article|text|body|detail")),
            soup.find("article"),
            soup.find("section", class_=re.compile(r"content|article")),
            soup.find("div", {"id": re.compile(r"content|article|detail")}),
        ]
        
        for container in content_candidates:
            if container:
                # 提取文本，保留段落结构
                paragraphs = container.find_all("p")
                if paragraphs:
                    content = "\n\n".join(p.get_text(strip=True) for p in paragraphs)
                else:
                    content = container.get_text(strip=True)
                if len(content) > 50:  # 确保提取到有效内容
                    break
        
        # 如果上面的方法都没提取到，尝试提取所有 p 标签
        if not content or len(content) < 20:
            paragraphs = soup.find_all("p")
            for p in paragraphs:
                text = p.get_text(strip=True)
                if len(text) > 30:
                    content += text + "\n\n"
            content = content.strip()
        
        # 提取发布时间
        published = None
        time_candidates = [
            soup.find("time"),
            soup.find("span", class_=re.compile(r"time|date|publish")),
            soup.find("div", class_=re.compile(r"time|date|publish")),
        ]
        for t in time_candidates:
            if t:
                published = t.get_text(strip=True)
                # 尝试提取 datetime 属性
                if t.get("datetime"):
                    published = t.get("datetime")
                break
        
        if not content or len(content) < 50:
            return None
        
        return {
            "title": title,
            "content": content,
            "published": published,
        }
        
    except Exception as e:
        print(f"❌ 获取 HTML 失败 {html_url}: {e}")
        return None


def format_article_data(data: Dict, source_url: str) -> Dict:
    # 从 URL 提取日期
    date_match = re.search(r"/a/(\d{4})(\d{2})(\d{2})/", source_url)
    if date_match:
        date_str = f"{date_match.group(1)}-{date_match.group(2)}-{date_match.group(3)}"
    else:
        date_str = get_date_path()
    
    return {
        "date": date_str,
        "title": data.get("title", ""),
        "content": data.get("content", ""),
        "source": "takungpao",
        "url": source_url,
        "published": data.get("published", ""),
    }


def save_article(article: Dict, output_dir: str = OUTPUT_DIR):
    date_str = article.get("date") or get_date_path()
    save_dir = os.path.join(output_dir, date_str)
    ensure_dir(save_dir)
    
    title = article.get("title", "untitled")
    safe_name = safe_filename(title)
    timestamp = datetime.now().strftime("%H%M%S")
    filename = f"{safe_name}_{timestamp}.json"
    filepath = os.path.join(save_dir, filename)
    
    counter = 1
    while os.path.exists(filepath):
        filename = f"{safe_name}_{timestamp}_{counter}.json"
        filepath = os.path.join(save_dir, filename)
        counter += 1
    
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(article, f, ensure_ascii=False, indent=2)
    
    return filepath


# ==================== 主流程 ====================
def crawl_takungpao(max_articles: int = 50, delay_min: float = 1.0, delay_max: float = 3.0, output_dir: str = OUTPUT_DIR):
    print("=" * 60)
    print("📰 大公报（takungpao）爬虫启动")
    print(f"   首页: {INDEX_URL}")
    print(f"   最大抓取: {max_articles} 篇")
    print("=" * 60)
    
    html = fetch_index_html()
    if not html:
        return
    
    article_urls = extract_article_links(html)
    print(f"\n🔗 发现 {len(article_urls)} 篇文章链接")
    
    if not article_urls:
        print("⚠️ 未发现文章链接")
        return
    
    if max_articles > 0 and len(article_urls) > max_articles:
        article_urls = article_urls[:max_articles]
        print(f"   限制抓取 {max_articles} 篇")
    
    print("\n📥 开始爬取文章...")
    success_count = 0
    fail_count = 0
    empty_count = 0
    
    for i, url in enumerate(article_urls, 1):
        print(f"\n  [{i}/{len(article_urls)}] {url[:80]}...")
        
        data = fetch_article_html(url)
        if not data:
            fail_count += 1
            random_delay(delay_min, delay_max)
            continue
        
        if not data.get("content") or len(data.get("content", "")) < 50:
            print(f"   ⚠️ 正文为空或太短，跳过")
            empty_count += 1
            random_delay(delay_min, delay_max)
            continue
        
        article = format_article_data(data, url)
        filepath = save_article(article, output_dir)
        print(f"   ✅ 已保存: {os.path.basename(filepath)}")
        print(f"      标题: {article.get('title', '')[:50]}...")
        print(f"      日期: {article.get('date')}")
        print(f"      内容长度: {len(article.get('content', ''))} 字符")
        success_count += 1
        
        random_delay(delay_min, delay_max)
    
    print("\n" + "=" * 60)
    print("📊 爬取完成统计")
    print(f"   成功: {success_count} 篇")
    print(f"   空内容: {empty_count} 篇")
    print(f"   失败: {fail_count} 篇")
    print(f"   总计: {len(article_urls)} 篇")
    print(f"   保存目录: {output_dir}")
    print("=" * 60)


# ==================== 命令行入口 ====================
def main():
    parser = argparse.ArgumentParser(description="大公报 takungpao 爬虫")
    parser.add_argument("--once", action="store_true", default=True, help="运行一次（默认）")
    parser.add_argument("--max", type=int, default=50, help="最大抓取篇数（默认 50）")
    parser.add_argument("--delay-min", type=float, default=1.0, help="最小延迟秒数（默认 1.0）")
    parser.add_argument("--delay-max", type=float, default=3.0, help="最大延迟秒数（默认 3.0）")
    parser.add_argument("--output", type=str, default=OUTPUT_DIR, help="输出目录")
    
    args = parser.parse_args()
    
    crawl_takungpao(
        max_articles=args.max,
        delay_min=args.delay_min,
        delay_max=args.delay_max,
        output_dir=args.output,
    )


if __name__ == "__main__":
    main()
"""
TikTok scraper — поиск видео по хэштегу.

Использует неофициальный подход через веб-страницы TikTok.
⚠️ TikTok может блокировать запросы — если перестанет работать,
    возможно понадобится обновить заголовки или использовать прокси.
"""

import aiohttp
import asyncio
import json
import re
import logging

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.tiktok.com/",
}


async def check_hashtag_exists(hashtag: str) -> bool:
    """
    Проверяет, существует ли хэштег в TikTok.
    Отправляет GET-запрос на страницу хэштега и проверяет ответ.
    """
    url = f"https://www.tiktok.com/tag/{hashtag}"
    try:
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    # Если страница содержит данные о хэштеге — он существует
                    if f"tag/{hashtag}" in text.lower() or "challengeName" in text:
                        return True
                    # Даже если 200, но страница пустая — ок, считаем что есть
                    return True
                elif resp.status == 404:
                    return False
                else:
                    logger.warning(f"TikTok вернул статус {resp.status} для #{hashtag}")
                    # На всякий случай считаем что существует
                    return True
    except Exception as e:
        logger.error(f"Ошибка при проверке хэштега #{hashtag}: {e}")
        # При ошибке сети даём возможность добавить
        return True


async def search_tiktok_by_hashtag(hashtag: str, min_likes: int = 0) -> list:
    """
    Ищет видео по хэштегу в TikTok.
    Возвращает список словарей с информацией о видео.

    Каждый элемент:
    {
        "id": str,
        "url": str,
        "likes": int,
        "comments": int,
        "shares": int,
        "description": str,
        "author": str
    }
    """
    url = f"https://www.tiktok.com/tag/{hashtag}"
    videos = []

    try:
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                if resp.status != 200:
                    logger.warning(f"Статус {resp.status} при поиске #{hashtag}")
                    return videos

                html = await resp.text()

                # TikTok хранит данные в JSON внутри <script> тега
                # Ищем SIGI_STATE или __UNIVERSAL_DATA_FOR_REHYDRATION__
                patterns = [
                    r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
                    r'<script id="SIGI_STATE"[^>]*>(.*?)</script>',
                    r'"ItemList":\s*(\{.*?\})\s*,\s*"',
                ]

                data = None
                for pattern in patterns:
                    match = re.search(pattern, html, re.DOTALL)
                    if match:
                        try:
                            data = json.loads(match.group(1))
                            break
                        except json.JSONDecodeError:
                            continue

                if not data:
                    logger.info(f"Не удалось извлечь данные для #{hashtag}")
                    return videos

                # Пробуем разные структуры данных TikTok
                items = extract_items(data)

                for item in items:
                    try:
                        stats = item.get("stats", {})
                        likes = stats.get("diggCount", 0) or stats.get("likeCount", 0) or 0

                        if likes >= min_likes:
                            video_id = item.get("id", "")
                            author_data = item.get("author", {})
                            author = author_data.get("uniqueId", "") if isinstance(author_data, dict) else str(author_data)

                            videos.append({
                                "id": str(video_id),
                                "url": f"https://www.tiktok.com/@{author}/video/{video_id}",
                                "likes": likes,
                                "comments": stats.get("commentCount", 0),
                                "shares": stats.get("shareCount", 0),
                                "description": item.get("desc", ""),
                                "author": author,
                            })
                    except Exception as e:
                        logger.debug(f"Ошибка при парсинге видео: {e}")
                        continue

                # Сортируем по лайкам (больше — выше)
                videos.sort(key=lambda x: x["likes"], reverse=True)

    except asyncio.TimeoutError:
        logger.error(f"Таймаут при запросе #{hashtag}")
    except Exception as e:
        logger.error(f"Ошибка при поиске #{hashtag}: {e}")

    return videos


def extract_items(data: dict) -> list:
    """
    Извлекает список видео из разных форматов JSON TikTok.
    """
    items = []

    # Формат __UNIVERSAL_DATA_FOR_REHYDRATION__
    try:
        default_scope = data.get("__DEFAULT_SCOPE__", {})
        item_list = default_scope.get("webapp.challenge-detail", {})
        if "itemList" in item_list:
            items = item_list["itemList"]
            if items:
                return items
    except (AttributeError, TypeError):
        pass

    # Формат SIGI_STATE
    try:
        item_module = data.get("ItemModule", {})
        if item_module:
            items = list(item_module.values())
            if items:
                return items
    except (AttributeError, TypeError):
        pass

    # Рекурсивный поиск itemList
    items = find_key_recursive(data, "itemList")
    if items:
        return items

    # Рекурсивный поиск items
    items = find_key_recursive(data, "items")
    if items:
        return items

    return items


def find_key_recursive(data, key, max_depth=5):
    """Рекурсивно ищет ключ в словаре."""
    if max_depth <= 0:
        return []
    if isinstance(data, dict):
        if key in data and isinstance(data[key], list):
            return data[key]
        for v in data.values():
            result = find_key_recursive(v, key, max_depth - 1)
            if result:
                return result
    return []

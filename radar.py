#!/usr/bin/env python3
"""
Радар заказов: читает публичный Telegram-канал через t.me/s/<channel>,
отбирает подходящие заказы, просит Claude написать черновик отклика
и присылает карточку в личный чат Telegram-бота.

Запуск: python radar.py
Переменные окружения:
  TG_BOT_TOKEN       токен бота от @BotFather
  TG_CHAT_ID         id твоего чата с ботом
  LLM_API_KEY        ключ любого LLM-провайдера (не нужен, если draft_enabled: false)
Состояние (последние обработанные посты) хранится в state.json.
"""

import json
import os
import re
import sys
import time
from pathlib import Path

import requests
import yaml
from bs4 import BeautifulSoup

ROOT = Path(__file__).parent
CONFIG = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
STATE_FILE = ROOT / "state.json"

TG_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TG_CHAT_ID", "")
API_KEY = os.environ.get("LLM_API_KEY") or os.environ.get("ANTHROPIC_API_KEY", "")

HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) kwork-radar/1.0"}


# ---------- состояние ----------

def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"seen": []}


def save_state(state: dict) -> None:
    state["seen"] = state["seen"][-500:]
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------- чтение канала ----------

def fetch_posts(channel: str, lookback: int) -> list[dict]:
    url = f"https://t.me/s/{channel}"
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    posts = []
    for msg in soup.select("div.tgme_widget_message"):
        post_id = msg.get("data-post", "")
        body = msg.select_one("div.tgme_widget_message_text")
        if not post_id or not body:
            continue
        for br in body.find_all("br"):
            br.replace_with("\n")
        text = body.get_text("\n").strip()
        text = re.sub(r"\n{3,}", "\n\n", text)
        posts.append({"id": post_id, "url": f"https://t.me/{post_id}", "text": text})
    return posts[-lookback:]


# ---------- разбор поста ----------

def parse_post(text: str) -> dict:
    title = ""
    m = re.search(r"📌\s*(.+)", text)
    if m:
        title = m.group(1).strip()
    tags = re.findall(r"#[\wЀ-ӿ]+", text)
    budget_lo = budget_hi = None
    m = re.search(r"От\s+([\d\s.,]+)\s*₽\s+до\s+([\d\s.,]+)\s*₽", text)
    if m:
        budget_lo = int(re.sub(r"\D", "", m.group(1)) or 0)
        budget_hi = int(re.sub(r"\D", "", m.group(2)) or 0)
    return {"title": title, "tags": tags, "budget_lo": budget_lo, "budget_hi": budget_hi}


def passes_filter(text: str, meta: dict) -> tuple[bool, str]:
    low = text.lower()
    for w in CONFIG["stop_words"]:
        if w.lower() in low:
            return False, f"стоп-слово «{w}»"
    allow = {t.lower() for t in CONFIG["tags_allow"]}
    if allow and not any(t.lower() in allow for t in meta["tags"]):
        return False, "тег не из списка"
    hi = meta["budget_hi"] or meta["budget_lo"] or 0
    if hi < CONFIG["min_budget"]:
        return False, f"бюджет {hi} ₽ ниже порога"
    return True, "ok"


# ---------- черновик ----------

def make_draft(text: str, meta: dict) -> str:
    if not CONFIG.get("draft_enabled", True):
        return ""
    if not API_KEY:
        return "_Черновик не создан: нет LLM_API_KEY._"
    system = (
        "Ты помогаешь фрилансеру писать отклики на заказы с биржи Kwork.\n\n"
        f"Профиль фрилансера:\n{CONFIG['profile']}\n\n{CONFIG['rules']}\n\n"
        "Формат ответа строго такой:\n"
        "ОЦЕНКА: одна строка. Брать / Брать с оговоркой / Не брать, и почему в 10 словах.\n"
        "ЧАСЫ: оценка трудозатрат в часах одним числом или диапазоном.\n"
        "ОТКЛИК:\nтекст отклика, готовый к вставке."
    )
    user = f"Заказ:\n{text}\n\nБюджет заказчика: от {meta['budget_lo']} до {meta['budget_hi']} ₽."
    provider = CONFIG.get("provider", "openai_compat")
    for attempt in range(3):
        try:
            if provider == "anthropic":
                import anthropic
                client = anthropic.Anthropic(api_key=API_KEY)
                resp = client.messages.create(
                    model=CONFIG["model"], max_tokens=900, system=system,
                    messages=[{"role": "user", "content": user}],
                )
                return resp.content[0].text.strip()
            # любой API формата OpenAI: DeepSeek, OpenRouter, российские агрегаторы и т.д.
            r = requests.post(
                CONFIG["base_url"].rstrip("/") + "/chat/completions",
                headers={"Authorization": f"Bearer {API_KEY}"},
                json={
                    "model": CONFIG["model"],
                    "max_tokens": 900,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
                timeout=90,
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:  # noqa: BLE001
            if attempt == 2:
                return f"_Ошибка API: {e}_"
            time.sleep(3 * (attempt + 1))
    return ""


# ---------- отправка ----------

def tg_send(text: str) -> None:
    if not TG_TOKEN or not TG_CHAT:
        print("TG_BOT_TOKEN / TG_CHAT_ID не заданы, печатаю в консоль:\n", text)
        return
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    # Telegram режет сообщения длиннее 4096 символов
    for chunk in [text[i:i + 4000] for i in range(0, len(text), 4000)]:
        r = requests.post(
            url,
            json={"chat_id": TG_CHAT, "text": chunk, "disable_web_page_preview": True},
            timeout=30,
        )
        if r.status_code != 200:
            print("Telegram error:", r.text, file=sys.stderr)


def format_card(post: dict, meta: dict, draft: str) -> str:
    budget = ""
    if meta["budget_hi"]:
        budget = f"{meta['budget_lo']:,} – {meta['budget_hi']:,} ₽".replace(",", " ")
    head = f"🎯 {meta['title'] or 'Заказ'}\n💰 {budget}\n🏷 {' '.join(meta['tags'])}\n🔗 {post['url']}\n"
    # в карточке оставляем только суть: бюджет и теги уже есть в шапке
    lines = []
    for ln in post["text"].split("\n"):
        ln = ln.strip()
        if not ln or ln.startswith(("〰", "💳", "🌐", "📌", "#")) or re.match(r"^От\s", ln):
            continue
        lines.append(ln.removeprefix("📝").strip())
    body = "\n".join(lines)
    if len(body) > 1200:
        body = body[:1200] + "…"
    parts = [head, "— — —", body]
    if draft:
        parts += ["— — —", draft]
    return "\n".join(parts)


# ---------- главный цикл ----------

def main() -> None:
    state = load_state()
    seen = set(state["seen"])
    posts = fetch_posts(CONFIG["channel"], CONFIG["lookback_posts"])
    print(f"Получено постов: {len(posts)}")

    first_run = not seen
    sent = 0
    for post in posts:
        if post["id"] in seen:
            continue
        seen.add(post["id"])
        state["seen"].append(post["id"])
        if first_run:
            # при первом запуске просто запоминаем историю, чтобы не завалить тебя старыми заказами
            continue
        meta = parse_post(post["text"])
        ok, why = passes_filter(post["text"], meta)
        print(f"{post['id']}: {meta['title'][:50]!r} -> {'БЕРЁМ' if ok else 'мимо: ' + why}")
        if not ok:
            continue
        draft = make_draft(post["text"], meta)
        tg_send(format_card(post, meta, draft))
        sent += 1

    save_state(state)
    if first_run:
        print("Первый запуск: история запомнена, уведомления начнутся со следующего запуска.")
    print(f"Отправлено карточек: {sent}")


if __name__ == "__main__":
    main()

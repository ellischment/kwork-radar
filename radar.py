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

DRAFT_BUDGET_SEC = 150      # суммарный потолок времени на оценки за прогон
DRAFT_TIMEOUT_SEC = 25      # потолок на один запрос к модели
_draft_spent = [0.0]

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
        "Ты помогаешь фрилансеру отбирать заказы на бирже Kwork.\n\n"
        f"Профиль фрилансера:\n{CONFIG['profile']}\n\n{CONFIG['rules']}\n\n"
        "Формат ответа строго такой, без лишних строк:\n"
        "ВЕРДИКТ: Брать / С оговоркой / Мимо\n"
        "ПОЧЕМУ: одна строка, максимум 15 слов.\n"
        "ЧАСЫ: только её собственные часы, числом или диапазоном.\n"
        "МИНИМУМ: расчёт себестоимости в виде «3 × 2 500 + 5 000 = 12 500 ₽».\n"
        "ЦЕНА: сколько просить, рубли, в рамках бюджета заказчика.\n"
        "РИСК: одна строка, если есть подвох. Если нет, напиши: нет."
    )
    user = f"Заказ:\n{text}\n\nБюджет заказчика: от {meta['budget_lo']} до {meta['budget_hi']} ₽."
    if _draft_spent[0] >= DRAFT_BUDGET_SEC:
        return "_Оценка пропущена: исчерпан лимит времени на запросы к модели._"
    provider = CONFIG.get("provider", "openai_compat")
    models = CONFIG.get("models") or [CONFIG.get("model")]
    errors = []
    started = time.time()
    for model in [m for m in models if m]:
        try:
            if provider == "anthropic":
                import anthropic
                client = anthropic.Anthropic(api_key=API_KEY)
                resp = client.messages.create(
                    model=model, max_tokens=350, system=system,
                    messages=[{"role": "user", "content": user}],
                )
                return resp.content[0].text.strip()
            # любой API формата OpenAI: DeepSeek, OpenRouter, российские агрегаторы и т.д.
            r = requests.post(
                CONFIG["base_url"].rstrip("/") + "/chat/completions",
                headers={"Authorization": f"Bearer {API_KEY}",
                         "X-Title": "kwork-radar"},
                json={
                    "model": model,
                    "max_tokens": 350,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
                timeout=DRAFT_TIMEOUT_SEC,
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:  # noqa: BLE001
            errors.append(f"{model.split('/')[-1]}: {str(e)[:90]}")
            if time.time() - started > DRAFT_BUDGET_SEC / 2:
                break
        finally:
            _draft_spent[0] = _draft_spent[0] + (time.time() - started)
    return "_Оценку получить не удалось. " + " | ".join(errors) + "_"


# ---------- разбор оценки ----------

def parse_verdict(text: str) -> dict:
    """Достаёт поля из ответа модели. Пустые значения означают, что оценки нет."""
    out = {"verdict": "", "why": "", "hours": "", "floor": "", "price": "", "risk": ""}
    keys = {"ВЕРДИКТ": "verdict", "ПОЧЕМУ": "why", "ЧАСЫ": "hours",
            "МИНИМУМ": "floor", "ЦЕНА": "price", "РИСК": "risk"}
    for line in text.split("\n"):
        for label, key in keys.items():
            if line.strip().upper().startswith(label):
                out[key] = line.split(":", 1)[-1].strip().strip("*_ ")
    return out


def is_worth_attention(v: dict) -> bool:
    """Шлём, только если заказ стоит внимания. Нет оценки — шлём на всякий случай."""
    verdict = v["verdict"].lower()
    if not verdict:
        return True
    return not verdict.startswith("мимо")


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


def format_card(post: dict, meta: dict, v: dict, raw: str) -> str:
    budget = ""
    if meta["budget_hi"]:
        budget = f"{meta['budget_lo']:,} – {meta['budget_hi']:,} ₽".replace(",", " ")

    head = f"🎯 {meta['title'] or 'Заказ'}\n"
    money = f"💰 {budget}"
    if v["price"]:
        money += f"  ·  просить {v['price']}"
    head += money
    if v["hours"]:
        head += f"\n⏱ {v['hours']} ч твоего времени"
        if v["floor"]:
            head += f"  ·  себестоимость {v['floor']}"

    parts = [head]
    if v["why"]:
        parts.append(f"\n✅ {v['why']}")
    risk = v["risk"].lower().strip(" .")
    if v["risk"] and risk not in ("нет", "отсутствует", "-", "нет рисков"):
        parts.append(f"⚠️ {v['risk']}")
    if not v["verdict"]:
        parts.append(f"\nℹ️ Оценка не получена, смотри сама.\n{raw[:200]}")

    body = []
    for ln in post["text"].split("\n"):
        ln = ln.strip()
        if not ln or ln.startswith(("〰", "💳", "🌐", "📌", "#")) or re.match(r"^От\s", ln):
            continue
        body.append(ln.removeprefix("📝").strip())
    text = "\n".join(body)
    if len(text) > 700:
        text = text[:700] + "…"
    parts.append("\n" + text)
    parts.append(f"\n🔗 {post['url']}")
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
        raw = make_draft(post["text"], meta)
        v = parse_verdict(raw)
        if not is_worth_attention(v):
            print(f"   -> не шлю, вердикт «{v['verdict']}»: {v['why']}")
            continue
        tg_send(format_card(post, meta, v, raw))
        sent += 1

    save_state(state)
    if first_run:
        print("Первый запуск: история запомнена, уведомления начнутся со следующего запуска.")
    print(f"Отправлено карточек: {sent}")


if __name__ == "__main__":
    main()

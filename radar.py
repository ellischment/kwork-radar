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
        kid = ""
        for a in msg.select("a[href]"):
            m = re.search(r"kwork\.ru/projects/(\d+)", a["href"])
            if m:
                kid = m.group(1)
                break
        text = body.get_text("\n").strip()
        text = re.sub(r"\n{3,}", "\n\n", text)
        if not kid:
            m = re.search(r"kwork\.ru/projects/(\d+)", text)
            kid = m.group(1) if m else ""
        posts.append({"id": post_id, "url": f"https://t.me/{post_id}", "text": text, "kid": kid})
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

def _extract_json(text: str) -> dict:
    """Достаёт первый JSON-объект из ответа, даже если модель обложила его текстом."""
    depth, start = 0, -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    obj = json.loads(text[start:i + 1])
                    if isinstance(obj, dict):
                        return obj
                except json.JSONDecodeError:
                    pass
                start = -1
    return {}


def _call_model(model: str, system: str, user: str) -> str:
    if CONFIG.get("provider") == "anthropic":
        import anthropic
        client = anthropic.Anthropic(api_key=API_KEY)
        resp = client.messages.create(model=model, max_tokens=500, system=system,
                                      messages=[{"role": "user", "content": user}])
        return resp.content[0].text
    r = requests.post(
        CONFIG["base_url"].rstrip("/") + "/chat/completions",
        headers={"Authorization": f"Bearer {API_KEY}", "X-Title": "kwork-radar"},
        json={"model": model, "max_tokens": 500, "temperature": 0,
              "messages": [{"role": "system", "content": system},
                           {"role": "user", "content": user}]},
        timeout=DRAFT_TIMEOUT_SEC,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def make_draft(text: str, meta: dict) -> tuple[dict, str]:
    """Возвращает (разобранная оценка, диагностика). Пустой dict означает, что оценки нет."""
    if not CONFIG.get("draft_enabled", True) or not API_KEY:
        return {}, "оценка выключена или нет ключа"
    if _draft_spent[0] >= DRAFT_BUDGET_SEC:
        return {}, "исчерпан лимит времени на запросы"

    system = (
        "Ты помогаешь фрилансеру отбирать заказы на бирже Kwork.\n\n"
        f"Профиль фрилансера:\n{CONFIG['profile']}\n\n{CONFIG['rules']}\n\n"
        "КРАЙНЕ ВАЖНО. Ответь ровно одним JSON-объектом и ничем больше. "
        "Не рассуждай, не пиши план, не используй markdown. "
        "Все значения строками и ТОЛЬКО по-русски.\n"
        '{"verdict":"Брать" или "С оговоркой" или "Отзыв" или "Мимо",'
        '"why":"одна живая фраза, чем заказ хорош именно для неё",'
        '"hours":"только её часы, число или диапазон",'
        '"floor":"себестоимость в виде 3 x 2 500 + 5 000 = 12 500 Р",'
        '"price":"сколько просить, рубли",'
        '"risk":"подвох одной фразой или слово нет"}'
    )
    user = f"Заказ:\n{text}\n\nБюджет заказчика: от {meta['budget_lo']} до {meta['budget_hi']} ₽."

    notes = []
    for model in [m for m in (CONFIG.get("models") or []) if m]:
        started = time.time()
        try:
            raw = _call_model(model, system, user)
            obj = _extract_json(raw)
            verdict = str(obj.get("verdict", "")).strip()
            if verdict:
                out = {k: str(obj.get(k, "")).strip() for k in
                       ("verdict", "why", "hours", "floor", "price", "risk")}
                return out, f"{model.split('/')[-1]}"
            notes.append(f"{model.split('/')[-1]}: ответ без JSON")
        except Exception as e:  # noqa: BLE001
            notes.append(f"{model.split('/')[-1]}: {str(e)[:70]}")
        finally:
            _draft_spent[0] = _draft_spent[0] + (time.time() - started)
        if _draft_spent[0] >= DRAFT_BUDGET_SEC:
            break
    return {}, " | ".join(notes)


# ---------- разбор оценки ----------

def is_worth_attention(v: dict) -> bool:
    """Шлём, только если заказ стоит внимания. Нет оценки — шлём на всякий случай."""
    verdict = v.get("verdict", "").lower()
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


def format_card(post: dict, meta: dict, v: dict, note: str) -> str:
    budget = ""
    if meta["budget_hi"]:
        budget = f"{meta['budget_lo']:,} – {meta['budget_hi']:,} ₽".replace(",", " ")

    seed = "🌱 для отзыва · " if v.get("verdict", "").lower().startswith("отзыв") else ""
    head = f"🎯 {seed}{meta['title'] or 'Заказ'}\n"
    money = f"💰 {budget}"
    if v.get("price"):
        money += f"  ·  просить {v['price']}"
    head += money
    if v.get("hours"):
        head += f"\n⏱ {v['hours']} ч твоего времени"
        if v.get("floor"):
            head += f"  ·  себестоимость {v['floor']}"

    parts = [head]
    if v.get("why"):
        parts.append(f"\n✅ {v['why']}")
    risk = v.get("risk", "").lower().strip(" .")
    if v.get("risk") and risk not in ("нет", "отсутствует", "-", "нет рисков"):
        parts.append(f"⚠️ {v['risk']}")
    if not v.get("verdict"):
        parts.append(f"\nℹ️ Оценку получить не удалось, смотри сама. ({note[:120]})")

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
    if post.get("kid"):
        parts.append(f"\n⚡ Откликнуться: https://kwork.ru/new_offer?project={post['kid']}")
        parts.append(f"🔗 Заказ: https://kwork.ru/projects/{post['kid']}/view")
    else:
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
        v, note = make_draft(post["text"], meta)
        print(f"   оценка: {note}")
        if not is_worth_attention(v):
            print(f"   -> не шлю, вердикт «{v['verdict']}»: {v['why']}")
            continue
        tg_send(format_card(post, meta, v, note))
        sent += 1

    save_state(state)
    if first_run:
        print("Первый запуск: история запомнена, уведомления начнутся со следующего запуска.")
    print(f"Отправлено карточек: {sent}")


if __name__ == "__main__":
    main()

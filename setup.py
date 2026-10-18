#!/usr/bin/env python3
"""
Автонастройка радара. Запусти из папки проекта:  python setup.py

Что делает сам:
  - находит твой chat_id по токену бота (ждёт, пока ты напишешь боту);
  - отправляет тестовое сообщение, чтобы убедиться, что бот пишет тебе;
  - создаёт публичный репозиторий на GitHub;
  - заливает туда файлы проекта;
  - прописывает секреты TG_BOT_TOKEN и TG_CHAT_ID;
  - включает право Actions писать в репозиторий;
  - запускает первый прогон.

Что нужно от тебя заранее (две вещи, их нельзя сделать без твоего аккаунта):
  1) Токен бота от @BotFather: /newbot, имя, скопировать токен.
  2) Токен GitHub: https://github.com/settings/tokens/new
     Note: radar, Expiration: 90 days, галочки: repo и workflow. Generate token.
"""

import base64
import getpass
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
REPO_NAME = "kwork-radar"


def die(msg: str) -> None:
    print(f"\n✖ {msg}")
    sys.exit(1)


def ask(prompt: str, secret: bool = False) -> str:
    value = (getpass.getpass(prompt) if secret else input(prompt)).strip()
    if not value:
        die("Пустое значение, прерываю.")
    return value


def api(url: str, token: str = "", data: dict | None = None, method: str = "") -> dict:
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method or ("POST" if body else "GET"))
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "kwork-radar-setup")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if body:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:400]
        raise RuntimeError(f"HTTP {e.code}: {detail}") from None


def run(*cmd: str, quiet: bool = False) -> None:
    res = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if res.returncode != 0 and not quiet:
        die(f"Команда {' '.join(cmd[:2])} не удалась:\n{res.stderr.strip()[:500]}")


def ensure_pynacl():
    try:
        from nacl import encoding, public  # noqa: F401
    except ImportError:
        print("Ставлю pynacl (нужен для шифрования секретов GitHub)…")
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "pynacl"], check=False)
        try:
            from nacl import encoding, public  # noqa: F401
        except ImportError:
            die("Не удалось установить pynacl. Запусти вручную: pip install pynacl")
    from nacl import encoding, public

    return encoding, public


# ---------- Telegram ----------

def tg_api(token: str, method: str, **params) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    if params:
        import urllib.parse

        url += "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=40) as r:
        return json.loads(r.read())


def find_chat_id(token: str) -> str:
    print("\nНапиши своему боту в Telegram любое сообщение (хоть «привет»).")
    print("Жду до 3 минут…")
    deadline = time.time() + 180
    while time.time() < deadline:
        try:
            res = tg_api(token, "getUpdates", timeout=0)
        except Exception as e:  # noqa: BLE001
            die(f"Telegram не отвечает: {e}. Проверь токен бота.")
        if not res.get("ok"):
            die(f"Telegram вернул ошибку: {res}. Токен бота неверный?")
        for upd in reversed(res.get("result", [])):
            msg = upd.get("message") or upd.get("edited_message") or {}
            chat = msg.get("chat") or {}
            if chat.get("id"):
                name = chat.get("first_name") or chat.get("username") or "ты"
                print(f"✔ Нашла чат с {name}")
                return str(chat["id"])
        time.sleep(3)
    die("Сообщение от тебя так и не пришло. Запусти setup.py заново.")


# ---------- GitHub ----------

def gh_create_repo(token: str, login: str) -> str:
    try:
        repo = api("https://api.github.com/user/repos", token,
                   {"name": REPO_NAME, "private": False, "has_issues": False,
                    "has_wiki": False, "description": "Радар фриланс-заказов из Telegram"})
        print(f"✔ Репозиторий создан: {repo['html_url']}")
        return repo["html_url"]
    except RuntimeError as e:
        if "already exists" in str(e):
            print(f"• Репозиторий {login}/{REPO_NAME} уже есть, использую его.")
            return f"https://github.com/{login}/{REPO_NAME}"
        raise


def gh_set_secret(token: str, login: str, name: str, value: str) -> None:
    encoding, public = ensure_pynacl()
    pk = api(f"https://api.github.com/repos/{login}/{REPO_NAME}/actions/secrets/public-key", token)
    box = public.SealedBox(public.PublicKey(pk["key"].encode(), encoding.Base64Encoder()))
    sealed = base64.b64encode(box.encrypt(value.encode())).decode()
    api(f"https://api.github.com/repos/{login}/{REPO_NAME}/actions/secrets/{name}", token,
        {"encrypted_value": sealed, "key_id": pk["key_id"]}, method="PUT")
    print(f"✔ Секрет {name} записан")


def main() -> None:
    print(__doc__)
    input("Готова? Нажми Enter, когда у тебя на руках оба токена. ")

    bot_token = ask("\nТокен бота от @BotFather: ", secret=True)
    me = tg_api(bot_token, "getMe")
    if not me.get("ok"):
        die("Токен бота не принят Telegram.")
    print(f"✔ Бот @{me['result']['username']}")

    chat_id = find_chat_id(bot_token)
    tg_api(bot_token, "sendMessage", chat_id=chat_id,
           text="Радар на связи. Здесь будут появляться подходящие заказы.")
    print("✔ Тестовое сообщение отправлено, проверь Telegram")

    gh_token = ask("\nТокен GitHub (scopes repo + workflow): ", secret=True)
    user = api("https://api.github.com/user", gh_token)
    login = user["login"]
    print(f"✔ GitHub: {login}")

    url = gh_create_repo(gh_token, login)

    print("\nЗаливаю файлы…")
    if not (ROOT / ".git").exists():
        run("git", "init", "-q")
        run("git", "branch", "-M", "main")
    run("git", "add", "-A")
    subprocess.run(["git", "-c", "user.name=radar-setup",
                    "-c", "user.email=radar@local", "commit", "-q", "-m", "Радар заказов"],
                   cwd=ROOT, capture_output=True, text=True)
    run("git", "remote", "remove", "origin", quiet=True)
    run("git", "remote", "add", "origin",
        f"https://{login}:{gh_token}@github.com/{login}/{REPO_NAME}.git")
    run("git", "push", "-q", "-u", "origin", "main", "--force")
    run("git", "remote", "set-url", "origin", f"https://github.com/{login}/{REPO_NAME}.git")
    print("✔ Файлы на GitHub")

    gh_set_secret(gh_token, login, "TG_BOT_TOKEN", bot_token)
    gh_set_secret(gh_token, login, "TG_CHAT_ID", chat_id)

    api(f"https://api.github.com/repos/{login}/{REPO_NAME}/actions/permissions/workflow", gh_token,
        {"default_workflow_permissions": "write", "can_approve_pull_request_reviews": False},
        method="PUT")
    print("✔ Actions разрешено писать в репозиторий")

    time.sleep(5)
    try:
        api(f"https://api.github.com/repos/{login}/{REPO_NAME}/actions/workflows/radar.yml/dispatches",
            gh_token, {"ref": "main"})
        print("✔ Первый прогон запущен (он только запомнит историю канала)")
    except RuntimeError as e:
        print(f"• Запустить прогон автоматически не вышло ({e}).")
        print(f"  Нажми Run workflow вручную: {url}/actions")

    print(f"""
Готово. Радар работает.

Репозиторий: {url}
Прогоны:     {url}/actions

Дальше ничего делать не нужно. Первый прогон запоминает историю канала,
со второго (через 5 минут) начнут приходить карточки заказов.
Если захочешь поменять фильтры, правь config.yaml прямо на GitHub,
изменения подхватятся со следующего прогона.
""")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nПрервано.")
    except RuntimeError as e:
        die(str(e))

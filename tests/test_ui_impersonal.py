# -*- coding: utf-8 -*-
"""Офлайн-проверка: тексты интерфейса безличные — без обращения на «ты» и «вы».

Инструкция пишется инфинитивом («нажать», «открыть»), описание — в третьем лице.
Проверяются все строки кода и шаблонов, кроме комментариев Python (`# …`).

Запуск из папки панели:  .venv\\Scripts\\python.exe tests\\test_ui_impersonal.py
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILES = ["dashboard.py", "wg_clients.py", "xkeen_killswitch.py", "sub_expiry_notifier.py",
         "xkeen_toast_daemon.py", "xkeen_snapshot_daily.py", "setup_helper.py"]

PRONOUNS = ("ты тебе тебя тобой твой твоя твоё твое твои твоих твоим твоими твоего твоей твоему твою "
            "вы вас вам вами ваш ваша ваше ваши вашего вашей вашим вашими ваших вашему вашу")
# Повелительное наклонение, встречавшееся в интерфейсе; формы на «вы» — отдельным правилом ниже.
IMPERATIVES = (
    "нажми жми открой проверь выбери используй добавь поставь смени вставь напиши повтори обнови найди "
    "впиши скопируй убери создай подожди запусти введи раскрой попробуй настрой установи сними положи "
    "кликни выполни выбирай сохрани поменяй пиши перезапусти перезагрузи нажимай забудь включи верни укажи "
    "убедись сравни сохраняй сделай сгенерируй делай смотри жди открывай отмени переименуй перетащи "
    "заполни разблокируй распакуй задай зайди войди выйди глянь почини скачай оставь отключи выключи "
    "пройди пропиши подключи загрузи импортируй")
YOU_PLURAL_IMPERATIVE = ("нажмите введите выберите включите выключите проверьте добавьте задайте скачайте "
                         "выдайте укажите уберите загрузите замените заполните повторите посмотрите "
                         "отзовите снимите сообщите покажите пройдите войдите прочитайте переназначьте "
                         "удалите отметьте освободите отключите настройте раскройте откройте нажимайте")
SECOND_PERSON = re.compile(r"(?<![а-яё])[а-яё]+(?:ешь|ёшь|ишь|ешься|ишься)(?![а-яё])")

WORDS = set((PRONOUNS + " " + IMPERATIVES + " " + YOU_PLURAL_IMPERATIVE).split())
WORD_RE = re.compile(r"(?<![а-яёa-z0-9_])([а-яё]+)(?![а-яёa-z0-9_])")
SECOND_PERSON_OK = {"лишь"}

fails = []
for name in FILES:
    path = os.path.join(ROOT, name)
    if not os.path.exists(path):
        continue
    with open(path, encoding="utf-8") as f:
        for no, line in enumerate(f, 1):
            if line.lstrip().startswith("#"):
                continue
            low = line.lower()
            hits = {w for w in WORD_RE.findall(low) if w in WORDS}
            hits |= {m.group(0) for m in SECOND_PERSON.finditer(low)} - SECOND_PERSON_OK
            if hits:
                fails.append(f"{name}:{no}: {', '.join(sorted(hits))} | {line.strip()[:120]}")

if fails:
    print("test_ui_impersonal: FAIL — обращение к читателю в тексте:")
    for x in fails[:50]:
        print("  " + x)
    if len(fails) > 50:
        print(f"  … и ещё {len(fails) - 50}")
    sys.exit(1)
print("test_ui_impersonal: OK")

# Contributing

Спасибо за интерес к **xray-dashboard**! Любой вклад приветствуется — баг-репорты, фичи, документация, тесты.

## 🐛 Сообщить о баге

Open Issue → выбери template **«🐛 Bug report»** → заполни поля. Версия панели, модель Keenetic, версия XKeen, шаги воспроизведения, логи.

## 💡 Предложить фичу

Issue → **«💡 Feature request»**. Опиши **что** хочешь и **зачем** — это поможет понять use case.

## ❓ Задать вопрос

Issue → **«❓ Вопрос»** или в **[Discussions](https://github.com/yuran2000/xray-dashboard/discussions)** (категория Q&A).

Перед вопросом — проверь Quick Start в README и Help-секцию в самой панели (она большая и покрывает 90% вопросов).

## 🔀 Прислать Pull Request

1. **Fork** репо на свой GitHub
2. Создай branch: `feature/что-то` или `fix/что-то`
3. Сделай изменения, помня про:
   - **Python код**: UTF-8, 4 пробела, docstrings для новых endpoint'ов
   - **.bat-файлы**: **только ASCII** (cmd.exe parser ломается на UTF-8 без BOM — см. v1.7.23)
   - **Зависимости**: минимизировать. Стандартная библиотека Python > pip-пакеты
   - **Commit messages**: короткий заголовок (≤72 chars) + опционально пояснение через пустую строку
4. **Версионирование**: bump `_VERSION_FALLBACK` в `dashboard.py` (последняя цифра — патч)
5. Push в свой fork → открой PR в `yuran2000/xray-dashboard:main`
6. В описании PR — что изменилось, зачем, как тестировал

## 🧪 Перед PR — minimal-чеклист

```powershell
# 1. Синтаксис Python OK?
python -c "import ast; ast.parse(open('dashboard.py', encoding='utf-8').read())"

# 2. Если правил .bat — проверь pure ASCII
# (используй редактор который показывает encoding в статус-баре)

# 3. Если правил endpoint — открой /xkeen в браузере и убедись что UI не сломан
```

## 📐 Архитектура (для ориентации)

`dashboard.py` — монолит ~13000 строк. Структура:

```
строки 1-200       Imports, config loading
строки 200-600     SSH helpers (keenetic_ssh, _ssh_args)
строки 600-900     watchdog.config read/write
строки 900-2000    Flask app setup, auth (@requires_auth)
строки 2000-5000   API endpoints (/api/xkeen/*, /api/keenetic/*)
строки 5000-8500   HTML template (jinja2, embedded в Python string)
строки 8500-13000  JavaScript (embedded в template)
```

**HTML/JS — inline** в Python-строке. Не разбиваем на отдельные `.html`/`.js` файлы пока. Это компромисс — большой файл, но один процесс, один deploy, нет path-конфликтов на Windows.

## 🗺 Где жить логика

- **На роутере** — `/opt/etc/xray/watchdog.sh` (минута cron-loop). Шаблоны в `bootstrap/`.
- **В дашборде** — Flask routes + SSH к роутеру через `keenetic_ssh()`.
- **На PC** — кэш и UI, никакого критичного state.

## 💬 Сложные дискуссии

Если фича большая или меняет архитектуру — открой Discussion в категории **Ideas** перед началом работы. Сэкономит время.

## 📜 Лицензия

Все вклады по умолчанию под MIT (как остальной код). См. [LICENSE](LICENSE).

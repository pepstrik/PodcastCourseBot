# PodcastCourseBot 📚🎙️

Мини-курс «Подкаст за 7 шагов» в Telegram: прогресс по шагам, финальный тест, PDF‑сертификат.

## Функции
- Пошаговая навигация и состояние прогресса (локальный JSON-кэш + авто‑сейв)
- Финальный тест и генерация PDF‑сертификата (fpdf)
- Админ-команды, меню, обработка ошибок

## Запуск
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.py.example config.py
python bot.py
```

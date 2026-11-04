# Бот «Зарплаты HoReCa» (@delaidelo24bot)

Чистый Python 3.10+, без сторонних библиотек. Данные — SQLite в /opt/ddbot/data.

- `texts.py` — все тексты и цифры зарплат (править здесь).
- `bot.py` — логика.
- Ссылки с меткой источника: `https://t.me/delaidelo24bot?start=vk_clip`
- `/stats` — статистика (для ID из ADMIN_IDS).

Обновить: `cd /opt/ddbot && sudo git pull && sudo systemctl restart ddbot`
Логи: `sudo journalctl -u ddbot -n 50 --no-pager`

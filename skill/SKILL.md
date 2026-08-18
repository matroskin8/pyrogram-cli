---
name: tgcli
description: Use when working with Telegram via tgcli CLI (Pyrogram).
tags: [telegram, pyrogram, mtproto, cli, user-bot]
---

# tgcli — Telegram CLI от имени аккаунта

`tgcli` — обёртка над Pyrogram-сессией владельца: работает от имени его аккаунта (MTProto, не Bot API). Этот скилл — «память агента»: триггеры, команды и грабли, которые нужно помнить при работе с CLI. Установка: `pip install .` в корне репозитория.

## Креды (env)
- `TGCLI_API_ID`, `TGCLI_API_HASH` (из my.telegram.org), `TGCLI_SESSION` — путь к session-файлу (обычно `<repo>/.env` или окружение)

```bash
export TGCLI_API_ID=<your_api_id> TGCLI_API_HASH=<your_api_hash> TGCLI_SESSION=<path_to_session>
```

## Команды
```bash
tgcli me                          # кто в сессии (id/username)
tgcli dialogs -n 20               # последние диалоги
tgcli dialogs -q "фильтр"         # фильтр по имени
tgcli search "запрос" -n 5        # поиск по ~200 последним диалогам (на стороне Telegram)
tgcli history @user -n 20         # история чата (@username / id / точное название)
tgcli history @user -q "слово"    # история с фильтром
tgcli send @user "текст"          # отправить сообщение от имени владельца
tgcli send-file @user <file_path> -c "подпись"   # отправить файл
tgcli contacts -q "Имя"           # поиск контакта
tgcli find-bot "имя"              # поиск бота по имени/username
tgcli export @user out.jsonl -n 5000   # экспорт чата в JSONL (для архива/поиска)
```

## Pitfalls
- **FLOOD_WAIT**: частые запросы → Telegram режет (`FLOOD_WAIT: try again in Ns`). Не ретраить вслепую, подождать N.
- **SQLite-lock**: session-файл — SQLite, конкурентные процессы (два tgcli сразу, или tgcli + другой pyrogram-скрипт) → lock. Запускать одну команду за раз.
- **Дубли отправки**: после сетевого таймаута `send`/`send-file` не повторять вслепую — сначала `history`, проверить, дошло ли.
- **Подтверждение отправки**: `send`/`send-file` — необратимое внешнее действие. Свои боты, свои каналы, знакомые контакты — можно; незнакомые/чужие чаты и любая массовая рассылка — только после явного подтверждения владельца.
- **Гео-наблюдение**: при несовпадении региона номера и IP вход с зарубежного сервера может не получить код (наблюдение из проверенного случая, не универсальное правило). Если сессия уже создана — новый `login` без необходимости не запускать.
- **Секрет**: session-файл = ключ от аккаунта. Не логировать, не коммитить, не передавать. `api_hash` не светить.
- **Search-ограничение**: итерирует ~200 последних диалогов; для глубокого поиска — `export` нужных чатов в JSONL + grep/jq.
- `me.phone_number` (не `phone`), `m.date` уже datetime (не timestamp) в Pyrogram 2.0.

## Когда использовать
- Владелец просит «поищи в переписке с X», «что писал Y про Z» → `tgcli search` + `history`.
- Отправить файл/ТЗ/сообщение от имени владельца (ботам, людям, каналам) → `send`/`send-file` (сначала `find-bot`/`contacts`, чтобы найти адресата; внешние адресаты — с подтверждением).
- Собрать архив переписки → `export` (потом можно грепать локально).
- Проверить, кто в сессии / жива ли сессия → `tgcli me`.

## Установка/обновление
```bash
cd <repo> && pip install -e . && git pull
```


# Автопостинг в Telegram-канал «Железный Хоккей» через GitHub Actions

Этот репозиторий публикует посты в канал `@kfkfkjfjfc` с помощью Telegram Bot API.
Публикации запускаются по расписанию (cron) и вручную (workflow_dispatch).

## Настройка
1. Создайте репозиторий на GitHub и загрузите сюда файлы из этого архива.
2. Откройте **Settings → Secrets and variables → Actions → New repository secret** и добавьте:
   - `BOT_TOKEN` — токен вашего бота (получен в @BotFather).
   - `CHANNEL_USERNAME` — например `@kfkfkjfjfc`.
3. (Опционально) Замените `assets/promo.png` на свою картинку для приветственного поста.

## Как запустить
- Вкладка **Actions** → выберите нужный workflow → **Run workflow** (для мгновенного запуска).
- По расписанию (cron) — см. секции `on.schedule` внутри файлов workflow.

> Важно: cron в GitHub Actions — по **UTC**. Москва = UTC+3.

## Файлы workflow
- `.github/workflows/post_welcome.yml` — приветственный пост (с картинкой).
- `.github/workflows/post_text.yml` — универсальный текстовый пост (можно править текст прямо в файле перед коммитом).
- `.github/workflows/post_photo.yml` — пост с картинкой + подписью (правьте текст в env).


## Done
- Auth (login/register/logout)
- File upload/download/delete
- Folders & nested directories
- Rename files and folders
- i18n (EN / UK / DE)

## Next
- Delete folders (empty / recursive)
- Move files between folders
- File info (size, date)
- UI improvements


Нижче — детальний, “практичний” функціонал адміна для CloudBox, з чітким поділом: що має робити admin, що можна віддати moderator, і що бачить звичайний користувач. Я орієнтуюсь на твій MVP (файли/папки) + твою вимогу: преміум видає адмін/модератор.

0) Основна ідея

Admin = керує системою і правами.
Moderator = керує користувачами в межах правил (преміум/блокування/скарги).
User/Premium = користується функціями сховища.

1) Адмін-панель: розділи та можливості
1.1 Dashboard (головна сторінка адміна)

Адмін бачить коротку картину системи:

Кількість користувачів: всього / активні за 7 днів

Скільки premium (і скільки закінчиться скоро, якщо буде premium_until)

Використання диску:

total (всі користувачі)

топ-10 користувачів за місцем

Статистика файлів:

кількість файлів/папок

середній розмір upload

найпопулярніші типи файлів (png/jpg/pdf/zip тощо)

Системні помилки/попередження:

останні 20 помилок (якщо буде логування)

failed login спроби (якщо додаси)

Для MVP можна зробити “легку” версію: users count + total storage + top users.

1.2 Користувачі (Users Management) — головний модуль

Таблиця користувачів з пошуком/фільтрами:

Колонки:

id

username

email (якщо є)

role (user/mod/admin)

premium (on/off або до дати)

status (active / blocked)

created_at, last_login_at

storage_used (MB/GB)

files_count (опційно)

Фільтри:

роль

premium yes/no

blocked yes/no

usage > X GB

created last 30 days

Дії для admin:

Змінити роль користувача:

user → moderator

moderator → user

(admin → user тільки іншому адміну; себе “розадмінити” не давати)

Видати/забрати Premium:

toggle is_premium

або встановити premium_until

Блокування/розблокування:

is_active = 0/1

при блокуванні: заборонити логін

Скинути пароль (для MVP можна зробити “force reset token”)

Переглянути профіль користувача (User details)

Імперсонація (опційно): “увійти як користувач” для дебагу

Видалити користувача (обережно) + що робити з файлами:

delete storage

або “архівувати”

Керування квотою:

set quota_mb індивідуально

або “по плану” (free/premium)

Делегувати модератору можна:

premium on/off

блокування/розблокування

перегляд списку користувачів
А от роль — краще лише адміну.

1.3 Premium Management (права преміуму)

Адмін визначає, що саме дає premium:

Приклад набору преміум-функцій:

більша quota (наприклад Free 2GB, Premium 50GB)

більший max upload size

доступ до share links

доступ до trash/restore

доступ до advanced preview

доступ до zip download (папку в zip)

Адмін-панель може мати сторінку:

“Premium features toggles” (вкл/викл фічі глобально)

“Plans” (пізніше)

Для MVP достатньо: quota + max upload + share links (пізніше).

1.4 Storage Administration (сховище)

Адмін керує “фізикою” файлів:

Перегляд storage використання по користувачах

Очистка сміття/кеша (якщо буде .trash)

Перерахунок usage (на випадок збоїв)

Пошук файлу по назві (наприклад “invoice.pdf” у кого є)

Перевірка підозрілих файлів (за розширенням: .exe, .js, .bat)

Масове обмеження:

заборонити upload деяких типів файлів

встановити max upload size глобально

Важливо: адмін не повинен за замовчуванням бачити вміст файлів користувача без потреби (це питання приватності). Для MVP можна не робити “перегляд файлів інших”.

1.5 Security & Access (безпека)

Адмін керує:

політикою паролів (мін. довжина, складність)

лімітом спроб логіну (rate limit)

сесіями:

“logout user everywhere”

примусове завершення сесій

audit log:

хто кому видав premium

хто змінив роль

хто заблокував користувача

хто видалив папку (опційно)

Для MVP рекомендую мінімум:

audit log для premium/role/block.

1.6 System Settings (налаштування системи)

APP_NAME, мови за замовчуванням

default quota для free/premium

max upload size free/premium

дозволені типи файлів

режим maintenance (сайт тільки для адміна)

backup settings (якщо додаси)

1.7 Logs / Monitoring (журнал)

помилки сервера (500)

дії користувачів (upload/delete/move)

підозрілі дії (спроби .., багато запитів)

Можна мінімум:

app.log + сторінка “Last 200 lines”.

2) Розподіл між admin і moderator (рекомендовано)
Moderator може:

вмикати/вимикати premium

блокувати/розблокувати користувачів

переглядати список користувачів

бачити audit log (обмежено)

Admin може все вище +:

змінювати ролі

змінювати глобальні налаштування

видаляти користувачів

керувати квотами/правилами безпеки

керувати резервними копіями

3) Мінімальний MVP для адміна (щоб не роздувати)

Якщо робити “правильно й швидко”, то MVP адміна = 3 сторінки:

/admin/users
таблиця + пошук + кнопки:

Toggle premium

Block/unblock

(Admin only) change role

/admin/audit
лог: хто кому що змінив (premium/role/block)

/admin/settings
quota free/premium + max upload size free/premium

4) Які “преміум можливості” варто дати першими

Щоб преміум мав сенс вже зараз:

Quota: Free 200MB, Premium 10GB (або як хочеш)

Max upload size: Free 10MB, Premium 200MB

Share link (тільки premium) — дуже “хмарна” фіча

5) Важливі правила безпеки для адмін-функцій

модератор не може робити admin

admin не може “сам себе” понизити (щоб не втратити доступ)

всі адмін-дії → в audit log

блокування користувача → скинути його сесію (logout everywhere) — пізніше
## Runtime secret configuration

CloudBox requires `SECRET_KEY` from environment at startup.
If `SECRET_KEY` is missing or shorter than 32 characters, app startup fails with `RuntimeError`.

For systemd deployments use:
- Unit file: `deploy/systemd/cloudbox.service`
- Environment file: `/etc/cloudbox/cloudbox.env`

Example `/etc/cloudbox/cloudbox.env` content:

```
SECRET_KEY=replace-with-a-random-secret-at-least-32-characters-long
```

## Security note
Secure session cookies require HTTPS; Cloudflare Tunnel provides HTTPS externally for CloudBox deployments behind Nginx.

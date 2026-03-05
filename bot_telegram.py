import asyncio
import html
import logging

from aiogram.utils import executor

from create_bot import bot, dp
from db_api import db_commands
from db_api.database import create_base
from handlers import client
from muiv_schedule_monitor import POLL_SECONDS, check_for_schedule_updates

monitor_task = None


def setup_handlers(dispatcher):
    """Регистрирует обработчики бота."""
    client.register_handlers_client(dispatcher)


def _normalize_text(value) -> str:
    """Нормализует значения расписания в короткие строки."""
    if value is None:
        return ''
    return str(value).strip()


def _course_name_from_key(course_key: str) -> str:
    """Извлекает имя курса из технического ключа."""
    key = _normalize_text(course_key)
    if ':' in key:
        return key.split(':', 1)[1].strip()
    return key


def _build_group_day_snapshot():
    """Строит snapshot по группам и дням для сравнения изменений расписания."""
    snapshot = {}

    for course_key, groups in client.schedule_data.items():
        course_name = _course_name_from_key(course_key)

        for group_code, weeks in groups.items():
            group_map = snapshot.setdefault(group_code, {})

            for week_label, entries in weeks.items():
                for entry in entries:
                    day_name = _normalize_text(entry.get('day'))
                    date_text = _normalize_text(entry.get('date'))
                    time_text = _normalize_text(entry.get('time'))
                    lesson_text = _normalize_text(entry.get('lesson'))

                    if not date_text or date_text.lower() == 'не указана':
                        continue

                    signature = ' | '.join(
                        [course_name, _normalize_text(week_label), day_name, time_text, lesson_text]
                    )
                    group_map.setdefault(date_text, set()).add(signature)

    normalized_snapshot = {}
    for group_code, days in snapshot.items():
        normalized_snapshot[group_code] = {
            date_key: tuple(sorted(signatures))
            for date_key, signatures in days.items()
        }

    return normalized_snapshot


def _collect_changed_group_days(old_snapshot, new_snapshot):
    """Возвращает изменившиеся дни расписания по группам."""
    changed = {}

    all_groups = set(old_snapshot.keys()) | set(new_snapshot.keys())
    for group_code in all_groups:
        old_days = old_snapshot.get(group_code, {})
        new_days = new_snapshot.get(group_code, {})

        all_dates = set(old_days.keys()) | set(new_days.keys())
        changed_dates = [date_key for date_key in all_dates if old_days.get(date_key) != new_days.get(date_key)]

        if changed_dates:
            changed[group_code] = sorted(changed_dates)

    return changed


async def _notify_users_about_changes(changed_group_days, monitor_report):
    """Отправляет уведомления пользователям, для чьих групп есть изменения."""
    if not changed_group_days:
        return 0

    users = await db_commands.get_users_with_selected_groups(set(changed_group_days.keys()))
    if not users:
        return 0

    new_count = len(monitor_report.get('new_files', []))
    updated_count = len(monitor_report.get('updated_files', []))

    notified = 0
    for user in users:
        telegram_id = user.get('telegram_id')
        group_code = user.get('selected_group')
        days = changed_group_days.get(group_code)
        if not telegram_id or not group_code or not days:
            continue

        days_preview = ', '.join(days[:10])
        if len(days) > 10:
            days_preview += f' и еще {len(days) - 10}'

        text = (
            '🔔 Появилось новое расписание.\n'
            f'👥 Группа: <b>{html.escape(str(group_code))}</b>\n'
            f'📅 Изменились дни: <b>{html.escape(days_preview)}</b>\n'
            f'🗂️ Файлы: новых {new_count}, обновленных {updated_count}\n\n'
            'Откройте «📅 Мое расписание», чтобы посмотреть детали.'
        )

        try:
            await bot.send_message(telegram_id, text, parse_mode='HTML')
            notified += 1
        except Exception as send_error:
            print(f'WARN: cannot notify user {telegram_id}: {send_error}')

    return notified


async def _schedule_monitor_loop():
    """Фоновый цикл мониторинга сайта расписаний и синхронизации БД."""
    # Небольшая задержка, чтобы бот полностью поднялся.
    await asyncio.sleep(10)

    while True:
        try:
            report = await asyncio.to_thread(check_for_schedule_updates)

            if report.get('errors'):
                for error_text in report['errors']:
                    print(f'[MONITOR][ERR] {error_text}')

            has_file_changes = bool(report.get('new_files') or report.get('updated_files'))
            if has_file_changes:
                old_snapshot = _build_group_day_snapshot()

                client.init_schedule()
                db_stats = await client.persist_schedule_to_db()

                new_snapshot = _build_group_day_snapshot()
                changed_group_days = _collect_changed_group_days(old_snapshot, new_snapshot)
                notified = await _notify_users_about_changes(changed_group_days, report)

                print(
                    '[MONITOR] updates: new={new_count}, updated={updated_count}, '
                    'groups_changed={groups_changed}, notified={notified}, db_lessons={db_lessons}'.format(
                        new_count=len(report.get('new_files', [])),
                        updated_count=len(report.get('updated_files', [])),
                        groups_changed=len(changed_group_days),
                        notified=notified,
                        db_lessons=db_stats.get('lessons', 0),
                    )
                )

        except asyncio.CancelledError:
            raise
        except Exception as loop_error:
            print(f'[MONITOR][ERR] loop failed: {loop_error}')

        await asyncio.sleep(POLL_SECONDS)


async def on_startup(dispatcher):
    """Инициализация БД, расписания и фонового мониторинга при запуске."""
    global monitor_task

    await create_base()
    setup_handlers(dispatcher)

    print('Загрузка расписания из Excel...')
    client.init_schedule()

    total_courses = len(client.schedule_data)
    total_groups = sum(len(groups) for groups in client.schedule_data.values())
    print(f'Загружено расписание: {total_courses} курсов, {total_groups} групп')

    try:
        db_stats = await client.persist_schedule_to_db()
        print(
            'Сохранено в БД: '
            f"периодов={db_stats['periods']}, курсов={db_stats['courses']}, "
            f"групп={db_stats['groups']}, недель={db_stats['weeks']}, "
            f"дней={db_stats['days']}, занятий={db_stats['lessons']}"
        )
    except Exception as db_error:
        print(f'⚠️ Ошибка сохранения расписания в БД: {db_error}')

    if total_groups == 0:
        print('⚠️ Внимание: расписание не загружено или файлы не найдены!')
    else:
        print('✅ Расписание успешно загружено!')

    if monitor_task is None or monitor_task.done():
        monitor_task = asyncio.create_task(_schedule_monitor_loop())
        print(f'✅ Монитор расписания запущен (интервал {POLL_SECONDS} сек)')

    print('Бот запущен!')


async def on_shutdown(dispatcher):
    """Останавливает фоновые задачи при завершении бота."""
    global monitor_task

    if monitor_task and not monitor_task.done():
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    executor.start_polling(dp, on_startup=on_startup, on_shutdown=on_shutdown)

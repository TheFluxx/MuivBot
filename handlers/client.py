import asyncio
import secrets
import os
import re
import html
from pathlib import Path
from datetime import datetime, timedelta
from aiogram import types, Dispatcher
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters import Text
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.utils.exceptions import MessageNotModified
import xlrd
from db_api import db_commands
from create_bot import bot
from data.config import ADMIN_LOGIN, ADMIN_PASSWORD, STAROSTA_LOGIN, STAROSTA_PASSWORD

# Создаем клавиатуру
def get_main_keyboard():
    """Создает основную клавиатуру бота."""
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(types.KeyboardButton("📅 Мое расписание"))
    kb.add(types.KeyboardButton("🔎 Поиск"))
    kb.add(types.KeyboardButton("🎉 События"))
    kb.add(types.KeyboardButton("💼 Настройки"))
    return kb


class SearchDialog(StatesGroup):
    """Состояния диалога поиска."""

    waiting_query = State()


class AdminAuthDialog(StatesGroup):
    """Состояния входа в админ-панель."""

    waiting_login = State()
    waiting_password = State()


class StarostaAuthDialog(StatesGroup):
    """Состояния входа в панель старосты."""

    waiting_login = State()
    waiting_password = State()


class AdminMessageDialog(StatesGroup):
    waiting_text = State()


class StarostaMessageDialog(StatesGroup):
    waiting_text = State()


class AdminEventDialog(StatesGroup):
    waiting_datetime = State()
    waiting_text = State()
    waiting_attachment = State()

async def cmd_start(message: types.Message):
    """Обрабатывает команду /start и регистрирует пользователя."""
    telegram_id = message.from_user.id
    username = message.from_user.username
    if username is None:
        username = message.from_user.first_name
    user_exists = await db_commands.registration_check(telegram_id)
    if not user_exists:
        try:
            referrer_id = int(message.get_args())
            await db_commands.register_user(telegram_id, username, referrer_id)
        except ValueError:
            await db_commands.register_user(telegram_id, username, 0)
        await message.reply(f"Привет, Вы успешно зарегистрированы.", reply_markup=get_main_keyboard())
    else:
        await message.reply("Вы уже зарегистрированы.", reply_markup=get_main_keyboard())


def _commands_help_text():
    """Возвращает справку по доступным командам бота."""
    return (
        "<b>📚 Команды бота</b>\n\n"
        "• <code>/start</code> - открыть главное меню\n"
        "• <code>/search</code> - открыть поиск по расписанию\n"
        "• <code>/group ИД 30.1/Б3-22</code> - выбрать группу\n"
        "• <code>/date 14.03.26</code> - открыть расписание на дату\n"
        "• <code>/teacher Простомолотов</code> - найти расписание преподавателя\n"
        "• <code>/room 505</code> - найти расписание аудитории\n"
        "• <code>/subject Эконометрика</code> - найти расписание предмета\n"
        "• <code>/today</code> - показать расписание на сегодня\n"
        "• <code>/tomorrow</code> - показать расписание на завтра\n"
        "• <code>/admin</code> - вход в админ-панель\n"
        "• <code>/starosta</code> - вход в панель старосты\n"
        "• <code>/help</code> - показать эту справку\n\n"
        "<b>Примеры</b>\n"
        "• <code>/group ИД 30.1/Б3-22</code>\n"
        "• <code>/date 14.03.26</code>\n"
        "• <code>/teacher Леденчук</code>\n"
        "• <code>/room ауд. 125</code>\n"
        "• <code>/subject Математические модели</code>\n"
        "• <code>/admin</code>\n"
        "• <code>/starosta</code>"
    )


async def cmd_help(message: types.Message):
    """Показывает пользователю список команд с примерами."""
    await message.answer(_commands_help_text(), reply_markup=get_main_keyboard(), parse_mode='HTML')


async def admin_command(message: types.Message, state: FSMContext):
    """Запускает вход в админ-панель или открывает ее для авторизованного пользователя."""
    await state.finish()

    if not _admin_panel_enabled():
        await message.answer(
            "⚠️ Админ-панель не настроена.\nДобавьте <code>ADMIN_LOGIN</code> и <code>ADMIN_PASSWORD</code> в .env.",
            parse_mode='HTML',
        )
        return

    telegram_id = _telegram_id_from_message(message)
    if telegram_id is not None and await db_commands.is_admin_authorized(telegram_id):
        await _render_admin_panel(message, telegram_id, edit=False)
        return

    await state.set_state(AdminAuthDialog.waiting_login.state)
    await message.answer(
        "🛠️ Вход в админ-панель\n\nВведите логин.\nДля отмены напишите <b>Отмена</b>.",
        parse_mode='HTML',
    )


async def admin_receive_login(message: types.Message, state: FSMContext):
    """Принимает логин для входа в админ-панель."""
    text_value = _normalize_cell_text(message.text)
    if not text_value:
        await message.answer('Введите логин.')
        return

    if _search_normalize_text(text_value) in {'отмена', 'cancel'}:
        await state.finish()
        await message.answer('Вход в админ-панель отменен.', reply_markup=get_main_keyboard())
        return

    await state.update_data(admin_login=text_value)
    await state.set_state(AdminAuthDialog.waiting_password.state)
    await message.answer(
        "🔒 Теперь введите пароль.\nДля отмены напишите <b>Отмена</b>.",
        parse_mode='HTML',
    )


async def admin_receive_password(message: types.Message, state: FSMContext):
    """Принимает пароль и завершает вход в админ-панель."""
    password_text = _normalize_cell_text(message.text)
    if not password_text:
        await message.answer('Введите пароль.')
        return

    if _search_normalize_text(password_text) in {'отмена', 'cancel'}:
        await state.finish()
        await message.answer('Вход в админ-панель отменен.', reply_markup=get_main_keyboard())
        return

    state_data = await state.get_data()
    login_text = state_data.get('admin_login', '')

    if not _is_admin_credentials_valid(login_text, password_text):
        await state.set_state(AdminAuthDialog.waiting_login.state)
        await state.update_data(admin_login=None)
        await message.answer(
            "⚠️ Неверный логин или пароль.\nВведите логин заново или напишите <b>Отмена</b>.",
            parse_mode='HTML',
        )
        return

    telegram_id = _telegram_id_from_message(message)
    await db_commands.authorize_admin(telegram_id, login_text)
    await state.finish()
    await message.answer('✅ Вход выполнен.')
    await _render_admin_panel(message, telegram_id, edit=False)


def _admin_panel_enabled():
    """Проверяет, настроена ли админ-панель через .env."""
    return bool(_normalize_cell_text(ADMIN_LOGIN) and _normalize_cell_text(ADMIN_PASSWORD))


def _is_admin_credentials_valid(login_text: str, password_text: str) -> bool:
    """Проверяет логин и пароль админ-панели."""
    normalized_login = _normalize_cell_text(login_text)
    normalized_password = _normalize_cell_text(password_text)
    return (
        secrets.compare_digest(normalized_login, _normalize_cell_text(ADMIN_LOGIN))
        and secrets.compare_digest(normalized_password, _normalize_cell_text(ADMIN_PASSWORD))
    )


def _starosta_panel_enabled():
    """Проверяет, настроен ли вход главной старосты через .env."""
    return bool(_normalize_cell_text(STAROSTA_LOGIN) and _normalize_cell_text(STAROSTA_PASSWORD))


def _is_starosta_credentials_valid(login_text: str, password_text: str) -> bool:
    """Проверяет логин и пароль главной старосты."""
    normalized_login = _normalize_cell_text(login_text)
    normalized_password = _normalize_cell_text(password_text)
    return (
        secrets.compare_digest(normalized_login, _normalize_cell_text(STAROSTA_LOGIN))
        and secrets.compare_digest(normalized_password, _normalize_cell_text(STAROSTA_PASSWORD))
    )


async def _resolve_starosta_context(state: FSMContext, telegram_id: int):
    """Возвращает права старосты, доступные группы и текущую выбранную группу."""
    access = await db_commands.get_starosta_access(telegram_id)
    if access.get('is_super'):
        available_groups = await db_commands.get_admin_user_group_filters()
    else:
        available_groups = sorted(
            {
                _normalize_cell_text(group)
                for group in access.get('groups') or []
                if _normalize_cell_text(group)
            },
            key=str.casefold,
        )

    user_data = await state.get_data()
    current_group = _normalize_cell_text(user_data.get('starosta_group_filter')) or None
    if current_group not in available_groups:
        current_group = available_groups[0] if available_groups else None

    try:
        current_page = int(user_data.get('starosta_users_page', 0))
    except (TypeError, ValueError):
        current_page = 0

    await state.update_data(
        starosta_group_filter=current_group,
        starosta_users_page=max(0, current_page),
    )
    return access, available_groups, current_group


def _build_starosta_panel_text(access: dict, current_group: str | None, students_count: int):
    """Формирует текст панели старосты."""
    groups = access.get('groups') or []
    role_text = 'Главная староста' if access.get('is_super') else 'Староста группы'
    groups_text = 'Все группы' if access.get('is_super') else (', '.join(groups) if groups else 'нет доступа')

    lines = [
        '<b>⭐ Панель старосты</b>',
        '',
        f"👤 Роль: <b>{html.escape(role_text)}</b>",
        f"👥 Текущая группа: <b>{html.escape(current_group or 'не выбрана')}</b>",
        f"📚 Доступные группы: <b>{html.escape(groups_text)}</b>",
        f"👨‍🎓 Пользователей в группе: <b>{students_count}</b>",
    ]
    return '\n'.join(lines)


def _build_starosta_panel_keyboard(current_group: str | None, can_switch_groups: bool, has_group: bool):
    """Строит клавиатуру панели старосты."""
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    if has_group:
        keyboard.add(
            types.InlineKeyboardButton(
                text=f"👥 {current_group}" if current_group else '👥 Выбрать группу',
                callback_data='starosta_group_pick_0' if can_switch_groups else 'starosta_noop',
            )
        )
        keyboard.row(
            types.InlineKeyboardButton(text='👨‍🎓 Ученики', callback_data='starosta_users'),
            types.InlineKeyboardButton(text='📣 Написать группе', callback_data='starosta_group_message'),
        )
    keyboard.row(
        types.InlineKeyboardButton(text='🔄 Обновить', callback_data='starosta_open'),
        types.InlineKeyboardButton(text='🚪 Выйти', callback_data='starosta_logout'),
    )
    keyboard.add(types.InlineKeyboardButton(text='🏠 В главное меню', callback_data='main_menu'))
    return keyboard


def _build_starosta_users_text(page_data: dict, group_label: str | None):
    """Формирует текст списка учеников для панели старосты."""
    current_page = page_data.get('page', 0) + 1
    total_pages = page_data.get('total_pages', 1)
    lines = [
        '<b>👨‍🎓 Ученики группы</b>',
        '',
        f"👥 Группа: <b>{html.escape(group_label or 'не выбрана')}</b>",
        f"📄 Страница: <b>{current_page}/{total_pages}</b> • Найдено: <b>{page_data.get('total', 0)}</b>",
        '',
    ]

    if not (page_data.get('items') or []):
        lines.append('По выбранной группе пользователи не найдены.')
    else:
        lines.append('Нажмите на ученика ниже, чтобы открыть карточку.')

    return '\n'.join(lines)


def _build_starosta_users_keyboard(page_data: dict, current_group: str | None, can_switch_groups: bool):
    """Строит клавиатуру списка учеников для панели старосты."""
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    if current_group:
        keyboard.row(
            types.InlineKeyboardButton(
                text=f"👥 {current_group}",
                callback_data='starosta_group_pick_0' if can_switch_groups else 'starosta_noop',
            ),
            types.InlineKeyboardButton(text='📣 Написать группе', callback_data='starosta_users_broadcast'),
        )

    for item in page_data.get('items') or []:
        keyboard.add(
            types.InlineKeyboardButton(
                text=_build_admin_user_button_text(item),
                callback_data=f"starosta_users_info_{item.get('telegram_id')}",
            )
        )

    total_pages = max(1, int(page_data.get('total_pages', 1)))
    current_page = max(0, int(page_data.get('page', 0)))
    if total_pages > 1:
        keyboard.row(
            types.InlineKeyboardButton(
                text='⬅️',
                callback_data='starosta_users_page_-1' if current_page > 0 else 'starosta_noop',
            ),
            types.InlineKeyboardButton(
                text=f'📄 Стр. {current_page + 1}/{total_pages}',
                callback_data='starosta_noop',
            ),
            types.InlineKeyboardButton(
                text='➡️',
                callback_data='starosta_users_page_1' if current_page < total_pages - 1 else 'starosta_noop',
            ),
        )

    keyboard.row(
        types.InlineKeyboardButton(text='🔄 Обновить список', callback_data='starosta_users'),
        types.InlineKeyboardButton(text='⬅️ В панель старосты', callback_data='starosta_open'),
    )
    return keyboard


def _build_starosta_user_details_keyboard(target_telegram_id: int):
    """Строит клавиатуру карточки ученика в панели старосты."""
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        types.InlineKeyboardButton(
            text='✉️ Написать ученику',
            callback_data=f'starosta_users_message_{target_telegram_id}',
        )
    )
    keyboard.add(types.InlineKeyboardButton(text='⬅️ К списку учеников', callback_data='starosta_users_back'))
    keyboard.row(
        types.InlineKeyboardButton(text='🔄 Обновить список', callback_data='starosta_users'),
        types.InlineKeyboardButton(text='⬅️ В панель старосты', callback_data='starosta_open'),
    )
    return keyboard


def _build_starosta_message_compose_text(
    mode: str,
    *,
    target_label: str | None = None,
    recipients_count: int | None = None,
    group_label: str | None = None,
):
    """Формирует экран ввода сообщения от старосты."""
    if mode == 'single':
        return '\n'.join(
            [
                '<b>✉️ Сообщение ученику</b>',
                '',
                f"Получатель: <b>{html.escape(target_label or 'неизвестно')}</b>",
                '',
                'Отправьте следующим сообщением текст.',
                'Для отмены нажмите кнопку ниже или напишите «Отмена».',
            ]
        )

    return '\n'.join(
        [
            '<b>📣 Сообщение группе</b>',
            '',
            f"👥 Группа: <b>{html.escape(group_label or 'не выбрана')}</b>",
            f"👤 Получателей: <b>{int(recipients_count or 0)}</b>",
            '',
            'Отправьте следующим сообщением текст рассылки.',
            'Для отмены нажмите кнопку ниже или напишите «Отмена».',
        ]
    )


def _build_starosta_message_compose_keyboard():
    """Строит клавиатуру экрана ввода сообщения от старосты."""
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(types.InlineKeyboardButton(text='⬅️ Отмена', callback_data='starosta_message_cancel'))
    return keyboard


def _build_admin_panel_keyboard():
    """Строит клавиатуру админ-панели."""
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.row(
        types.InlineKeyboardButton(text='👥 Пользователи', callback_data='admin_users'),
        types.InlineKeyboardButton(text='📣 Создать событие', callback_data='admin_event_create'),
    )
    keyboard.row(
        types.InlineKeyboardButton(text='🔄 Обновить', callback_data='admin_refresh'),
        types.InlineKeyboardButton(text='🚪 Выйти', callback_data='admin_logout'),
    )
    keyboard.add(types.InlineKeyboardButton(text='🏠 В главное меню', callback_data='main_menu'))
    return keyboard


def _build_admin_panel_text(stats: dict, session_data: dict | None):
    """Формирует текст админ-панели."""
    login_value = html.escape(_normalize_cell_text((session_data or {}).get('login')) or 'неизвестно')
    authorized_at = (session_data or {}).get('authorized_at')
    if authorized_at:
        authorized_text = authorized_at.strftime('%d.%m.%Y %H:%M:%S')
    else:
        authorized_text = 'неизвестно'

    lines = [
        '<b>🛠️ Админ-панель</b>',
        '',
        f'👤 Логин: <b>{login_value}</b>',
        f'🕒 Вход выполнен: <b>{authorized_text}</b>',
        '',
        '<b>📊 Статистика</b>',
        f"• Пользователей: <b>{stats.get('total_users', 0)}</b>",
        f"• С выбранной группой: <b>{stats.get('users_with_group', 0)}</b>",
        f"• С включенной рассылкой: <b>{stats.get('users_with_digest', 0)}</b>",
        f"• Активных админ-сессий: <b>{stats.get('active_admins', 0)}</b>",
        f"• Событий в базе: <b>{stats.get('events_count', 0)}</b>",
        '',
        '<b>📚 Расписание</b>',
        f"• Периодов: <b>{stats.get('periods_count', 0)}</b>",
        f"• Курсов: <b>{stats.get('courses_count', 0)}</b>",
        f"• Групп: <b>{stats.get('groups_count', 0)}</b>",
        f"• Недель: <b>{stats.get('weeks_count', 0)}</b>",
        f"• Дней: <b>{stats.get('days_count', 0)}</b>",
        f"• Занятий: <b>{stats.get('lessons_count', 0)}</b>",
    ]
    return '\n'.join(lines)


async def _render_admin_panel(target, telegram_id, *, edit: bool):
    """Показывает экран админ-панели."""
    if not await db_commands.is_admin_authorized(telegram_id):
        if edit:
            await target.answer('⚠️ Сначала войдите в админ-панель через /admin', show_alert=True)
        else:
            await target.answer('⚠️ Сначала войдите в админ-панель через /admin')
        return False

    stats = await db_commands.get_admin_dashboard_stats()
    session_data = await db_commands.get_admin_session(telegram_id)
    text = _build_admin_panel_text(stats, session_data)
    keyboard = _build_admin_panel_keyboard()

    if edit:
        await _safe_edit_text(target.message, text, reply_markup=keyboard, parse_mode='HTML')
        await target.answer()
    else:
        await target.answer(text, reply_markup=keyboard, parse_mode='HTML')

    return True


def _starosta_can_access_group(access: dict, group_code: str | None):
    """Проверяет, доступна ли группа текущей старосте."""
    normalized_group = _normalize_cell_text(group_code) or None
    if access.get('is_super'):
        return True
    return bool(normalized_group and normalized_group in (access.get('groups') or []))


async def _render_starosta_panel(target, state: FSMContext, telegram_id: int, *, edit: bool):
    """Показывает панель старосты."""
    access, available_groups, current_group = await _resolve_starosta_context(state, telegram_id)
    if not access.get('has_access'):
        if edit:
            await target.answer('⚠️ У вас нет доступа к панели старосты', show_alert=True)
        else:
            await target.answer('⚠️ У вас нет доступа к панели старосты')
        return False

    recipients = []
    if current_group:
        recipients = await db_commands.get_admin_message_recipients(
            group_code=current_group,
            allowed_group_codes=None if access.get('is_super') else available_groups,
        )

    text = _build_starosta_panel_text(access, current_group, len(recipients))
    keyboard = _build_starosta_panel_keyboard(
        current_group,
        can_switch_groups=bool(access.get('is_super') or len(available_groups) > 1),
        has_group=bool(current_group),
    )

    if edit:
        await _safe_edit_text(target.message, text, reply_markup=keyboard, parse_mode='HTML')
        await target.answer()
    else:
        await target.answer(text, reply_markup=keyboard, parse_mode='HTML')
    return True


async def starosta_command(message: types.Message, state: FSMContext):
    """Открывает панель старосты или запускает вход главной старосты."""
    await state.finish()
    telegram_id = _telegram_id_from_message(message)
    access = await db_commands.get_starosta_access(telegram_id)
    if access.get('has_access'):
        await _render_starosta_panel(message, state, telegram_id, edit=False)
        return

    if not _starosta_panel_enabled():
        await message.answer('⚠️ Вас не назначили старостой, а вход главной старосты не настроен.')
        return

    await state.set_state(StarostaAuthDialog.waiting_login.state)
    await message.answer(
        "⭐ Вход в панель старосты\n\nВведите логин.\nДля отмены напишите <b>Отмена</b>.",
        parse_mode='HTML',
    )


async def starosta_receive_login(message: types.Message, state: FSMContext):
    """Принимает логин главной старосты."""
    text_value = _normalize_cell_text(message.text)
    if not text_value:
        await message.answer('Введите логин.')
        return

    if _search_normalize_text(text_value) in {'отмена', 'cancel'}:
        await state.finish()
        await message.answer('Вход в панель старосты отменен.', reply_markup=get_main_keyboard())
        return

    await state.update_data(starosta_login=text_value)
    await state.set_state(StarostaAuthDialog.waiting_password.state)
    await message.answer(
        "🔒 Теперь введите пароль.\nДля отмены напишите <b>Отмена</b>.",
        parse_mode='HTML',
    )


async def starosta_receive_password(message: types.Message, state: FSMContext):
    """Принимает пароль и выполняет вход главной старосты."""
    password_text = _normalize_cell_text(message.text)
    if not password_text:
        await message.answer('Введите пароль.')
        return

    if _search_normalize_text(password_text) in {'отмена', 'cancel'}:
        await state.finish()
        await message.answer('Вход в панель старосты отменен.', reply_markup=get_main_keyboard())
        return

    state_data = await state.get_data()
    login_text = state_data.get('starosta_login', '')

    if not _is_starosta_credentials_valid(login_text, password_text):
        await state.set_state(StarostaAuthDialog.waiting_login.state)
        await state.update_data(starosta_login=None)
        await message.answer(
            "⚠️ Неверный логин или пароль.\nВведите логин заново или напишите <b>Отмена</b>.",
            parse_mode='HTML',
        )
        return

    telegram_id = _telegram_id_from_message(message)
    await db_commands.authorize_starosta(telegram_id, login_text)
    await state.finish()
    await message.answer('✅ Вход выполнен.')
    await _render_starosta_panel(message, state, telegram_id, edit=False)


async def starosta_open(callback_query: types.CallbackQuery, state: FSMContext):
    """Открывает панель старосты по callback."""
    await _render_starosta_panel(callback_query, state, _telegram_id_from_callback(callback_query), edit=True)


async def starosta_logout(callback_query: types.CallbackQuery, state: FSMContext):
    """Выходит из панели старосты."""
    telegram_id = _telegram_id_from_callback(callback_query)
    if telegram_id is not None:
        await db_commands.revoke_starosta(telegram_id)

    await state.finish()
    await _safe_edit_text(
        callback_query.message,
        "🔒 Вы вышли из панели старосты.",
        reply_markup=types.InlineKeyboardMarkup().add(
            types.InlineKeyboardButton(text='🏠 В главное меню', callback_data='main_menu')
        ),
    )
    await callback_query.answer()


def _build_starosta_group_picker_keyboard(groups: list[str], current_group: str | None, page: int):
    """Строит клавиатуру выбора группы в панели старосты."""
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    total_pages = max(1, (len(groups) + ADMIN_FILTER_PAGE_SIZE - 1) // ADMIN_FILTER_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * ADMIN_FILTER_PAGE_SIZE
    end = start + ADMIN_FILTER_PAGE_SIZE

    for absolute_index in range(start, min(end, len(groups))):
        group_code = groups[absolute_index]
        marker = '✅ ' if group_code == current_group else ''
        keyboard.add(
            types.InlineKeyboardButton(
                text=f'{marker}{group_code}',
                callback_data=f'starosta_group_set_{absolute_index}',
            )
        )

    if total_pages > 1:
        keyboard.row(
            types.InlineKeyboardButton(
                text='⬅️',
                callback_data=f'starosta_group_pick_{page - 1}' if page > 0 else 'starosta_noop',
            ),
            types.InlineKeyboardButton(text=f'📄 {page + 1}/{total_pages}', callback_data='starosta_noop'),
            types.InlineKeyboardButton(
                text='➡️',
                callback_data=f'starosta_group_pick_{page + 1}' if page < total_pages - 1 else 'starosta_noop',
            ),
        )

    keyboard.add(types.InlineKeyboardButton(text='⬅️ В панель старосты', callback_data='starosta_open'))
    return keyboard


async def starosta_pick_group(callback_query: types.CallbackQuery, state: FSMContext):
    """Показывает выбор группы для панели старосты."""
    telegram_id = _telegram_id_from_callback(callback_query)
    access, available_groups, current_group = await _resolve_starosta_context(state, telegram_id)
    if not access.get('has_access'):
        await callback_query.answer('⚠️ У вас нет доступа к панели старосты', show_alert=True)
        return

    try:
        page = int(callback_query.data.rsplit('_', 1)[1])
    except (TypeError, ValueError, IndexError):
        page = 0

    if not available_groups:
        await callback_query.answer('Для вас пока не назначены группы.', show_alert=True)
        return

    text = (
        '<b>👥 Выбор группы</b>\n\n'
        f"Текущая группа: <b>{html.escape(current_group or 'не выбрана')}</b>\n"
        'Выберите группу из списка ниже.'
    )
    keyboard = _build_starosta_group_picker_keyboard(available_groups, current_group, page)
    await _safe_edit_text(callback_query.message, text, reply_markup=keyboard, parse_mode='HTML')
    await callback_query.answer()


async def starosta_set_group(callback_query: types.CallbackQuery, state: FSMContext):
    """Сохраняет выбранную группу в панели старосты."""
    telegram_id = _telegram_id_from_callback(callback_query)
    access, available_groups, _ = await _resolve_starosta_context(state, telegram_id)
    if not access.get('has_access'):
        await callback_query.answer('⚠️ У вас нет доступа к панели старосты', show_alert=True)
        return

    try:
        index = int(callback_query.data.rsplit('_', 1)[1])
    except (TypeError, ValueError, IndexError):
        await callback_query.answer('⚠️ Некорректная группа', show_alert=True)
        return

    if index < 0 or index >= len(available_groups):
        await callback_query.answer('⚠️ Группа не найдена', show_alert=True)
        return

    await state.update_data(
        starosta_group_filter=available_groups[index],
        starosta_users_page=0,
    )
    await _render_starosta_panel(callback_query, state, telegram_id, edit=True)


async def _build_starosta_users_view_payload(state: FSMContext, telegram_id: int):
    """Собирает экран списка учеников для панели старосты."""
    access, available_groups, current_group = await _resolve_starosta_context(state, telegram_id)
    if not access.get('has_access'):
        return None, None

    user_data = await state.get_data()
    try:
        page = int(user_data.get('starosta_users_page', 0))
    except (TypeError, ValueError):
        page = 0

    page_data = await db_commands.get_admin_users_page(
        group_code=current_group,
        page=page,
        page_size=ADMIN_USERS_PAGE_SIZE,
        allowed_group_codes=None if access.get('is_super') else available_groups,
    )
    if page_data.get('page', 0) != page:
        page = page_data['page']
        await state.update_data(starosta_users_page=page)

    text = _build_starosta_users_text(page_data, current_group)
    keyboard = _build_starosta_users_keyboard(
        page_data,
        current_group,
        can_switch_groups=bool(access.get('is_super') or len(available_groups) > 1),
    )
    return text, keyboard


async def _render_starosta_users_view(target, state: FSMContext, telegram_id: int, *, edit: bool):
    """Показывает список учеников в панели старосты."""
    access = await db_commands.get_starosta_access(telegram_id)
    if not access.get('has_access'):
        if edit:
            await target.answer('⚠️ У вас нет доступа к панели старосты', show_alert=True)
        else:
            await target.answer('⚠️ У вас нет доступа к панели старосты')
        return False

    text, keyboard = await _build_starosta_users_view_payload(state, telegram_id)
    if text is None:
        await target.answer('⚠️ Нет доступных групп', show_alert=True)
        return False

    if edit:
        await _safe_edit_text(target.message, text, reply_markup=keyboard, parse_mode='HTML')
        await target.answer()
    else:
        await target.answer(text, reply_markup=keyboard, parse_mode='HTML')
    return True


def _starosta_can_view_user(access: dict, user_details: dict):
    """Проверяет, доступен ли пользователь старосте."""
    if access.get('is_super'):
        return True
    group_code = _normalize_cell_text(user_details.get('selected_group')) or None
    return bool(group_code and group_code in (access.get('groups') or []))


async def _render_starosta_user_details_view(target, telegram_id: int, target_telegram_id: int, *, edit: bool):
    """Показывает карточку ученика в панели старосты."""
    access = await db_commands.get_starosta_access(telegram_id)
    if not access.get('has_access'):
        if edit:
            await target.answer('⚠️ У вас нет доступа к панели старосты', show_alert=True)
        else:
            await target.answer('⚠️ У вас нет доступа к панели старосты')
        return False

    user_details = await db_commands.get_admin_user_details(target_telegram_id)
    if not user_details or not _starosta_can_view_user(access, user_details):
        if edit:
            await target.answer('⚠️ Ученик недоступен', show_alert=True)
        else:
            await target.answer('⚠️ Ученик недоступен')
        return False

    text = _build_admin_user_details_text(user_details)
    keyboard = _build_starosta_user_details_keyboard(target_telegram_id)

    if edit:
        await _safe_edit_text(target.message, text, reply_markup=keyboard, parse_mode='HTML')
        await target.answer()
    else:
        await target.answer(text, reply_markup=keyboard, parse_mode='HTML')
    return True


async def admin_open(callback_query: types.CallbackQuery, state: FSMContext):
    """Открывает админ-панель по callback-кнопке."""
    await _render_admin_panel(callback_query, _telegram_id_from_callback(callback_query), edit=True)


async def admin_refresh(callback_query: types.CallbackQuery, state: FSMContext):
    """Обновляет данные на экране админ-панели."""
    await _render_admin_panel(callback_query, _telegram_id_from_callback(callback_query), edit=True)


async def admin_logout(callback_query: types.CallbackQuery, state: FSMContext):
    """Завершает админ-сессию пользователя."""
    telegram_id = _telegram_id_from_callback(callback_query)
    if telegram_id is not None:
        await db_commands.revoke_admin(telegram_id)

    await state.finish()
    await _safe_edit_text(
        callback_query.message,
        "🔒 Вы вышли из админ-панели.",
        reply_markup=types.InlineKeyboardMarkup().add(
            types.InlineKeyboardButton(text='🏠 В главное меню', callback_data='main_menu')
        ),
    )
    await callback_query.answer()


def _get_admin_users_view_state(user_data):
    """Возвращает сохраненные фильтры и страницу списка пользователей."""
    course_filter = _normalize_cell_text(user_data.get('admin_users_course_filter')) or None
    group_filter = _normalize_cell_text(user_data.get('admin_users_group_filter')) or None
    starosta_only = bool(user_data.get('admin_users_starosta_only'))

    try:
        page = int(user_data.get('admin_users_page', 0))
    except (TypeError, ValueError):
        page = 0

    return course_filter, group_filter, max(0, page), starosta_only


def _short_admin_button_text(prefix: str, value: str, fallback: str, limit: int = 24):
    """Собирает короткую подпись для inline-кнопки админ-фильтра."""
    label = _normalize_cell_text(value) or fallback
    if len(label) > limit:
        label = f'{label[: limit - 1].rstrip()}…'
    return f'{prefix} {label}'


def _build_admin_user_button_text(item: dict):
    """Собирает читаемую подпись кнопки пользователя."""
    username = _normalize_cell_text(item.get('username'))
    primary = f'@{username}' if username else f"ID {item.get('telegram_id')}"
    group_code = _normalize_cell_text(item.get('selected_group')) or 'без группы'
    label = f'👤 {primary} • {group_code}'
    if len(label) > 46:
        label = f'{label[:45].rstrip()}…'
    return label


def _build_admin_user_details_text(user_details: dict):
    """Формирует текст карточки пользователя для админ-панели."""
    username = _normalize_cell_text(user_details.get('username'))
    username_text = f'@{username}' if username else 'не указан'
    course_text = _normalize_cell_text(user_details.get('selected_course_name')) or 'не выбран'
    group_text = _normalize_cell_text(user_details.get('selected_group')) or 'не выбрана'

    digest_enabled = bool(user_details.get('daily_digest_enabled'))
    if digest_enabled:
        digest_text = (
            f"включена ✅ {int(user_details.get('daily_digest_hour', 0)):02d}:"
            f"{int(user_details.get('daily_digest_minute', 0)):02d}"
        )
    else:
        digest_text = 'выключена ❌'

    event_notifications_enabled = user_details.get('event_notifications_enabled')
    event_notifications_text = 'включены ✅' if event_notifications_enabled is not False else 'выключены ❌'

    referrer_id = user_details.get('referrer_id')
    if referrer_id in (None, 0):
        referrer_text = 'нет'
    else:
        referrer_text = str(referrer_id)

    updated_at = user_details.get('updated_at')
    updated_text = updated_at.strftime('%d.%m.%Y %H:%M') if updated_at else 'нет данных'
    starosta_groups = [
        _normalize_cell_text(group)
        for group in (user_details.get('starosta_groups') or [])
        if _normalize_cell_text(group)
    ]
    starosta_text = ', '.join(starosta_groups) if starosta_groups else 'не назначен'

    lines = [
        '<b>👤 Пользователь</b>',
        '',
        f"🆔 Telegram ID: <code>{user_details.get('telegram_id')}</code>",
        f"👤 Username: <b>{html.escape(username_text)}</b>",
        f"🎓 Курс: <b>{html.escape(course_text)}</b>",
        f"👥 Группа: <b>{html.escape(group_text)}</b>",
        f"🔔 Рассылка: <b>{html.escape(digest_text)}</b>",
        f"🎉 События: <b>{html.escape(event_notifications_text)}</b>",
        f"⭐ Староста групп: <b>{html.escape(starosta_text)}</b>",
        f"🔗 Реферал: <b>{html.escape(referrer_text)}</b>",
        f"🕒 Обновлено: <b>{html.escape(updated_text)}</b>",
    ]
    return '\n'.join(lines)


def _build_admin_user_details_keyboard(target_telegram_id: int, user_details: dict | None = None):
    """Строит клавиатуру карточки пользователя."""
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        types.InlineKeyboardButton(
            text='✉️ Написать пользователю',
            callback_data=f'admin_users_message_{target_telegram_id}',
        )
    )
    if user_details is not None:
        selected_group = _normalize_cell_text(user_details.get('selected_group')) or None
        starosta_groups = {
            _normalize_cell_text(group)
            for group in (user_details.get('starosta_groups') or [])
            if _normalize_cell_text(group)
        }
        if selected_group:
            is_group_starosta = selected_group in starosta_groups
            keyboard.add(
                types.InlineKeyboardButton(
                    text='❌ Снять старосту группы' if is_group_starosta else '⭐ Назначить старостой группы',
                    callback_data=f'admin_users_starosta_toggle_{target_telegram_id}',
                )
            )
    keyboard.add(types.InlineKeyboardButton(text='⬅️ К списку пользователей', callback_data='admin_users_back'))
    keyboard.row(
        types.InlineKeyboardButton(text='🔄 Обновить список', callback_data='admin_users'),
        types.InlineKeyboardButton(text='⬅️ В админку', callback_data='admin_open'),
    )
    return keyboard


def _build_admin_message_target_label(user_details: dict):
    """Возвращает короткое имя получателя для экрана отправки сообщения."""
    username = _normalize_cell_text(user_details.get('username'))
    if username:
        return f'@{username}'
    return f"ID {user_details.get('telegram_id')}"


def _build_admin_message_compose_text(
    mode: str,
    *,
    target_label: str | None = None,
    recipients_count: int | None = None,
    course_label: str | None = None,
    group_label: str | None = None,
):
    """Формирует экран ввода текста для админ-сообщения."""
    if mode == 'single':
        return '\n'.join(
            [
                '<b>✉️ Сообщение пользователю</b>',
                '',
                f"Получатель: <b>{html.escape(target_label or 'неизвестно')}</b>",
                '',
                'Отправьте следующим сообщением текст.',
                'Для отмены нажмите кнопку ниже или напишите «Отмена».',
            ]
        )

    return '\n'.join(
        [
            '<b>📣 Сообщение по фильтру</b>',
            '',
            f"🎓 Курс: <b>{html.escape(course_label or 'Все курсы')}</b>",
            f"👥 Группа: <b>{html.escape(group_label or 'Все группы')}</b>",
            f"👤 Получателей: <b>{int(recipients_count or 0)}</b>",
            '',
            'Отправьте следующим сообщением текст рассылки.',
            'Для отмены нажмите кнопку ниже или напишите «Отмена».',
        ]
    )


def _build_admin_message_compose_keyboard():
    """Строит клавиатуру экрана ввода админ-сообщения."""
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(types.InlineKeyboardButton(text='⬅️ Отмена', callback_data='admin_message_cancel'))
    return keyboard


def _build_admin_event_datetime_text():
    """Формирует экран ввода даты и времени события."""
    return (
        '<b>📣 Новое событие</b>\n\n'
        'Введите дату и время события.\n'
        'Поддерживаются форматы:\n'
        '• <code>14.03.2026 18:30</code>\n'
        '• <code>14.03.26 18:30</code>\n'
        '• <code>2026-03-14 18:30</code>'
    )


def _build_admin_event_text_prompt(event_at: datetime):
    """Формирует экран ввода текста события."""
    return (
        '<b>📝 Текст события</b>\n\n'
        f"Дата и время: <b>{html.escape(_format_event_datetime(event_at))}</b>\n\n"
        'Отправьте текст события следующим сообщением.\n'
        'Для отмены нажмите кнопку ниже или напишите «Отмена».'
    )


def _build_admin_event_attachment_prompt(event_at: datetime, description: str):
    """Формирует экран ввода вложения события."""
    preview = _normalize_cell_text(description).replace('\n', ' ').strip()
    if len(preview) > 100:
        preview = f'{preview[:99].rstrip()}…'

    return (
        '<b>📎 Вложение к событию</b>\n\n'
        f"Дата и время: <b>{html.escape(_format_event_datetime(event_at))}</b>\n"
        f"Текст: <b>{html.escape(preview or 'без текста')}</b>\n\n"
        'Теперь можно отправить одно вложение:\n'
        '• фото\n'
        '• видео\n'
        '• GIF\n'
        '• документ\n'
        '• аудио\n'
        '• локацию\n\n'
        'Если вложение не нужно, нажмите кнопку ниже.'
    )


def _build_admin_event_step_keyboard(*, allow_skip: bool = False):
    """Строит клавиатуру шагов создания события."""
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    if allow_skip:
        keyboard.add(types.InlineKeyboardButton(text='✅ Без вложения', callback_data='admin_event_skip'))
    keyboard.add(types.InlineKeyboardButton(text='⬅️ Отмена', callback_data='admin_event_cancel'))
    return keyboard


def _build_admin_users_text(page_data: dict, course_label: str | None, group_label: str | None, starosta_only: bool):
    """Формирует текст экрана списка пользователей."""
    current_page = page_data.get('page', 0) + 1
    total_pages = page_data.get('total_pages', 1)
    lines = [
        '<b>👥 Пользователи</b>',
        '',
        f"🎓 Курс: <b>{html.escape(course_label or 'Все курсы')}</b>",
        f"👥 Группа: <b>{html.escape(group_label or 'Все группы')}</b>",
        f"⭐ Режим: <b>{'Только старосты' if starosta_only else 'Все пользователи'}</b>",
        '',
        (
            f"📄 Страница: <b>{current_page}/{total_pages}</b>"
            f" • Найдено: <b>{page_data.get('total', 0)}</b>"
        ),
        '',
        'Нажмите на пользователя ниже, чтобы увидеть подробности.',
        '',
    ]

    items = page_data.get('items') or []
    if not items:
        lines.append('Пользователи по выбранным фильтрам не найдены.')
        return '\n'.join(lines)

    return '\n'.join(lines).rstrip()


def _build_admin_users_keyboard(page_data: dict, course_label: str | None, group_label: str | None, starosta_only: bool):
    """Строит клавиатуру экрана списка пользователей."""
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.row(
        types.InlineKeyboardButton(
            text=_short_admin_button_text('🎓', course_label, 'Все курсы'),
            callback_data='admin_users_coursepick_0',
        ),
        types.InlineKeyboardButton(
            text=_short_admin_button_text('👥', group_label, 'Все группы'),
            callback_data='admin_users_grouppick_0',
        ),
    )
    keyboard.add(
        types.InlineKeyboardButton(
            text='⭐ Только старосты' if not starosta_only else '👥 Все пользователи',
            callback_data='admin_users_starosta_toggle',
        )
    )

    if course_label or group_label:
        keyboard.add(types.InlineKeyboardButton(text='🧹 Сбросить фильтры', callback_data='admin_users_reset'))

    if int(page_data.get('total', 0) or 0) > 0:
        keyboard.add(
            types.InlineKeyboardButton(
                text='📣 Написать по фильтру',
                callback_data='admin_users_broadcast',
            )
        )

    for item in page_data.get('items') or []:
        keyboard.add(
            types.InlineKeyboardButton(
                text=_build_admin_user_button_text(item),
                callback_data=f"admin_users_info_{item.get('telegram_id')}",
            )
        )

    total_pages = max(1, int(page_data.get('total_pages', 1)))
    current_page = max(0, int(page_data.get('page', 0)))
    if total_pages > 1:
        keyboard.row(
            types.InlineKeyboardButton(
                text='⬅️',
                callback_data=f'admin_users_page_{-1}' if current_page > 0 else 'admin_users_noop',
            ),
            types.InlineKeyboardButton(
                text=f'📄 Стр. {current_page + 1}/{total_pages}',
                callback_data='admin_users_noop',
            ),
            types.InlineKeyboardButton(
                text='➡️',
                callback_data=f'admin_users_page_{1}' if current_page < total_pages - 1 else 'admin_users_noop',
            ),
        )

    keyboard.row(
        types.InlineKeyboardButton(text='🔄 Обновить список', callback_data='admin_users'),
        types.InlineKeyboardButton(text='⬅️ В админку', callback_data='admin_open'),
    )
    return keyboard


def _build_admin_filter_keyboard(options, selected_value, prefix: str, page: int, clear_callback: str, back_callback: str):
    """Строит клавиатуру выбора фильтра админ-списка."""
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    total_items = len(options)
    total_pages = max(1, (total_items + ADMIN_FILTER_PAGE_SIZE - 1) // ADMIN_FILTER_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * ADMIN_FILTER_PAGE_SIZE
    end = start + ADMIN_FILTER_PAGE_SIZE

    for absolute_index in range(start, min(end, total_items)):
        option = options[absolute_index]
        option_value = option['value'] if isinstance(option, dict) else option
        option_label = option['label'] if isinstance(option, dict) else option
        marker = '✅ ' if option_value == selected_value else ''
        keyboard.add(
            types.InlineKeyboardButton(
                text=f'{marker}{option_label}',
                callback_data=f'{prefix}_{absolute_index}',
            )
        )

    keyboard.add(types.InlineKeyboardButton(text='♻️ Сбросить фильтр', callback_data=clear_callback))

    if total_pages > 1:
        keyboard.row(
            types.InlineKeyboardButton(
                text='⬅️',
                callback_data=f'{back_callback}_{page - 1}' if page > 0 else 'admin_users_noop',
            ),
            types.InlineKeyboardButton(
                text=f'📄 {page + 1}/{total_pages}',
                callback_data='admin_users_noop',
            ),
            types.InlineKeyboardButton(
                text='➡️',
                callback_data=f'{back_callback}_{page + 1}' if page < total_pages - 1 else 'admin_users_noop',
            ),
        )

    keyboard.add(types.InlineKeyboardButton(text='⬅️ К списку', callback_data='admin_users'))
    return keyboard


def _admin_message_is_cancel(text_value: str) -> bool:
    """Проверяет, что администратор отменил ввод сообщения."""
    return _search_normalize_text(text_value) in {'отмена', 'cancel'}


async def _safe_edit_message_text_by_id(
    chat_id: int,
    message_id: int,
    text: str,
    reply_markup=None,
    parse_mode: str | None = None,
):
    """Безопасно редактирует сообщение по chat_id/message_id."""
    try:
        await bot.edit_message_text(
            text=text,
            chat_id=chat_id,
            message_id=message_id,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
    except MessageNotModified:
        pass


def _event_weekday_name(event_at: datetime | None):
    """Возвращает русское название дня недели для события."""
    if not isinstance(event_at, datetime):
        return 'Неизвестно'

    weekday_index = event_at.weekday()
    if 0 <= weekday_index < len(EVENT_WEEKDAY_NAMES):
        return EVENT_WEEKDAY_NAMES[weekday_index].capitalize()
    return 'Неизвестно'


def _format_event_datetime(event_at: datetime | None):
    """Форматирует дату и время события для интерфейса."""
    if not isinstance(event_at, datetime):
        return 'не указано'
    return f"{event_at.strftime('%d.%m.%Y %H:%M')} ({_event_weekday_name(event_at)})"


def _parse_admin_event_datetime(text_value: str):
    """Разбирает дату и время события из текста администратора."""
    normalized = _normalize_cell_text(text_value)
    if not normalized:
        return None

    formats = (
        '%d.%m.%Y %H:%M',
        '%d.%m.%y %H:%M',
        '%Y-%m-%d %H:%M',
        '%d.%m.%Y %H.%M',
        '%d.%m.%y %H.%M',
    )
    for fmt in formats:
        try:
            return datetime.strptime(normalized, fmt)
        except ValueError:
            continue
    return None


def _event_title_from_description(description: str):
    """Строит короткий заголовок события по его описанию."""
    normalized = str(description or '').strip()
    if not normalized:
        return 'Событие'

    for line in normalized.splitlines():
        line = line.strip()
        if line:
            return line[:255]
    return normalized[:255] or 'Событие'


def _event_is_finished(event: dict):
    """Проверяет, завершилось ли событие."""
    event_at = event.get('event_at')
    return isinstance(event_at, datetime) and event_at < datetime.now()


def _event_attachment_label(attachment_type: str | None):
    """Возвращает человекочитаемое название вложения события."""
    labels = {
        'photo': 'Фото',
        'video': 'Видео',
        'animation': 'GIF',
        'document': 'Документ',
        'audio': 'Аудио',
        'location': 'Локация',
        'venue': 'Локация',
    }
    return labels.get(_normalize_cell_text(attachment_type).lower(), 'Вложение')


def _event_description_for_display(description: str, limit: int = 3200):
    """Ограничивает описание события для безопасного вывода в Telegram."""
    normalized = str(description or '').strip()
    if len(normalized) <= limit:
        return normalized
    return f'{normalized[: limit - 1].rstrip()}…'


def _build_event_notice_text(event: dict):
    """Формирует текст уведомления о новом событии."""
    event_at = event.get('event_at')
    lines = [
        '<b>📣 Новое событие</b>',
        '',
        f"📝 Название: <b>{html.escape(_normalize_cell_text(event.get('title')) or 'Событие')}</b>",
        f"🗓️ Когда: <b>{html.escape(_format_event_datetime(event_at))}</b>",
    ]

    attachment_type = event.get('attachment_type')
    if attachment_type:
        lines.append(f"📎 Вложение: <b>{html.escape(_event_attachment_label(attachment_type))}</b>")

    description = _event_description_for_display(event.get('description') or '')
    if description:
        lines.extend(['', html.escape(description)])

    return '\n'.join(lines)


def _serialize_event_attachment(message: types.Message):
    """Извлекает поддерживаемое вложение события из сообщения администратора."""
    content_type = _normalize_cell_text(getattr(message, 'content_type', '')).lower()

    if content_type == 'photo' and message.photo:
        return 'photo', {'file_id': message.photo[-1].file_id}
    if content_type == 'video' and message.video:
        return 'video', {'file_id': message.video.file_id}
    if content_type == 'animation' and message.animation:
        return 'animation', {'file_id': message.animation.file_id}
    if content_type == 'document' and message.document:
        return 'document', {'file_id': message.document.file_id}
    if content_type == 'audio' and message.audio:
        return 'audio', {'file_id': message.audio.file_id}
    if content_type == 'location' and message.location:
        return 'location', {
            'latitude': message.location.latitude,
            'longitude': message.location.longitude,
        }
    if content_type == 'venue' and message.venue:
        return 'venue', {
            'latitude': message.venue.location.latitude,
            'longitude': message.venue.location.longitude,
            'title': message.venue.title,
            'address': message.venue.address,
        }

    return None, None


async def _send_event_attachment(chat_id: int, attachment_type: str | None, attachment_payload: dict | None):
    """Отправляет вложение события пользователю."""
    if not attachment_type or not attachment_payload:
        return

    if attachment_type == 'photo':
        await bot.send_photo(chat_id, attachment_payload.get('file_id'))
        return
    if attachment_type == 'video':
        await bot.send_video(chat_id, attachment_payload.get('file_id'))
        return
    if attachment_type == 'animation':
        await bot.send_animation(chat_id, attachment_payload.get('file_id'))
        return
    if attachment_type == 'document':
        await bot.send_document(chat_id, attachment_payload.get('file_id'))
        return
    if attachment_type == 'audio':
        await bot.send_audio(chat_id, attachment_payload.get('file_id'))
        return
    if attachment_type == 'location':
        await bot.send_location(
            chat_id,
            latitude=attachment_payload.get('latitude'),
            longitude=attachment_payload.get('longitude'),
        )
        return
    if attachment_type == 'venue':
        await bot.send_venue(
            chat_id,
            latitude=attachment_payload.get('latitude'),
            longitude=attachment_payload.get('longitude'),
            title=attachment_payload.get('title') or 'Локация',
            address=attachment_payload.get('address') or 'Адрес не указан',
        )


async def _send_event_attachment_with_caption(
    chat_id: int,
    attachment_type: str | None,
    attachment_payload: dict | None,
    text: str,
    reply_markup,
):
    """Пытается отправить событие одним сообщением: вложение, подпись и кнопки."""
    if not attachment_type or not attachment_payload:
        return False

    if attachment_type == 'photo':
        await bot.send_photo(
            chat_id,
            attachment_payload.get('file_id'),
            caption=text,
            parse_mode='HTML',
            reply_markup=reply_markup,
        )
        return True
    if attachment_type == 'video':
        await bot.send_video(
            chat_id,
            attachment_payload.get('file_id'),
            caption=text,
            parse_mode='HTML',
            reply_markup=reply_markup,
        )
        return True
    if attachment_type == 'animation':
        await bot.send_animation(
            chat_id,
            attachment_payload.get('file_id'),
            caption=text,
            parse_mode='HTML',
            reply_markup=reply_markup,
        )
        return True
    if attachment_type == 'document':
        await bot.send_document(
            chat_id,
            attachment_payload.get('file_id'),
            caption=text,
            parse_mode='HTML',
            reply_markup=reply_markup,
        )
        return True
    if attachment_type == 'audio':
        await bot.send_audio(
            chat_id,
            attachment_payload.get('file_id'),
            caption=text,
            parse_mode='HTML',
            reply_markup=reply_markup,
        )
        return True

    return False


def _build_event_open_keyboard():
    """Строит клавиатуру перехода к списку событий из рассылки."""
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(types.InlineKeyboardButton(text='🎉 Все события', callback_data='events_open_all'))
    return keyboard


async def _send_event_announcement(chat_id: int, event: dict):
    """Отправляет пользователю анонс события и его вложение."""
    text = _build_event_notice_text(event)
    keyboard = _build_event_open_keyboard()
    attachment_type = event.get('attachment_type')
    attachment_payload = event.get('attachment_payload')

    if attachment_type and attachment_payload:
        try:
            sent_as_single_message = await _send_event_attachment_with_caption(
                chat_id,
                attachment_type,
                attachment_payload,
                text,
                keyboard,
            )
            if sent_as_single_message:
                return
        except Exception:
            pass

        await _send_event_attachment(chat_id, attachment_type, attachment_payload)

    await bot.send_message(
        chat_id,
        text,
        parse_mode='HTML',
        reply_markup=keyboard,
    )


def _split_events_for_user_view(events: list[dict]):
    """Разделяет события на будущие и завершенные списки."""
    now = datetime.now()
    upcoming_events = []
    finished_events = []
    undated_events = []

    for event in events:
        event_at = event.get('event_at')
        if not isinstance(event_at, datetime):
            undated_events.append(event)
            continue

        if event_at >= now:
            upcoming_events.append(event)
        else:
            finished_events.append(event)

    upcoming_events.sort(key=lambda item: (item.get('event_at'), int(item.get('id', 0))))
    finished_events.sort(key=lambda item: (item.get('event_at'), int(item.get('id', 0))), reverse=True)
    undated_events.sort(key=lambda item: int(item.get('id', 0)), reverse=True)
    upcoming_events.extend(undated_events)
    return upcoming_events, finished_events


def _events_for_user_view(events: list[dict], mode: str):
    """Возвращает нужный пользователю список событий."""
    upcoming_events, finished_events = _split_events_for_user_view(events)
    if mode == 'past':
        return finished_events
    return upcoming_events


def _pick_events_initial_page(events: list[dict], mode: str):
    """Возвращает стартовую страницу выбранного списка событий."""
    if not events:
        return 0
    return 0


def _event_page_for_id(events: list[dict], event_id: int, mode: str):
    """Возвращает страницу списка, на которой находится событие."""
    for index, item in enumerate(events):
        if int(item.get('id', 0)) == int(event_id):
            return index // EVENTS_PAGE_SIZE
    return _pick_events_initial_page(events, mode)


def _build_event_button_text(event: dict):
    """Строит подпись inline-кнопки события."""
    event_at = event.get('event_at')
    title = _normalize_cell_text(event.get('title')) or 'Событие'
    if len(title) > 24:
        title = f'{title[:23].rstrip()}…'

    date_part = event_at.strftime('%d.%m %H:%M') if isinstance(event_at, datetime) else 'без даты'
    if _event_is_finished(event):
        label = f'✅ Завершено • {date_part} • {title}'
    else:
        label = f'🗓️ {date_part} • {title}'

    if len(label) > 64:
        return f'{label[:63].rstrip()}…'
    return label


def _build_events_list_text(events: list[dict], page: int, total_pages: int, mode: str, all_events: list[dict]):
    """Формирует текст экрана списка событий."""
    upcoming_events, finished_events = _split_events_for_user_view(all_events)
    title = '🎉 События' if mode == 'upcoming' else '✅ Завершенные события'

    lines = [
        f'<b>{title}</b>',
        '',
        f"📄 Страница: <b>{page + 1}/{total_pages}</b> • Всего: <b>{len(events)}</b>",
        f"🟢 Предстоящие: <b>{len(upcoming_events)}</b> • ✅ Завершенные: <b>{len(finished_events)}</b>",
        '',
    ]

    if not events:
        if mode == 'upcoming':
            lines.append('Пока предстоящих событий нет.')
        else:
            lines.append('Завершенных событий пока нет.')
    else:
        lines.append('Нажмите на событие ниже, чтобы открыть подробности.')

    return '\n'.join(lines)


def _build_events_list_keyboard(events: list[dict], page: int, total_pages: int, mode: str, finished_count: int):
    """Строит клавиатуру списка событий."""
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    start = page * EVENTS_PAGE_SIZE
    end = start + EVENTS_PAGE_SIZE

    for event in events[start:end]:
        keyboard.add(
            types.InlineKeyboardButton(
                text=_build_event_button_text(event),
                callback_data=f"events_detail_{mode}_{event.get('id')}_{page}",
            )
        )

    if total_pages > 1:
        keyboard.row(
            types.InlineKeyboardButton(
                text='⬅️',
                callback_data=f'events_page_{mode}_{page - 1}' if page > 0 else 'events_noop',
            ),
            types.InlineKeyboardButton(
                text=f'📄 {page + 1}/{total_pages}',
                callback_data='events_noop',
            ),
            types.InlineKeyboardButton(
                text='➡️',
                callback_data=f'events_page_{mode}_{page + 1}' if page < total_pages - 1 else 'events_noop',
            ),
        )

    if mode == 'upcoming' and finished_count > 0:
        keyboard.add(types.InlineKeyboardButton(text='✅ Завершенные события', callback_data='events_open_past'))
    if mode == 'past':
        keyboard.add(types.InlineKeyboardButton(text='🟢 Актуальные события', callback_data='events_open_upcoming'))

    keyboard.add(types.InlineKeyboardButton(text='🏠 В меню', callback_data='main_menu'))
    return keyboard


def _build_event_detail_text(event: dict, event_index: int, total_events: int):
    """Формирует подробный текст события."""
    event_at = event.get('event_at')
    is_finished = _event_is_finished(event)
    status_line = '✅ Событие завершено' if is_finished else '🟢 Событие запланировано'
    attachment_type = event.get('attachment_type')
    created_at = event.get('created_at')
    created_by_login = _normalize_cell_text(event.get('created_by_login'))

    lines = [
        '<b>🎉 Карточка события</b>',
        '',
        f"📌 Статус: <b>{status_line}</b>",
        f"📝 Название: <b>{html.escape(_normalize_cell_text(event.get('title')) or 'Событие')}</b>",
        f"🗓️ Когда: <b>{html.escape(_format_event_datetime(event_at))}</b>",
    ]

    if attachment_type:
        lines.append(f"📎 Вложение: <b>{html.escape(_event_attachment_label(attachment_type))}</b>")

    if created_by_login:
        lines.append(f"👤 Создал: <b>{html.escape(created_by_login)}</b>")
    if isinstance(created_at, datetime):
        lines.append(f"➕ Добавлено: <b>{created_at.strftime('%d.%m.%Y %H:%M')}</b>")

    description = _event_description_for_display(event.get('description') or '')
    if description:
        lines.extend(['', html.escape(description)])

    lines.extend(['', f"<b>Событие {event_index + 1} из {total_events}</b>"])
    return '\n'.join(lines)


def _build_event_detail_keyboard(events: list[dict], event_index: int, page: int, mode: str):
    """Строит клавиатуру карточки события."""
    event = events[event_index]
    keyboard = types.InlineKeyboardMarkup(row_width=2)

    keyboard.row(
        types.InlineKeyboardButton(
            text='⬅️ Предыдущее',
            callback_data=f"events_prev_{mode}_{event.get('id')}_{page}" if event_index > 0 else 'events_noop',
        ),
        types.InlineKeyboardButton(
            text='Следующее ➡️',
            callback_data=(
                f"events_next_{mode}_{event.get('id')}_{page}"
                if event_index < len(events) - 1
                else 'events_noop'
            ),
        ),
    )
    keyboard.row(
        types.InlineKeyboardButton(text='⬅️ К списку', callback_data=f'events_back_{mode}_{page}'),
        types.InlineKeyboardButton(text='🏠 В меню', callback_data='main_menu'),
    )
    return keyboard


async def _clear_admin_message_context(state: FSMContext):
    """Сбрасывает служебные данные экрана отправки админ-сообщения."""
    await state.reset_state(with_data=False)
    await state.update_data(
        admin_message_mode=None,
        admin_message_target_telegram_id=None,
        admin_message_target_label=None,
        admin_message_course_filter=None,
        admin_message_group_filter=None,
        admin_message_recipients_count=None,
        admin_message_origin_view=None,
        admin_message_origin_chat_id=None,
        admin_message_origin_message_id=None,
    )


async def _clear_admin_event_context(state: FSMContext):
    """Сбрасывает служебные данные экрана создания события."""
    await state.reset_state(with_data=False)
    await state.update_data(
        admin_event_at_iso=None,
        admin_event_description=None,
        admin_event_origin_chat_id=None,
        admin_event_origin_message_id=None,
    )


async def _restore_admin_event_origin(state: FSMContext, telegram_id: int):
    """Возвращает админа к экрану панели после создания или отмены события."""
    user_data = await state.get_data()
    chat_id = user_data.get('admin_event_origin_chat_id')
    message_id = user_data.get('admin_event_origin_message_id')
    if not chat_id or not message_id:
        return

    stats = await db_commands.get_admin_dashboard_stats()
    session_data = await db_commands.get_admin_session(telegram_id)
    await _safe_edit_message_text_by_id(
        int(chat_id),
        int(message_id),
        _build_admin_panel_text(stats, session_data),
        reply_markup=_build_admin_panel_keyboard(),
        parse_mode='HTML',
    )


async def _build_admin_users_view_payload(state: FSMContext):
    """Собирает текст и клавиатуру списка пользователей с учетом фильтров."""
    user_data = await state.get_data()
    course_filter, group_filter, page, starosta_only = _get_admin_users_view_state(user_data)
    course_filters = await db_commands.get_admin_user_course_filters()
    course_labels = {item['value']: item['label'] for item in course_filters}

    if course_filter and course_filter not in course_labels:
        course_filter = None
        group_filter = None
        page = 0

    group_filters = await db_commands.get_admin_user_group_filters(course_filter)
    if group_filter and group_filter not in group_filters:
        group_filter = None
        page = 0

    page_data = await db_commands.get_admin_users_page(
        course_key=course_filter,
        group_code=group_filter,
        page=page,
        page_size=ADMIN_USERS_PAGE_SIZE,
        starosta_only=starosta_only,
    )
    if page_data.get('page', 0) != page:
        page = page_data['page']

    await state.update_data(
        admin_users_course_filter=course_filter,
        admin_users_group_filter=group_filter,
        admin_users_page=page,
        admin_users_starosta_only=starosta_only,
    )

    text = _build_admin_users_text(page_data, course_labels.get(course_filter), group_filter, starosta_only)
    keyboard = _build_admin_users_keyboard(page_data, course_labels.get(course_filter), group_filter, starosta_only)
    return text, keyboard


async def _render_admin_user_details_view(target, telegram_id: int, target_telegram_id: int, *, edit: bool):
    """Показывает карточку выбранного пользователя."""
    if not await db_commands.is_admin_authorized(telegram_id):
        if edit:
            await target.answer('⚠️ Сначала войдите в админ-панель через /admin', show_alert=True)
        else:
            await target.answer('⚠️ Сначала войдите в админ-панель через /admin')
        return False

    user_details = await db_commands.get_admin_user_details(target_telegram_id)
    if not user_details:
        if edit:
            await target.answer('⚠️ Пользователь не найден', show_alert=True)
        else:
            await target.answer('⚠️ Пользователь не найден')
        return False

    text = _build_admin_user_details_text(user_details)
    keyboard = _build_admin_user_details_keyboard(target_telegram_id, user_details)

    if edit:
        await _safe_edit_text(target.message, text, reply_markup=keyboard, parse_mode='HTML')
        await target.answer()
    else:
        await target.answer(text, reply_markup=keyboard, parse_mode='HTML')

    return True


async def _restore_admin_message_origin(state: FSMContext):
    """Возвращает админа к исходному экрану после отправки или отмены."""
    user_data = await state.get_data()
    chat_id = user_data.get('admin_message_origin_chat_id')
    message_id = user_data.get('admin_message_origin_message_id')
    origin_view = user_data.get('admin_message_origin_view')

    if not chat_id or not message_id:
        return

    if origin_view == 'user_details':
        target_telegram_id = user_data.get('admin_message_target_telegram_id')
        if target_telegram_id:
            user_details = await db_commands.get_admin_user_details(int(target_telegram_id))
        else:
            user_details = None
        if user_details:
            await _safe_edit_message_text_by_id(
                int(chat_id),
                int(message_id),
                _build_admin_user_details_text(user_details),
                reply_markup=_build_admin_user_details_keyboard(int(target_telegram_id), user_details),
                parse_mode='HTML',
            )
            return

    text, keyboard = await _build_admin_users_view_payload(state)
    await _safe_edit_message_text_by_id(
        int(chat_id),
        int(message_id),
        text,
        reply_markup=keyboard,
        parse_mode='HTML',
    )


async def _clear_starosta_message_context(state: FSMContext):
    """Сбрасывает служебные данные экрана сообщений старосты."""
    await state.reset_state(with_data=False)
    await state.update_data(
        starosta_message_mode=None,
        starosta_message_actor_telegram_id=None,
        starosta_message_target_telegram_id=None,
        starosta_message_target_label=None,
        starosta_message_group_filter=None,
        starosta_message_recipients_count=None,
        starosta_message_origin_view=None,
        starosta_message_origin_chat_id=None,
        starosta_message_origin_message_id=None,
    )


async def _restore_starosta_message_origin(state: FSMContext):
    """Возвращает старосту к исходному экрану после отправки или отмены."""
    user_data = await state.get_data()
    chat_id = user_data.get('starosta_message_origin_chat_id')
    message_id = user_data.get('starosta_message_origin_message_id')
    origin_view = user_data.get('starosta_message_origin_view')
    actor_telegram_id = user_data.get('starosta_message_actor_telegram_id')

    if not chat_id or not message_id or not actor_telegram_id:
        return

    if origin_view == 'user_details':
        target_telegram_id = user_data.get('starosta_message_target_telegram_id')
        if target_telegram_id:
            user_details = await db_commands.get_admin_user_details(int(target_telegram_id))
            access = await db_commands.get_starosta_access(int(actor_telegram_id))
        else:
            user_details = None
            access = None
        if user_details and access and _starosta_can_view_user(access, user_details):
            await _safe_edit_message_text_by_id(
                int(chat_id),
                int(message_id),
                _build_admin_user_details_text(user_details),
                reply_markup=_build_starosta_user_details_keyboard(int(target_telegram_id)),
                parse_mode='HTML',
            )
            return

    if origin_view == 'users':
        text, keyboard = await _build_starosta_users_view_payload(state, int(actor_telegram_id))
        if text and keyboard:
            await _safe_edit_message_text_by_id(
                int(chat_id),
                int(message_id),
                text,
                reply_markup=keyboard,
                parse_mode='HTML',
            )
            return

    access, available_groups, current_group = await _resolve_starosta_context(state, int(actor_telegram_id))
    recipients = []
    if current_group:
        recipients = await db_commands.get_admin_message_recipients(
            group_code=current_group,
            allowed_group_codes=None if access.get('is_super') else available_groups,
        )
    await _safe_edit_message_text_by_id(
        int(chat_id),
        int(message_id),
        _build_starosta_panel_text(access, current_group, len(recipients)),
        reply_markup=_build_starosta_panel_keyboard(
            current_group,
            can_switch_groups=bool(access.get('is_super') or len(available_groups) > 1),
            has_group=bool(current_group),
        ),
        parse_mode='HTML',
    )


async def _render_admin_users_view(target, state: FSMContext, telegram_id, *, edit: bool):
    """Показывает список пользователей с фильтрами по курсу и группе."""
    if not await db_commands.is_admin_authorized(telegram_id):
        if edit:
            await target.answer('⚠️ Сначала войдите в админ-панель через /admin', show_alert=True)
        else:
            await target.answer('⚠️ Сначала войдите в админ-панель через /admin')
        return False

    text, keyboard = await _build_admin_users_view_payload(state)

    if edit:
        await _safe_edit_text(target.message, text, reply_markup=keyboard, parse_mode='HTML')
        await target.answer()
    else:
        await target.answer(text, reply_markup=keyboard, parse_mode='HTML')

    return True


async def _render_admin_course_filter_picker(callback_query: types.CallbackQuery, state: FSMContext, page: int):
    """Показывает список доступных курсов для фильтрации пользователей."""
    telegram_id = _telegram_id_from_callback(callback_query)
    if not await db_commands.is_admin_authorized(telegram_id):
        await callback_query.answer('⚠️ Сначала войдите в админ-панель через /admin', show_alert=True)
        return

    user_data = await state.get_data()
    selected_course, _, _, _ = _get_admin_users_view_state(user_data)
    courses = await db_commands.get_admin_user_course_filters()
    if not courses:
        await callback_query.answer('Список курсов пока пуст.', show_alert=True)
        return

    page_count = max(1, (len(courses) + ADMIN_FILTER_PAGE_SIZE - 1) // ADMIN_FILTER_PAGE_SIZE)
    page = max(0, min(page, page_count - 1))
    current_label = next((item['label'] for item in courses if item['value'] == selected_course), 'Все курсы')

    text = (
        '<b>🎓 Фильтр по курсу</b>\n\n'
        f'Текущий курс: <b>{html.escape(current_label)}</b>\n'
        'Выберите курс из списка ниже.'
    )
    keyboard = _build_admin_filter_keyboard(
        courses,
        selected_course,
        'admin_users_course_set',
        page,
        'admin_users_course_clear',
        'admin_users_coursepick',
    )
    await _safe_edit_text(callback_query.message, text, reply_markup=keyboard, parse_mode='HTML')
    await callback_query.answer()


async def _render_admin_group_filter_picker(callback_query: types.CallbackQuery, state: FSMContext, page: int):
    """Показывает список групп для фильтрации пользователей."""
    telegram_id = _telegram_id_from_callback(callback_query)
    if not await db_commands.is_admin_authorized(telegram_id):
        await callback_query.answer('⚠️ Сначала войдите в админ-панель через /admin', show_alert=True)
        return

    user_data = await state.get_data()
    selected_course, selected_group, _, _ = _get_admin_users_view_state(user_data)
    groups = await db_commands.get_admin_user_group_filters(selected_course)
    if not groups:
        await callback_query.answer('Для выбранного курса групп пока нет.', show_alert=True)
        return

    page_count = max(1, (len(groups) + ADMIN_FILTER_PAGE_SIZE - 1) // ADMIN_FILTER_PAGE_SIZE)
    page = max(0, min(page, page_count - 1))
    course_label = 'Все курсы'
    if selected_course:
        course_filters = await db_commands.get_admin_user_course_filters()
        course_label = next(
            (item['label'] for item in course_filters if item['value'] == selected_course),
            selected_course,
        )

    text = (
        '<b>👥 Фильтр по группе</b>\n\n'
        f'Курс: <b>{html.escape(course_label)}</b>\n'
        f"Текущая группа: <b>{html.escape(selected_group or 'Все группы')}</b>\n"
        'Выберите группу из списка ниже.'
    )
    keyboard = _build_admin_filter_keyboard(
        groups,
        selected_group,
        'admin_users_group_set',
        page,
        'admin_users_group_clear',
        'admin_users_grouppick',
    )
    await _safe_edit_text(callback_query.message, text, reply_markup=keyboard, parse_mode='HTML')
    await callback_query.answer()


async def admin_users_open(callback_query: types.CallbackQuery, state: FSMContext):
    """Открывает экран списка пользователей в админ-панели."""
    await _render_admin_users_view(callback_query, state, _telegram_id_from_callback(callback_query), edit=True)


async def admin_users_change_page(callback_query: types.CallbackQuery, state: FSMContext):
    """Переключает страницу списка пользователей."""
    try:
        delta = int(callback_query.data.rsplit('_', 1)[1])
    except (TypeError, ValueError, IndexError):
        await callback_query.answer('⚠️ Некорректная страница', show_alert=True)
        return

    telegram_id = _telegram_id_from_callback(callback_query)
    user_data = await state.get_data()
    _, _, page, _ = _get_admin_users_view_state(user_data)
    await state.update_data(admin_users_page=max(0, page + delta))
    await _render_admin_users_view(callback_query, state, telegram_id, edit=True)


async def admin_users_pick_course(callback_query: types.CallbackQuery, state: FSMContext):
    """Открывает выбор курса для фильтра списка пользователей."""
    try:
        page = int(callback_query.data.rsplit('_', 1)[1])
    except (TypeError, ValueError, IndexError):
        page = 0

    await _render_admin_course_filter_picker(callback_query, state, page)


async def admin_users_pick_group(callback_query: types.CallbackQuery, state: FSMContext):
    """Открывает выбор группы для фильтра списка пользователей."""
    try:
        page = int(callback_query.data.rsplit('_', 1)[1])
    except (TypeError, ValueError, IndexError):
        page = 0

    await _render_admin_group_filter_picker(callback_query, state, page)


async def admin_users_set_course(callback_query: types.CallbackQuery, state: FSMContext):
    """Применяет фильтр по курсу и сбрасывает страницу списка."""
    try:
        index = int(callback_query.data.rsplit('_', 1)[1])
    except (TypeError, ValueError, IndexError):
        await callback_query.answer('⚠️ Некорректный курс', show_alert=True)
        return

    courses = await db_commands.get_admin_user_course_filters()
    if index < 0 or index >= len(courses):
        await callback_query.answer('⚠️ Курс не найден', show_alert=True)
        return

    await state.update_data(
        admin_users_course_filter=courses[index]['value'],
        admin_users_group_filter=None,
        admin_users_page=0,
    )
    await _render_admin_users_view(callback_query, state, _telegram_id_from_callback(callback_query), edit=True)


async def admin_users_set_group(callback_query: types.CallbackQuery, state: FSMContext):
    """Применяет фильтр по группе и сбрасывает страницу списка."""
    try:
        index = int(callback_query.data.rsplit('_', 1)[1])
    except (TypeError, ValueError, IndexError):
        await callback_query.answer('⚠️ Некорректная группа', show_alert=True)
        return

    user_data = await state.get_data()
    course_filter, _, _, _ = _get_admin_users_view_state(user_data)
    groups = await db_commands.get_admin_user_group_filters(course_filter)
    if index < 0 or index >= len(groups):
        await callback_query.answer('⚠️ Группа не найдена', show_alert=True)
        return

    await state.update_data(
        admin_users_group_filter=groups[index],
        admin_users_page=0,
    )
    await _render_admin_users_view(callback_query, state, _telegram_id_from_callback(callback_query), edit=True)


async def admin_users_clear_course(callback_query: types.CallbackQuery, state: FSMContext):
    """Сбрасывает фильтр по курсу и группе."""
    await state.update_data(
        admin_users_course_filter=None,
        admin_users_group_filter=None,
        admin_users_page=0,
    )
    await _render_admin_users_view(callback_query, state, _telegram_id_from_callback(callback_query), edit=True)


async def admin_users_clear_group(callback_query: types.CallbackQuery, state: FSMContext):
    """Сбрасывает фильтр по группе."""
    await state.update_data(admin_users_group_filter=None, admin_users_page=0)
    await _render_admin_users_view(callback_query, state, _telegram_id_from_callback(callback_query), edit=True)


async def admin_users_reset_filters(callback_query: types.CallbackQuery, state: FSMContext):
    """Полностью сбрасывает фильтры списка пользователей."""
    await state.update_data(
        admin_users_course_filter=None,
        admin_users_group_filter=None,
        admin_users_page=0,
        admin_users_starosta_only=False,
    )
    await _render_admin_users_view(callback_query, state, _telegram_id_from_callback(callback_query), edit=True)


async def admin_users_toggle_starosta_filter(callback_query: types.CallbackQuery, state: FSMContext):
    """Переключает фильтр списка пользователей только по старостам."""
    user_data = await state.get_data()
    _, _, _, starosta_only = _get_admin_users_view_state(user_data)
    await state.update_data(
        admin_users_starosta_only=not starosta_only,
        admin_users_page=0,
    )
    await _render_admin_users_view(callback_query, state, _telegram_id_from_callback(callback_query), edit=True)


async def admin_users_back_to_list(callback_query: types.CallbackQuery, state: FSMContext):
    """Возвращает из карточки пользователя к списку пользователей."""
    await _render_admin_users_view(callback_query, state, _telegram_id_from_callback(callback_query), edit=True)


async def admin_users_toggle_starosta_assignment(callback_query: types.CallbackQuery, state: FSMContext):
    """Назначает или снимает старосту для группы пользователя."""
    telegram_id = _telegram_id_from_callback(callback_query)
    if not await db_commands.is_admin_authorized(telegram_id):
        await callback_query.answer('⚠️ Сначала войдите в админ-панель через /admin', show_alert=True)
        return

    try:
        target_telegram_id = int(callback_query.data.rsplit('_', 1)[1])
    except (TypeError, ValueError, IndexError):
        await callback_query.answer('⚠️ Пользователь не найден', show_alert=True)
        return

    user_details = await db_commands.get_admin_user_details(target_telegram_id)
    if not user_details:
        await callback_query.answer('⚠️ Пользователь не найден', show_alert=True)
        return

    selected_group = _normalize_cell_text(user_details.get('selected_group')) or None
    if not selected_group:
        await callback_query.answer('⚠️ У пользователя не выбрана группа', show_alert=True)
        return

    starosta_groups = {
        _normalize_cell_text(group)
        for group in (user_details.get('starosta_groups') or [])
        if _normalize_cell_text(group)
    }
    if selected_group in starosta_groups:
        await db_commands.clear_group_starosta(selected_group)
        notice = f'Староста для группы {selected_group} снят'
    else:
        await db_commands.assign_group_starosta(selected_group, target_telegram_id, telegram_id)
        notice = f'Пользователь назначен старостой группы {selected_group}'

    await _render_admin_user_details_view(
        callback_query,
        telegram_id,
        target_telegram_id,
        edit=True,
    )
    await callback_query.answer(notice)


async def admin_users_start_single_message(callback_query: types.CallbackQuery, state: FSMContext):
    """Открывает ввод сообщения для одного пользователя."""
    telegram_id = _telegram_id_from_callback(callback_query)
    if not await db_commands.is_admin_authorized(telegram_id):
        await callback_query.answer('⚠️ Сначала войдите в админ-панель через /admin', show_alert=True)
        return

    try:
        target_telegram_id = int(callback_query.data.rsplit('_', 1)[1])
    except (TypeError, ValueError, IndexError):
        await callback_query.answer('⚠️ Пользователь не найден', show_alert=True)
        return

    user_details = await db_commands.get_admin_user_details(target_telegram_id)
    if not user_details:
        await callback_query.answer('⚠️ Пользователь не найден', show_alert=True)
        return

    await state.update_data(
        admin_message_mode='single',
        admin_message_target_telegram_id=target_telegram_id,
        admin_message_target_label=_build_admin_message_target_label(user_details),
        admin_message_course_filter=None,
        admin_message_group_filter=None,
        admin_message_recipients_count=1,
        admin_message_origin_view='user_details',
        admin_message_origin_chat_id=callback_query.message.chat.id,
        admin_message_origin_message_id=callback_query.message.message_id,
    )
    await state.set_state(AdminMessageDialog.waiting_text.state)

    await _safe_edit_text(
        callback_query.message,
        _build_admin_message_compose_text(
            'single',
            target_label=_build_admin_message_target_label(user_details),
        ),
        reply_markup=_build_admin_message_compose_keyboard(),
        parse_mode='HTML',
    )
    await callback_query.answer()


async def admin_users_start_filtered_message(callback_query: types.CallbackQuery, state: FSMContext):
    """Открывает ввод сообщения для пользователей по текущим фильтрам."""
    telegram_id = _telegram_id_from_callback(callback_query)
    if not await db_commands.is_admin_authorized(telegram_id):
        await callback_query.answer('⚠️ Сначала войдите в админ-панель через /admin', show_alert=True)
        return

    user_data = await state.get_data()
    course_filter, group_filter, _, _ = _get_admin_users_view_state(user_data)
    recipients = await db_commands.get_admin_message_recipients(course_filter, group_filter)
    if not recipients:
        await callback_query.answer('⚠️ По выбранным фильтрам получателей нет', show_alert=True)
        return

    course_label = 'Все курсы'
    if course_filter:
        course_filters = await db_commands.get_admin_user_course_filters()
        course_label = next(
            (item['label'] for item in course_filters if item['value'] == course_filter),
            course_filter,
        )

    await state.update_data(
        admin_message_mode='filtered',
        admin_message_target_telegram_id=None,
        admin_message_target_label=None,
        admin_message_course_filter=course_filter,
        admin_message_group_filter=group_filter,
        admin_message_recipients_count=len(recipients),
        admin_message_origin_view='users',
        admin_message_origin_chat_id=callback_query.message.chat.id,
        admin_message_origin_message_id=callback_query.message.message_id,
    )
    await state.set_state(AdminMessageDialog.waiting_text.state)

    await _safe_edit_text(
        callback_query.message,
        _build_admin_message_compose_text(
            'filtered',
            recipients_count=len(recipients),
            course_label=course_label,
            group_label=group_filter,
        ),
        reply_markup=_build_admin_message_compose_keyboard(),
        parse_mode='HTML',
    )
    await callback_query.answer()


async def admin_users_show_details(callback_query: types.CallbackQuery, state: FSMContext):
    """Открывает карточку выбранного пользователя отдельным сообщением."""
    telegram_id = _telegram_id_from_callback(callback_query)
    try:
        target_telegram_id = int(callback_query.data.rsplit('_', 1)[1])
    except (TypeError, ValueError, IndexError):
        await callback_query.answer('⚠️ Пользователь не найден', show_alert=True)
        return

    await _render_admin_user_details_view(
        callback_query,
        telegram_id,
        target_telegram_id,
        edit=True,
    )


async def admin_message_cancel(callback_query: types.CallbackQuery, state: FSMContext):
    """Отменяет ввод админ-сообщения и возвращает предыдущий экран."""
    telegram_id = _telegram_id_from_callback(callback_query)
    if not await db_commands.is_admin_authorized(telegram_id):
        await callback_query.answer('⚠️ Сначала войдите в админ-панель через /admin', show_alert=True)
        return

    await _restore_admin_message_origin(state)
    await _clear_admin_message_context(state)
    await callback_query.answer('Отправка отменена')


async def admin_receive_message_text(message: types.Message, state: FSMContext):
    """Получает текст админ-сообщения и отправляет его выбранным получателям."""
    telegram_id = _telegram_id_from_message(message)
    if not await db_commands.is_admin_authorized(telegram_id):
        await _clear_admin_message_context(state)
        await message.answer('⚠️ Сначала войдите в админ-панель через /admin')
        return

    text_value = message.text or message.caption or ''
    text_value = text_value.strip()
    if not text_value:
        await message.answer('Отправьте текстовое сообщение или нажмите «Отмена».')
        return

    if _admin_message_is_cancel(text_value):
        await _restore_admin_message_origin(state)
        await _clear_admin_message_context(state)
        await message.answer('Отправка отменена.')
        return

    user_data = await state.get_data()
    mode = user_data.get('admin_message_mode')

    if mode == 'single':
        target_telegram_id = user_data.get('admin_message_target_telegram_id')
        recipients = [{'telegram_id': int(target_telegram_id)}] if target_telegram_id else []
    else:
        recipients = await db_commands.get_admin_message_recipients(
            user_data.get('admin_message_course_filter'),
            user_data.get('admin_message_group_filter'),
        )

    if not recipients:
        await _restore_admin_message_origin(state)
        await _clear_admin_message_context(state)
        await message.answer('⚠️ Получатели не найдены.')
        return

    success_count = 0
    failed_count = 0
    for recipient in recipients:
        try:
            await bot.send_message(int(recipient['telegram_id']), text_value)
            success_count += 1
        except Exception:
            failed_count += 1

    await _restore_admin_message_origin(state)
    await _clear_admin_message_context(state)
    await message.answer(
        f'✅ Отправлено: {success_count}\n'
        f'❌ Ошибок: {failed_count}'
    )


async def starosta_users_open(callback_query: types.CallbackQuery, state: FSMContext):
    """Открывает список учеников в панели старосты."""
    await _render_starosta_users_view(callback_query, state, _telegram_id_from_callback(callback_query), edit=True)


async def starosta_users_change_page(callback_query: types.CallbackQuery, state: FSMContext):
    """Листает список учеников в панели старосты."""
    try:
        delta = int(callback_query.data.rsplit('_', 1)[1])
    except (TypeError, ValueError, IndexError):
        await callback_query.answer('⚠️ Некорректная страница', show_alert=True)
        return

    user_data = await state.get_data()
    try:
        page = int(user_data.get('starosta_users_page', 0))
    except (TypeError, ValueError):
        page = 0

    await state.update_data(starosta_users_page=max(0, page + delta))
    await _render_starosta_users_view(callback_query, state, _telegram_id_from_callback(callback_query), edit=True)


async def starosta_users_back_to_list(callback_query: types.CallbackQuery, state: FSMContext):
    """Возвращает из карточки ученика к списку группы."""
    await _render_starosta_users_view(callback_query, state, _telegram_id_from_callback(callback_query), edit=True)


async def starosta_users_show_details(callback_query: types.CallbackQuery, state: FSMContext):
    """Открывает карточку ученика для старосты."""
    telegram_id = _telegram_id_from_callback(callback_query)
    try:
        target_telegram_id = int(callback_query.data.rsplit('_', 1)[1])
    except (TypeError, ValueError, IndexError):
        await callback_query.answer('⚠️ Ученик не найден', show_alert=True)
        return

    await _render_starosta_user_details_view(
        callback_query,
        telegram_id,
        target_telegram_id,
        edit=True,
    )


async def starosta_start_single_message(callback_query: types.CallbackQuery, state: FSMContext):
    """Открывает ввод сообщения одному ученику от старосты."""
    telegram_id = _telegram_id_from_callback(callback_query)
    access = await db_commands.get_starosta_access(telegram_id)
    if not access.get('has_access'):
        await callback_query.answer('⚠️ У вас нет доступа к панели старосты', show_alert=True)
        return

    try:
        target_telegram_id = int(callback_query.data.rsplit('_', 1)[1])
    except (TypeError, ValueError, IndexError):
        await callback_query.answer('⚠️ Ученик не найден', show_alert=True)
        return

    user_details = await db_commands.get_admin_user_details(target_telegram_id)
    if not user_details or not _starosta_can_view_user(access, user_details):
        await callback_query.answer('⚠️ Ученик недоступен', show_alert=True)
        return

    await state.update_data(
        starosta_message_mode='single',
        starosta_message_actor_telegram_id=telegram_id,
        starosta_message_target_telegram_id=target_telegram_id,
        starosta_message_target_label=_build_admin_message_target_label(user_details),
        starosta_message_group_filter=None,
        starosta_message_recipients_count=1,
        starosta_message_origin_view='user_details',
        starosta_message_origin_chat_id=callback_query.message.chat.id,
        starosta_message_origin_message_id=callback_query.message.message_id,
    )
    await state.set_state(StarostaMessageDialog.waiting_text.state)
    await _safe_edit_text(
        callback_query.message,
        _build_starosta_message_compose_text(
            'single',
            target_label=_build_admin_message_target_label(user_details),
        ),
        reply_markup=_build_starosta_message_compose_keyboard(),
        parse_mode='HTML',
    )
    await callback_query.answer()


async def starosta_start_group_message(callback_query: types.CallbackQuery, state: FSMContext):
    """Открывает ввод сообщения всей группе от старосты."""
    telegram_id = _telegram_id_from_callback(callback_query)
    access, available_groups, current_group = await _resolve_starosta_context(state, telegram_id)
    if not access.get('has_access'):
        await callback_query.answer('⚠️ У вас нет доступа к панели старосты', show_alert=True)
        return

    if not current_group:
        await callback_query.answer('⚠️ Сначала выберите группу', show_alert=True)
        return

    recipients = await db_commands.get_admin_message_recipients(
        group_code=current_group,
        allowed_group_codes=None if access.get('is_super') else available_groups,
    )
    if not recipients:
        await callback_query.answer('⚠️ В этой группе пока нет получателей', show_alert=True)
        return

    origin_view = 'panel' if callback_query.data == 'starosta_group_message' else 'users'
    await state.update_data(
        starosta_message_mode='group',
        starosta_message_actor_telegram_id=telegram_id,
        starosta_message_target_telegram_id=None,
        starosta_message_target_label=None,
        starosta_message_group_filter=current_group,
        starosta_message_recipients_count=len(recipients),
        starosta_message_origin_view=origin_view,
        starosta_message_origin_chat_id=callback_query.message.chat.id,
        starosta_message_origin_message_id=callback_query.message.message_id,
    )
    await state.set_state(StarostaMessageDialog.waiting_text.state)
    await _safe_edit_text(
        callback_query.message,
        _build_starosta_message_compose_text(
            'group',
            recipients_count=len(recipients),
            group_label=current_group,
        ),
        reply_markup=_build_starosta_message_compose_keyboard(),
        parse_mode='HTML',
    )
    await callback_query.answer()


async def starosta_message_cancel(callback_query: types.CallbackQuery, state: FSMContext):
    """Отменяет ввод сообщения от старосты."""
    telegram_id = _telegram_id_from_callback(callback_query)
    access = await db_commands.get_starosta_access(telegram_id)
    if not access.get('has_access'):
        await callback_query.answer('⚠️ У вас нет доступа к панели старосты', show_alert=True)
        return

    await _restore_starosta_message_origin(state)
    await _clear_starosta_message_context(state)
    await callback_query.answer('Отправка отменена')


async def starosta_receive_message_text(message: types.Message, state: FSMContext):
    """Получает текст сообщения от старосты и отправляет его адресатам."""
    telegram_id = _telegram_id_from_message(message)
    access = await db_commands.get_starosta_access(telegram_id)
    if not access.get('has_access'):
        await _clear_starosta_message_context(state)
        await message.answer('⚠️ У вас нет доступа к панели старосты')
        return

    text_value = (message.text or message.caption or '').strip()
    if not text_value:
        await message.answer('Отправьте текстовое сообщение или нажмите «Отмена».')
        return

    if _admin_message_is_cancel(text_value):
        await _restore_starosta_message_origin(state)
        await _clear_starosta_message_context(state)
        await message.answer('Отправка отменена.')
        return

    user_data = await state.get_data()
    mode = user_data.get('starosta_message_mode')
    if mode == 'single':
        target_telegram_id = user_data.get('starosta_message_target_telegram_id')
        recipients = [{'telegram_id': int(target_telegram_id)}] if target_telegram_id else []
    else:
        recipients = await db_commands.get_admin_message_recipients(
            group_code=user_data.get('starosta_message_group_filter'),
            allowed_group_codes=None if access.get('is_super') else access.get('groups'),
        )

    if not recipients:
        await _restore_starosta_message_origin(state)
        await _clear_starosta_message_context(state)
        await message.answer('⚠️ Получатели не найдены.')
        return

    success_count = 0
    failed_count = 0
    for recipient in recipients:
        try:
            await bot.send_message(int(recipient['telegram_id']), text_value)
            success_count += 1
        except Exception:
            failed_count += 1

    await _restore_starosta_message_origin(state)
    await _clear_starosta_message_context(state)
    await message.answer(
        f'✅ Отправлено: {success_count}\n'
        f'❌ Ошибок: {failed_count}'
    )


async def starosta_noop(callback_query: types.CallbackQuery):
    """Служебная кнопка панели старосты."""
    await callback_query.answer()


def _admin_event_skip_requested(text_value: str) -> bool:
    """Проверяет, что администратор хочет сохранить событие без вложения."""
    return _search_normalize_text(text_value) in {'без вложения', 'без файла', 'skip'}


def _get_admin_event_payload(user_data: dict):
    """Достает из FSM дату и текст создаваемого события."""
    event_at_iso = _normalize_cell_text(user_data.get('admin_event_at_iso'))
    description = str(user_data.get('admin_event_description') or '').strip()

    try:
        event_at = datetime.fromisoformat(event_at_iso) if event_at_iso else None
    except ValueError:
        event_at = None

    return event_at, description


async def _finalize_admin_event_creation(
    state: FSMContext,
    telegram_id: int,
    *,
    attachment_type: str | None = None,
    attachment_payload: dict | None = None,
):
    """Сохраняет событие в БД и рассылает его пользователям."""
    user_data = await state.get_data()
    event_at, description = _get_admin_event_payload(user_data)
    if event_at is None or not description:
        return None, 0, 0

    admin_session = await db_commands.get_admin_session(telegram_id)
    event = await db_commands.create_bot_event(
        title=_event_title_from_description(description),
        description=description,
        event_at=event_at,
        attachment_type=attachment_type,
        attachment_payload=attachment_payload,
        created_by_telegram_id=telegram_id,
        created_by_login=(admin_session or {}).get('login'),
    )

    recipients = await db_commands.get_event_notification_recipients()
    success_count = 0
    failed_count = 0
    for recipient in recipients:
        try:
            await _send_event_announcement(int(recipient['telegram_id']), event)
            success_count += 1
        except Exception:
            failed_count += 1

    return event, success_count, failed_count


async def admin_event_create_start(callback_query: types.CallbackQuery, state: FSMContext):
    """Запускает создание нового события из админ-панели."""
    telegram_id = _telegram_id_from_callback(callback_query)
    if not await db_commands.is_admin_authorized(telegram_id):
        await callback_query.answer('⚠️ Сначала войдите в админ-панель через /admin', show_alert=True)
        return

    await state.update_data(
        admin_event_at_iso=None,
        admin_event_description=None,
        admin_event_origin_chat_id=callback_query.message.chat.id,
        admin_event_origin_message_id=callback_query.message.message_id,
    )
    await state.set_state(AdminEventDialog.waiting_datetime.state)
    await _safe_edit_text(
        callback_query.message,
        _build_admin_event_datetime_text(),
        reply_markup=_build_admin_event_step_keyboard(),
        parse_mode='HTML',
    )
    await callback_query.answer()


async def admin_event_cancel(callback_query: types.CallbackQuery, state: FSMContext):
    """Отменяет создание события и возвращает администратора в панель."""
    telegram_id = _telegram_id_from_callback(callback_query)
    if not await db_commands.is_admin_authorized(telegram_id):
        await _clear_admin_event_context(state)
        await callback_query.answer('⚠️ Сначала войдите в админ-панель через /admin', show_alert=True)
        return

    await _restore_admin_event_origin(state, telegram_id)
    await _clear_admin_event_context(state)
    await callback_query.answer('Создание события отменено')


async def admin_event_receive_datetime(message: types.Message, state: FSMContext):
    """Принимает дату и время нового события."""
    telegram_id = _telegram_id_from_message(message)
    if not await db_commands.is_admin_authorized(telegram_id):
        await _clear_admin_event_context(state)
        await message.answer('⚠️ Сначала войдите в админ-панель через /admin')
        return

    text_value = _normalize_cell_text(message.text or message.caption)
    if not text_value:
        await message.answer('Введите дату и время события.')
        return

    if _admin_message_is_cancel(text_value):
        await _restore_admin_event_origin(state, telegram_id)
        await _clear_admin_event_context(state)
        await message.answer('Создание события отменено.')
        return

    event_at = _parse_admin_event_datetime(text_value)
    if event_at is None:
        await message.answer(
            '⚠️ Не удалось распознать дату и время.\n'
            'Пример: 14.03.2026 18:30'
        )
        return

    await state.update_data(admin_event_at_iso=event_at.isoformat())
    await state.set_state(AdminEventDialog.waiting_text.state)
    await message.answer(
        _build_admin_event_text_prompt(event_at),
        reply_markup=_build_admin_event_step_keyboard(),
        parse_mode='HTML',
    )


async def admin_event_receive_text(message: types.Message, state: FSMContext):
    """Принимает текст нового события."""
    telegram_id = _telegram_id_from_message(message)
    if not await db_commands.is_admin_authorized(telegram_id):
        await _clear_admin_event_context(state)
        await message.answer('⚠️ Сначала войдите в админ-панель через /admin')
        return

    text_value = _normalize_cell_text(message.text)
    if not text_value:
        await message.answer('Отправьте обычное текстовое сообщение с описанием события.')
        return

    if _admin_message_is_cancel(text_value):
        await _restore_admin_event_origin(state, telegram_id)
        await _clear_admin_event_context(state)
        await message.answer('Создание события отменено.')
        return

    user_data = await state.get_data()
    event_at, _ = _get_admin_event_payload(user_data)
    if event_at is None:
        await _clear_admin_event_context(state)
        await message.answer('⚠️ Дата события потерялась. Начните создание заново.')
        return

    await state.update_data(admin_event_description=text_value)
    await state.set_state(AdminEventDialog.waiting_attachment.state)
    await message.answer(
        _build_admin_event_attachment_prompt(event_at, text_value),
        reply_markup=_build_admin_event_step_keyboard(allow_skip=True),
        parse_mode='HTML',
    )


async def admin_event_skip_attachment(callback_query: types.CallbackQuery, state: FSMContext):
    """Сохраняет событие без вложения."""
    telegram_id = _telegram_id_from_callback(callback_query)
    if not await db_commands.is_admin_authorized(telegram_id):
        await _clear_admin_event_context(state)
        await callback_query.answer('⚠️ Сначала войдите в админ-панель через /admin', show_alert=True)
        return

    event, success_count, failed_count = await _finalize_admin_event_creation(state, telegram_id)
    if event is None:
        await _clear_admin_event_context(state)
        await callback_query.answer('⚠️ Не удалось сохранить событие. Начните заново.', show_alert=True)
        return

    await _restore_admin_event_origin(state, telegram_id)
    await _clear_admin_event_context(state)
    await callback_query.message.answer(
        '✅ Событие сохранено.\n'
        f'👤 Получили уведомление: {success_count}\n'
        f'❌ Ошибок отправки: {failed_count}'
    )
    await callback_query.answer()


async def admin_event_receive_attachment(message: types.Message, state: FSMContext):
    """Принимает вложение события и завершает создание."""
    telegram_id = _telegram_id_from_message(message)
    if not await db_commands.is_admin_authorized(telegram_id):
        await _clear_admin_event_context(state)
        await message.answer('⚠️ Сначала войдите в админ-панель через /admin')
        return

    if message.text:
        text_value = _normalize_cell_text(message.text)
        if _admin_message_is_cancel(text_value):
            await _restore_admin_event_origin(state, telegram_id)
            await _clear_admin_event_context(state)
            await message.answer('Создание события отменено.')
            return
        if _admin_event_skip_requested(text_value):
            event, success_count, failed_count = await _finalize_admin_event_creation(state, telegram_id)
            if event is None:
                await _clear_admin_event_context(state)
                await message.answer('⚠️ Не удалось сохранить событие. Начните заново.')
                return

            await _restore_admin_event_origin(state, telegram_id)
            await _clear_admin_event_context(state)
            await message.answer(
                '✅ Событие сохранено.\n'
                f'👤 Получили уведомление: {success_count}\n'
                f'❌ Ошибок отправки: {failed_count}'
            )
            return

    attachment_type, attachment_payload = _serialize_event_attachment(message)
    if not attachment_type:
        await message.answer(
            'Поддерживаются только фото, видео, GIF, документ, аудио или локация.\n'
            'Если вложение не нужно, нажмите кнопку «Без вложения».'
        )
        return

    event, success_count, failed_count = await _finalize_admin_event_creation(
        state,
        telegram_id,
        attachment_type=attachment_type,
        attachment_payload=attachment_payload,
    )
    if event is None:
        await _clear_admin_event_context(state)
        await message.answer('⚠️ Не удалось сохранить событие. Начните заново.')
        return

    await _restore_admin_event_origin(state, telegram_id)
    await _clear_admin_event_context(state)
    await message.answer(
        '✅ Событие сохранено.\n'
        f'👤 Получили уведомление: {success_count}\n'
        f'❌ Ошибок отправки: {failed_count}'
    )


async def _render_events_list_view(target, *, edit: bool, page: int | None = None, mode: str = 'upcoming'):
    """Показывает список событий пользователю."""
    all_events = await db_commands.get_bot_events()
    events = _events_for_user_view(all_events, mode)
    _, finished_events = _split_events_for_user_view(all_events)
    total_pages = max(1, (len(events) + EVENTS_PAGE_SIZE - 1) // EVENTS_PAGE_SIZE)

    if page is None:
        page = _pick_events_initial_page(events, mode)
    page = max(0, min(int(page), total_pages - 1))

    text = _build_events_list_text(events, page, total_pages, mode, all_events)
    keyboard = _build_events_list_keyboard(events, page, total_pages, mode, len(finished_events))

    if edit:
        await _safe_edit_text(target.message, text, reply_markup=keyboard, parse_mode='HTML')
        await target.answer()
    else:
        if isinstance(target, types.CallbackQuery):
            await target.message.answer(text, reply_markup=keyboard, parse_mode='HTML')
            await target.answer()
        else:
            await target.answer(text, reply_markup=keyboard, parse_mode='HTML')


async def _send_event_detail_view(chat_id: int, event_id: int, page: int | None = None, mode: str = 'upcoming'):
    """Отправляет карточку выбранного события новым сообщением."""
    events = _events_for_user_view(await db_commands.get_bot_events(), mode)
    if not events:
        return False

    event_index = next((index for index, item in enumerate(events) if int(item.get('id', 0)) == int(event_id)), None)
    if event_index is None:
        return False

    if page is None:
        page = _event_page_for_id(events, event_id, mode)
    total_pages = max(1, (len(events) + EVENTS_PAGE_SIZE - 1) // EVENTS_PAGE_SIZE)
    page = max(0, min(int(page), total_pages - 1))

    event = events[event_index]
    text = _build_event_detail_text(event, event_index, len(events))
    keyboard = _build_event_detail_keyboard(events, event_index, page, mode)
    attachment_type = event.get('attachment_type')
    attachment_payload = event.get('attachment_payload')

    if attachment_type and attachment_payload:
        try:
            sent_as_single_message = await _send_event_attachment_with_caption(
                chat_id,
                attachment_type,
                attachment_payload,
                text,
                keyboard,
            )
            if sent_as_single_message:
                return True
        except Exception:
            pass

        await _send_event_attachment(chat_id, attachment_type, attachment_payload)

    await bot.send_message(
        chat_id,
        text,
        parse_mode='HTML',
        reply_markup=keyboard,
    )
    return True


async def user_events(message: types.Message):
    """Открывает список событий из главного меню."""
    await _render_events_list_view(message, edit=False, mode='upcoming')


async def user_events_change_page(callback_query: types.CallbackQuery):
    """Переключает страницу списка событий."""
    try:
        parts = callback_query.data.split('_', 3)
        if len(parts) == 4:
            _, _, mode, page_text = parts
            page = int(page_text)
        else:
            mode = 'upcoming'
            page = int(callback_query.data.rsplit('_', 1)[1])
    except (TypeError, ValueError, IndexError):
        await callback_query.answer('⚠️ Некорректная страница', show_alert=True)
        return

    await _render_events_list_view(callback_query, edit=True, page=page, mode=mode)


async def user_events_back_to_list(callback_query: types.CallbackQuery):
    """Возвращает из карточки события к списку."""
    try:
        parts = callback_query.data.split('_', 3)
        if len(parts) == 4:
            _, _, mode, page_text = parts
            page = int(page_text)
        else:
            mode = 'upcoming'
            page = int(callback_query.data.rsplit('_', 1)[1])
    except (TypeError, ValueError, IndexError):
        mode = 'upcoming'
        page = None

    await _render_events_list_view(callback_query, edit=False, page=page, mode=mode)


async def user_events_open_detail(callback_query: types.CallbackQuery):
    """Открывает карточку события по кнопке из списка."""
    payload = callback_query.data[len('events_detail_'):]
    try:
        parts = payload.split('_', 2)
        if len(parts) == 3:
            mode, event_id_text, page_text = parts
            event_id = int(event_id_text)
            page = int(page_text)
        else:
            mode = 'upcoming'
            event_id_text, page_text = payload.rsplit('_', 1)
            event_id = int(event_id_text)
            page = int(page_text)
    except (TypeError, ValueError, IndexError):
        await callback_query.answer('⚠️ Некорректное событие', show_alert=True)
        return

    sent = await _send_event_detail_view(callback_query.message.chat.id, event_id, page, mode)
    if not sent:
        await callback_query.answer('⚠️ Событие не найдено', show_alert=True)
        return
    await callback_query.answer()


async def user_events_jump(callback_query: types.CallbackQuery):
    """Открывает список событий из уведомления."""
    await _render_events_list_view(callback_query, edit=False, page=None, mode='upcoming')


async def user_events_open_past(callback_query: types.CallbackQuery):
    """Открывает список завершенных событий."""
    await _render_events_list_view(callback_query, edit=False, page=None, mode='past')


async def user_events_open_upcoming(callback_query: types.CallbackQuery):
    """Открывает основной список актуальных событий."""
    await _render_events_list_view(callback_query, edit=False, page=None, mode='upcoming')


async def user_events_shift_detail(callback_query: types.CallbackQuery):
    """Листает карточки событий вперед или назад."""
    if callback_query.data.startswith('events_prev_'):
        payload = callback_query.data[len('events_prev_'):]
        delta = -1
    else:
        payload = callback_query.data[len('events_next_'):]
        delta = 1

    try:
        parts = payload.split('_', 2)
        if len(parts) == 3:
            mode, event_id_text, page_text = parts
            event_id = int(event_id_text)
            page = int(page_text)
        else:
            mode = 'upcoming'
            event_id_text, page_text = payload.rsplit('_', 1)
            event_id = int(event_id_text)
            page = int(page_text)
    except (TypeError, ValueError, IndexError):
        await callback_query.answer('⚠️ Некорректное событие', show_alert=True)
        return

    events = _events_for_user_view(await db_commands.get_bot_events(), mode)
    event_index = next((index for index, item in enumerate(events) if int(item.get('id', 0)) == event_id), None)
    if event_index is None:
        await callback_query.answer('⚠️ Событие не найдено', show_alert=True)
        return

    target_index = event_index + delta
    if target_index < 0 or target_index >= len(events):
        await callback_query.answer('📭 Больше событий нет.', show_alert=True)
        return

    sent = await _send_event_detail_view(
        callback_query.message.chat.id,
        int(events[target_index]['id']),
        page,
        mode,
    )
    if not sent:
        await callback_query.answer('⚠️ Событие не найдено', show_alert=True)
        return
    await callback_query.answer()


async def user_events_open_attachment(callback_query: types.CallbackQuery):
    """Отправляет пользователю вложение выбранного события."""
    payload = callback_query.data[len('events_media_'):]
    try:
        event_id_text, _ = payload.rsplit('_', 1)
        event_id = int(event_id_text)
    except (TypeError, ValueError, IndexError):
        await callback_query.answer('⚠️ Некорректное вложение', show_alert=True)
        return

    event = await db_commands.get_bot_event(event_id)
    if not event or not event.get('attachment_type') or not event.get('attachment_payload'):
        await callback_query.answer('📭 У этого события нет вложения.', show_alert=True)
        return

    try:
        await _send_event_attachment(
            callback_query.message.chat.id,
            event.get('attachment_type'),
            event.get('attachment_payload'),
        )
    except Exception:
        await callback_query.answer('⚠️ Не удалось открыть вложение', show_alert=True)
        return

    await callback_query.answer('Вложение отправлено')


async def user_events_noop(callback_query: types.CallbackQuery):
    """Служебная кнопка списка событий."""
    await callback_query.answer()


async def admin_users_noop(callback_query: types.CallbackQuery):
    """Служебная кнопка пагинации в админ-панели."""
    await callback_query.answer()


def _digest_settings_from_user_data(user_data):
    """Возвращает нормализованные настройки вечерней рассылки."""
    enabled = user_data.get('daily_digest_enabled')
    hour = user_data.get('daily_digest_hour')
    minute = user_data.get('daily_digest_minute')

    if enabled is None:
        enabled = db_commands.DEFAULT_DAILY_DIGEST_ENABLED
    else:
        enabled = bool(enabled)

    try:
        hour = int(hour)
    except (TypeError, ValueError):
        hour = db_commands.DEFAULT_DAILY_DIGEST_HOUR

    try:
        minute = int(minute)
    except (TypeError, ValueError):
        minute = db_commands.DEFAULT_DAILY_DIGEST_MINUTE

    return enabled, hour % 24, max(0, min(59, minute))


def _event_notifications_enabled_from_user_data(user_data):
    """Возвращает нормализованный флаг уведомлений о событиях."""
    value = user_data.get('event_notifications_enabled')
    if value is None:
        return True
    return bool(value)


def _build_settings_text(user_data):
    """Формирует текст экрана настроек."""
    course = user_data.get('selected_course')
    group_code = user_data.get('selected_group')
    digest_enabled, digest_hour, digest_minute = _digest_settings_from_user_data(user_data)
    event_notifications_enabled = _event_notifications_enabled_from_user_data(user_data)

    lines = ['<b>💼 Настройки</b>', '']

    if course and group_code:
        lines.append(f"🎓 Курс: <b>{html.escape(_course_display(course))}</b>")
        lines.append(f"👥 Группа: <b>{html.escape(group_code)}</b>")
    else:
        lines.append("👥 Группа: <b>не выбрана</b>")

    lines.append('')
    lines.append(
        f"🔔 Вечерняя рассылка: <b>{'включена ✅' if digest_enabled else 'выключена ❌'}</b>"
    )
    lines.append(f"🕒 Время рассылки: <b>{digest_hour:02d}:{digest_minute:02d} МСК</b>")
    lines.append(
        f"🎉 Уведомления о событиях: <b>{'включены ✅' if event_notifications_enabled else 'выключены ❌'}</b>"
    )

    if not group_code:
        lines.append('')
        lines.append('ℹ️ Рассылка начнет работать после выбора группы.')

    return '\n'.join(lines)


def _build_settings_keyboard(user_data):
    """Строит клавиатуру экрана настроек."""
    group_code = user_data.get('selected_group')
    digest_enabled, digest_hour, digest_minute = _digest_settings_from_user_data(user_data)
    event_notifications_enabled = _event_notifications_enabled_from_user_data(user_data)

    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        types.InlineKeyboardButton(
            text="✏️ Изменить группу" if group_code else "🎓 Выбрать группу",
            callback_data="change_group",
        )
    )
    keyboard.add(
        types.InlineKeyboardButton(
            text="🔕 Выключить рассылку" if digest_enabled else "🔔 Включить рассылку",
            callback_data="settings_digest_toggle",
        )
    )
    keyboard.row(
        types.InlineKeyboardButton(text='◀️', callback_data='settings_digest_time_-1'),
        types.InlineKeyboardButton(
            text=f"🕒 {digest_hour:02d}:{digest_minute:02d}",
            callback_data='settings_time_noop',
        ),
        types.InlineKeyboardButton(text='▶️', callback_data='settings_digest_time_1'),
    )
    keyboard.add(
        types.InlineKeyboardButton(
            text='🔕 Отключить события' if event_notifications_enabled else '🎉 Включить события',
            callback_data='settings_events_toggle',
        )
    )
    return keyboard


async def _render_settings_view(target_message: types.Message, state: FSMContext, telegram_id, *, edit: bool = False):
    """Показывает или обновляет экран настроек."""
    user_data = await _get_user_data_with_db_fallback(state, telegram_id)
    text = _build_settings_text(user_data)
    keyboard = _build_settings_keyboard(user_data)

    if edit:
        await _safe_edit_text(target_message, text, reply_markup=keyboard, parse_mode='HTML')
    else:
        await target_message.answer(text, reply_markup=keyboard, parse_mode='HTML')


async def settings(message: types.Message, state: FSMContext):
    """Показывает раздел настроек."""
    await _render_settings_view(message, state, _telegram_id_from_message(message), edit=False)

# Словари для хранения расписания
schedule_data = {}
group_info_data = {}
week_days_info = {}  # Структура для хранения информации о днях недели: {курс: {неделя: {день: дата}}}
DAY_NAMES = {
    "понедельник",
    "вторник",
    "среда",
    "четверг",
    "пятница",
    "суббота",
    "воскресенье",
}
EVENT_WEEKDAY_NAMES = [
    "понедельник",
    "вторник",
    "среда",
    "четверг",
    "пятница",
    "суббота",
    "воскресенье",
]

GROUP_PREFIX = "ИД"
WEEK_TOKEN = "НЕДЕЛЯ"
TIME_HEADER = "время"
DEFAULT_DATE = "Не указана"
DEFAULT_DIRECTION = "Не указано"

IGNORED_LESSON_VALUES = {
    "",
    "none",
    "null",
    "нет",
    "праздничный день",
}

SCHEDULES_DIR = Path(__file__).resolve().parents[1] / "schedules"
EXCEL_EXTENSIONS = {".xls", ".xlsx"}

# Отображаемые названия курсов с учетом периода из имени файла
course_display_names = {}
course_period_ids = {}
period_id_to_label = {}
period_courses = {}
USER_SELECTION_KEYS = (
    'selected_course',
    'selected_course_name',
    'selected_group',
    'selected_week_index',
    'selected_day_index',
    'daily_digest_enabled',
    'daily_digest_hour',
    'daily_digest_minute',
    'event_notifications_enabled',
)
SEARCH_CALLBACK_CACHE = {}
SEARCH_CALLBACK_LIMIT = 3000
SEARCH_PAGE_SIZE = 4
SEARCH_TYPES = {
    'teacher': {
        'title': '👨‍🏫 Поиск по преподавателю',
        'entity_emoji': '👨‍🏫',
        'entity_label': 'Преподаватель',
        'prompt': 'Напишите преподавателя. Пример: `Леденчук` или `Леденчук Н.С.`',
        'empty': 'По такому преподавателю ничего не найдено.',
        'command_example': '/teacher Простомолотов',
    },
    'room': {
        'title': '🏫 Поиск по аудитории',
        'entity_emoji': '🏫',
        'entity_label': 'Аудитория',
        'prompt': 'Напишите аудиторию. Пример: `125`, `ауд. 125`, `спортзал`',
        'empty': 'По такой аудитории ничего не найдено.',
        'command_example': '/room 505',
    },
    'subject': {
        'title': '📚 Поиск по предмету',
        'entity_emoji': '📚',
        'entity_label': 'Предмет',
        'prompt': 'Напишите предмет. Пример: `математика` или `принятия решений`',
        'empty': 'По такому предмету ничего не найдено.',
        'command_example': '/subject Эконометрика',
    },
}
ROOM_PATTERN = re.compile(r'(?i)\b(?:ауд\.?|каб\.?|кабинет|лаб\.?|лаборатория|спортзал|зал)\s*[^,;|]*')
ADMIN_USERS_PAGE_SIZE = 8
ADMIN_FILTER_PAGE_SIZE = 8
EVENTS_PAGE_SIZE = 8


def _telegram_id_from_message(message: types.Message):
    """Возвращает telegram_id из сообщения."""
    if message and message.from_user:
        return message.from_user.id
    return None


def _telegram_id_from_callback(callback_query: types.CallbackQuery):
    """Возвращает telegram_id из callback-запроса."""
    if callback_query and callback_query.from_user:
        return callback_query.from_user.id
    return None


async def _load_user_selection_from_db(telegram_id):
    """Загружает сохраненный выбор пользователя из базы данных."""
    if telegram_id is None:
        return {}

    try:
        db_state = await db_commands.get_user_schedule_state(telegram_id)
    except Exception as db_error:
        print(f"ERROR: cannot load user schedule state from DB: {db_error}")
        return {}

    if not db_state:
        return {}

    return {key: db_state.get(key) for key in USER_SELECTION_KEYS if key in db_state}


async def _get_user_data_with_db_fallback(state: FSMContext, telegram_id):
    """Возвращает пользовательские данные из FSM с подгрузкой из БД при необходимости."""
    user_data = await state.get_data()

    required_keys = (
        'selected_course',
        'selected_group',
        'daily_digest_enabled',
        'daily_digest_hour',
        'daily_digest_minute',
        'event_notifications_enabled',
    )
    if all(key in user_data and user_data.get(key) is not None for key in required_keys):
        return user_data

    db_payload = await _load_user_selection_from_db(telegram_id)
    if not db_payload:
        return user_data

    missing_payload = {
        key: value
        for key, value in db_payload.items()
        if key not in user_data or user_data.get(key) is None
    }
    if missing_payload:
        await state.update_data(**missing_payload)
        user_data = await state.get_data()

    return user_data


async def _update_user_selection(state: FSMContext, telegram_id, **kwargs):
    """Обновляет выбор пользователя в FSM и синхронизирует его в БД."""
    payload = {key: kwargs.get(key) for key in USER_SELECTION_KEYS if key in kwargs}
    if not payload:
        return

    await state.update_data(**payload)

    if telegram_id is None:
        return

    try:
        await db_commands.upsert_user_schedule_state(telegram_id, **payload)
    except Exception as db_error:
        print(f"ERROR: cannot save user schedule state to DB: {db_error}")



def _discover_excel_files():
    """Ищет все Excel-файлы в папке schedules."""
    SCHEDULES_DIR.mkdir(parents=True, exist_ok=True)
    files = [
        file_path
        for file_path in SCHEDULES_DIR.iterdir()
        if file_path.is_file()
        and file_path.suffix.lower() in EXCEL_EXTENSIONS
        and not file_path.name.startswith("~$")
    ]
    return sorted(files, key=lambda item: item.name.lower())


def _extract_period_label(file_name):
    """Формирует подпись периода по имени Excel-файла."""
    stem = Path(file_name).stem
    normalized = stem.lower()
    normalized = normalized.replace(".", " ").replace("-", " ").replace("_", " ")

    parts = []

    year_match = re.search(r"(\d{2})\s+(\d{2})", normalized)
    if year_match:
        start_year = int(year_match.group(1))
        end_year = int(year_match.group(2))
        parts.append(f"20{start_year:02d}/20{end_year:02d}")

    season_map = [
        ("vesen", "Весенний"),
        ("osen", "Осенний"),
    ]

    month_map = [
        ("yanvar", "Январь"),
        ("january", "Январь"),
        ("fevr", "Февраль"),
        ("feb", "Февраль"),
        ("mart", "Март"),
        ("march", "Март"),
        ("aprel", "Апрель"),
        ("april", "Апрель"),
        ("may", "Май"),
        ("mai", "Май"),
        ("iyun", "Июнь"),
        ("june", "Июнь"),
        ("iyul", "Июль"),
        ("july", "Июль"),
        ("avgust", "Август"),
        ("august", "Август"),
        ("sentyabr", "Сентябрь"),
        ("september", "Сентябрь"),
        ("oktyabr", "Октябрь"),
        ("october", "Октябрь"),
        ("noyabr", "Ноябрь"),
        ("november", "Ноябрь"),
        ("dekabr", "Декабрь"),
        ("december", "Декабрь"),
    ]

    for token, label in season_map:
        if token in normalized:
            parts.append(f"{label} семестр")
            break

    for token, label in month_map:
        if token in normalized:
            parts.append(label)
            break

    return " | ".join(parts) if parts else stem


def _register_period(period_label):
    """Регистрирует период и возвращает его внутренний идентификатор."""
    for period_id, label in period_id_to_label.items():
        if label == period_label:
            return period_id

    period_id = f"P{len(period_id_to_label) + 1}"
    period_id_to_label[period_id] = period_label
    period_courses[period_id] = []
    return period_id


def _course_display(course_key):
    """Возвращает отображаемое имя курса."""
    return course_display_names.get(course_key, course_key)


def _course_name(course_key):
    """Извлекает название курса без технического префикса источника."""
    key = _normalize_cell_text(course_key)
    if ":" in key:
        return key.split(":", 1)[1].strip()
    return key


def _course_keys_by_name(course_name):
    """Возвращает ключи курсов с одинаковым названием из разных файлов."""
    target_name = _normalize_cell_text(course_name)
    return [
        course_key
        for course_key in schedule_data.keys()
        if _course_name(course_key) == target_name
    ]


def _period_label_for_course(course_key):
    """Возвращает подпись периода для курса."""
    period_id = course_period_ids.get(course_key)
    if period_id and period_id in period_id_to_label:
        return period_id_to_label[period_id]

    display_name = _course_display(course_key)
    if " | " in display_name:
        return display_name.split(" | ", 1)[0]
    return display_name


def _group_info_for_course_name(course_name, group_code):
    """Ищет информацию о группе по названию курса и коду группы."""
    for course_key in _course_keys_by_name(course_name):
        if course_key in group_info_data and group_code in group_info_data[course_key]:
            return group_info_data[course_key][group_code]
    return [course_name, group_code, "Информация не найдена"]


def _group_search_normalize_text(value):
    """Нормализует текст для поиска группы по команде."""
    return re.sub(r'\s+', '', _search_normalize_text(value))


def _find_group_matches(query_text):
    """Ищет подходящие группы по коду или направлению."""
    normalized_query = _group_search_normalize_text(query_text)
    if not normalized_query:
        return []

    matches = []
    seen = set()

    for course_key in sorted(schedule_data.keys(), key=lambda item: (_course_name(item), item)):
        course_name = _course_name(course_key)

        for group_code in sorted(schedule_data.get(course_key, {}).keys()):
            dedupe_key = (course_name, group_code)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)

            group_normalized = _group_search_normalize_text(group_code)
            group_info = _group_info_for_course_name(course_name, group_code)
            direction = _normalize_cell_text(group_info[2]) if len(group_info) > 2 else ''
            direction_normalized = _search_normalize_text(direction)

            in_group = normalized_query in group_normalized
            in_direction = normalized_query in direction_normalized
            if not in_group and not in_direction:
                continue

            matches.append(
                {
                    'course_name': course_name,
                    'group_code': group_code,
                    'direction': direction,
                    'sort_key': (
                        0 if group_normalized == normalized_query else 1,
                        0 if group_normalized.startswith(normalized_query) else 1,
                        0 if in_group else 1,
                        course_name,
                        group_code,
                    ),
                }
            )

    matches.sort(key=lambda item: item['sort_key'])
    return matches


def _normalize_cell_text(value):
    """Нормализует значение ячейки Excel в строку."""
    if value is None:
        return ""

    if isinstance(value, str):
        return " ".join(value.replace("\n", " ").split())

    return str(value).strip()


def _format_excel_date(value, datemode):
    """Преобразует дату из Excel в строку."""
    if isinstance(value, float):
        try:
            y, m, d, _, _, _ = xlrd.xldate_as_tuple(value, datemode)
            return f"{y:04d}-{m:02d}-{d:02d}"
        except Exception:
            pass

    text_value = _normalize_cell_text(value)
    return text_value if text_value else DEFAULT_DATE


def _format_excel_time(value, datemode):
    """Преобразует время из Excel в строку."""
    if isinstance(value, float):
        try:
            _, _, _, h, m, _ = xlrd.xldate_as_tuple(value, datemode)
            return f"{h:02d}:{m:02d}"
        except Exception:
            pass

    return _normalize_cell_text(value)


def _find_week_label(row_values):
    """Ищет в строке метку недели."""
    for cell_value in row_values:
        text_value = _normalize_cell_text(cell_value)
        if text_value and WEEK_TOKEN in text_value.upper():
            return text_value
    return None





def _week_sort_key(week_label):
    """Возвращает ключ сортировки недель по номеру."""
    week_text = _normalize_cell_text(week_label)
    match = re.search(r"\d+", week_text)
    if match:
        return int(match.group(0)), week_text
    return 10 ** 9, week_text

def parse_excel_file():
    """Парсит все Excel-файлы с расписанием из папки schedules."""
    global schedule_data, group_info_data, week_days_info, course_display_names, course_period_ids, period_id_to_label, period_courses

    excel_files = _discover_excel_files()
    if not excel_files:
        print(f"ERROR: no Excel files found in {SCHEDULES_DIR}")
        return

    print(f"Found Excel files: {len(excel_files)}")
    for file_path in excel_files:
        print(f"  - {file_path.name}")

    for source_index, excel_path in enumerate(excel_files, start=1):
        source_token = f"S{source_index}"
        source_label = _extract_period_label(excel_path.name)
        period_id = _register_period(source_label)

        print(f"Reading workbook: {excel_path.name} ({source_label})")

        try:
            workbook = xlrd.open_workbook(str(excel_path))
        except Exception as open_error:
            print(f"ERROR: cannot open workbook {excel_path.name}: {open_error}")
            continue

        sheets = workbook.sheet_names()
        print(f"Sheets found in {excel_path.name}: {len(sheets)}")

        for sheet_name_raw in sheets:
            course_name = sheet_name_raw.strip()
            course_key = f"{source_token}:{course_name}"
            course_display_names[course_key] = f"{source_label} | {course_name}"
            course_period_ids[course_key] = period_id
            period_courses.setdefault(period_id, []).append(course_key)

            print(f"Processing sheet: {course_display_names[course_key]}")

            try:
                sheet = workbook.sheet_by_name(sheet_name_raw)
            except Exception as sheet_error:
                print(f"  ERROR: cannot read sheet {sheet_name_raw}: {sheet_error}")
                continue

            schedule_data[course_key] = {}
            group_info_data[course_key] = {}
            week_days_info[course_key] = {}

            group_codes = {}

            for row_idx in range(sheet.nrows):
                row = sheet.row_values(row_idx)
                if not any(row):
                    continue

                for col_idx, cell_value in enumerate(row):
                    cell_text = _normalize_cell_text(cell_value)
                    if not cell_text:
                        continue

                    upper_text = cell_text.upper()
                    if not (upper_text.startswith("ИД") or upper_text.startswith("ID")):
                        continue

                    group_code = cell_text
                    if group_code in group_codes:
                        continue

                    group_codes[group_code] = {"col_idx": col_idx}
                    schedule_data[course_key][group_code] = {}

                    direction = DEFAULT_DIRECTION
                    if row_idx + 1 < sheet.nrows:
                        next_row = sheet.row_values(row_idx + 1)
                        if col_idx < len(next_row):
                            direction_value = _normalize_cell_text(next_row[col_idx])
                            if direction_value:
                                direction = direction_value

                    group_info_data[course_key][group_code] = [course_key, group_code, direction]

            print(f"  Groups found: {len(group_codes)}")
            if not group_codes:
                print("  WARNING: no groups found on this sheet")
                continue

            current_week = None
            current_day = None
            current_date = DEFAULT_DATE

            for row_idx in range(sheet.nrows):
                row = sheet.row_values(row_idx)
                if not any(row):
                    continue

                week_label = _find_week_label(row)
                if week_label:
                    current_week = week_label
                    current_day = None
                    current_date = DEFAULT_DATE

                    week_days_info[course_key].setdefault(current_week, {})
                    for group_code in group_codes:
                        schedule_data[course_key][group_code].setdefault(current_week, [])
                    continue

                first_cell = _normalize_cell_text(row[0]) if row else ""
                if first_cell.lower() in DAY_NAMES:
                    current_day = first_cell
                    date_cell = row[1] if len(row) > 1 else None
                    current_date = _format_excel_date(date_cell, workbook.datemode)

                    if current_week:
                        week_days_info[course_key].setdefault(current_week, {})
                        week_days_info[course_key][current_week][current_day] = current_date

                if not current_week or not current_day:
                    continue

                time_cell = row[2] if len(row) > 2 else None
                time_str = _format_excel_time(time_cell, workbook.datemode)
                if not time_str or time_str.lower() == TIME_HEADER:
                    continue

                for group_code, group_data in group_codes.items():
                    col_idx = group_data["col_idx"]
                    if col_idx >= len(row):
                        continue

                    lesson_info = _normalize_cell_text(row[col_idx])
                    if not lesson_info:
                        continue

                    if lesson_info.lower() in IGNORED_LESSON_VALUES:
                        continue

                    lesson_details = ''
                    if row_idx + 1 < sheet.nrows:
                        next_row = sheet.row_values(row_idx + 1)
                        next_first_cell = _normalize_cell_text(next_row[0]) if next_row else ''
                        next_time_cell = next_row[2] if len(next_row) > 2 else None
                        next_time_str = _format_excel_time(next_time_cell, workbook.datemode)
                        next_week_label = _find_week_label(next_row) if next_row else None

                        is_details_row = (
                            not next_week_label
                            and next_first_cell.lower() not in DAY_NAMES
                            and (not next_time_str or next_time_str.lower() == TIME_HEADER)
                        )

                        if is_details_row and col_idx < len(next_row):
                            lesson_details = _normalize_cell_text(next_row[col_idx])
                            if lesson_details.lower() in IGNORED_LESSON_VALUES:
                                lesson_details = ''

                    lesson_text = lesson_info
                    if lesson_details and lesson_details not in lesson_info:
                        lesson_text = f"{lesson_info} | {lesson_details}"

                    schedule_entry = {
                        "day": current_day,
                        "date": current_date,
                        "time": time_str,
                        "lesson": lesson_text,
                        "subject": lesson_info,
                        "details": lesson_details,
                        "week": current_week,
                    }
                    schedule_data[course_key][group_code][current_week].append(schedule_entry)

            total_lessons = sum(
                len(week_schedule)
                for group in schedule_data[course_key].values()
                for week_schedule in group.values()
            )
            print(f"  Lessons loaded: {total_lessons}")

            if total_lessons == 0:
                # Не оставляем в меню курсы без занятий.
                schedule_data.pop(course_key, None)
                group_info_data.pop(course_key, None)
                week_days_info.pop(course_key, None)
                course_display_names.pop(course_key, None)

                period_id_for_course = course_period_ids.pop(course_key, None)
                if period_id_for_course and period_id_for_course in period_courses:
                    period_courses[period_id_for_course] = [
                        item for item in period_courses[period_id_for_course] if item != course_key
                    ]
    empty_period_ids = [period_id for period_id, courses in period_courses.items() if not courses]
    for period_id in empty_period_ids:
        period_courses.pop(period_id, None)
        period_id_to_label.pop(period_id, None)

    print("Parsing Excel files finished")


def init_schedule():
    """Инициализирует и загружает расписание в память."""
    global schedule_data, group_info_data, week_days_info, course_display_names, course_period_ids, period_id_to_label, period_courses
    
    # Очищаем предыдущие данные
    schedule_data.clear()
    group_info_data.clear()
    week_days_info.clear()
    course_display_names.clear()
    course_period_ids.clear()
    period_id_to_label.clear()
    period_courses.clear()
    
    # Пробуем парсить файл
    parse_excel_file()
    
    # Если данные не загружены, используем тестовые данные
    if not schedule_data:
        print("⚠️ Использую тестовые данные...")
        create_test_data()

async def persist_schedule_to_db():
    """Сохраняет текущее расписание из памяти в базу данных."""
    if not schedule_data:
        return {
            'periods': 0,
            'courses': 0,
            'groups': 0,
            'weeks': 0,
            'days': 0,
            'lessons': 0,
        }

    return await db_commands.replace_schedule_snapshot(
        schedule_data=schedule_data,
        group_info_data=group_info_data,
        week_days_info=week_days_info,
        course_display_names=course_display_names,
        course_period_ids=course_period_ids,
        period_id_to_label=period_id_to_label,
    )

def create_test_data():
    """Создает тестовые данные, если файлы расписания недоступны."""
    global schedule_data, group_info_data, week_days_info, course_display_names, course_period_ids, period_id_to_label, period_courses

    course_key = "S1:TestCourse"
    group_key = "ID-TEST-01"

    schedule_data = {
        course_key: {
            group_key: {
                "19 WEEK": [
                    {
                        "day": "monday",
                        "date": "2026-01-05",
                        "time": "08:20-09:50",
                        "lesson": "Test subject",
                        "week": "19 WEEK",
                    }
                ]
            }
        }
    }

    group_info_data = {
        course_key: {
            group_key: [course_key, group_key, "Test direction"],
        }
    }

    week_days_info = {
        course_key: {
            "19 WEEK": {
                "monday": "2026-01-05",
            }
        }
    }

    course_display_names = {
        course_key: "test | course",
    }
    course_period_ids = {
        course_key: "P1",
    }
    period_id_to_label = {
        "P1": "test period",
    }
    period_courses = {
        "P1": [course_key],
    }


WEEKDAY_ORDER = [
    "понедельник",
    "вторник",
    "среда",
    "четверг",
    "пятница",
    "суббота",
]
WEEKDAY_INDEX = {day_name: idx for idx, day_name in enumerate(WEEKDAY_ORDER)}

def _short_day_name(day_name):
    """Возвращает короткое имя дня недели."""
    normalized = _normalize_cell_text(day_name)
    if not normalized:
        return "--"
    return normalized[:2].title()


def _parse_schedule_date(date_text):
    """Преобразует строку даты расписания в datetime."""
    value = _normalize_cell_text(date_text)
    if not value:
        return None

    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue

    return None


def _collect_days_for_week(course, target_week, week_schedule):
    """Собирает полный упорядоченный список дней выбранной недели."""
    day_to_date = {}

    saved_days = week_days_info.get(course, {}).get(target_week, {})
    for day_name, date_value in saved_days.items():
        normalized_day = _normalize_cell_text(day_name).lower()
        normalized_date = _normalize_cell_text(date_value)
        if normalized_day:
            day_to_date[normalized_day] = normalized_date or DEFAULT_DATE

    for entry in week_schedule:
        normalized_day = _normalize_cell_text(entry.get("day")).lower()
        normalized_date = _normalize_cell_text(entry.get("date"))

        if not normalized_day or normalized_day not in DAY_NAMES:
            continue
        if not normalized_date:
            continue

        existing_date = day_to_date.get(normalized_day)
        if not existing_date or existing_date in (DEFAULT_DATE, "Дата не указана"):
            day_to_date[normalized_day] = normalized_date

    known_points = []
    for day_name, date_value in day_to_date.items():
        if day_name not in WEEKDAY_INDEX:
            continue

        parsed_date = _parse_schedule_date(date_value)
        if parsed_date:
            known_points.append((WEEKDAY_INDEX[day_name], parsed_date))

    if known_points:
        monday_candidates = [date_obj - timedelta(days=day_idx) for day_idx, date_obj in known_points]
        monday_date = min(monday_candidates)

        for day_idx, day_name in enumerate(WEEKDAY_ORDER):
            day_to_date.setdefault(day_name, (monday_date + timedelta(days=day_idx)).strftime("%Y-%m-%d"))
    else:
        for day_name in WEEKDAY_ORDER:
            day_to_date.setdefault(day_name, "Дата не указана")

    ordered_days = [
        (day_name, day_to_date.get(day_name, "Дата не указана"))
        for day_name in WEEKDAY_ORDER
    ]

    sunday_name = "воскресенье"
    if sunday_name in day_to_date:
        ordered_days.append((sunday_name, day_to_date[sunday_name]))

    return ordered_days


async def get_day_schedule(course, group_code, week, day_index):
    """Формирует расписание на конкретный день."""

    if course not in schedule_data:
        return f"Курс '{course}' не найден в расписании.", None, None, None

    if group_code not in schedule_data[course]:
        return f"Группа '{group_code}' не найдена в расписании курса '{course}'.", None, None, None

    group_schedule = schedule_data[course][group_code]
    available_weeks = sorted(group_schedule.keys(), key=_week_sort_key)
    if not available_weeks:
        return f"Для группы '{group_code}' нет расписания на какие-либо недели.", None, None, None

    target_week = week if week and week in available_weeks else available_weeks[-1]
    week_schedule = group_schedule.get(target_week, [])
    days_in_week = _collect_days_for_week(course, target_week, week_schedule)

    if not days_in_week:
        return f"Для группы '{group_code}' нет дней в неделе '{target_week}'.", None, None, None

    if day_index is None or day_index >= len(days_in_week):
        current_day_index = 0
    else:
        current_day_index = max(0, min(day_index, len(days_in_week) - 1))

    current_day, current_date = days_in_week[current_day_index]

    schedule_text = f"<b>📅 Расписание для группы {group_code}</b>\n"
    schedule_text += f"<b>🎓 Курс:</b> {_course_display(course)}\n"
    schedule_text += f"<b>🗓️ Неделя:</b> {target_week}\n"
    schedule_text += f"<b>📅 День:</b> {current_day} ({current_date})\n\n"

    day_lessons = []
    for entry in week_schedule:
        if _normalize_cell_text(entry.get("day")).lower() == current_day:
            day_lessons.append(entry)

    day_lessons_sorted = sorted(day_lessons, key=lambda x: x["time"])

    if not day_lessons_sorted:
        schedule_text += "<b>😌 Свободный день!</b>\n"
        schedule_text += "Нет лекций и семинаров\n"
    else:
        for entry in day_lessons_sorted:
            time_display = entry["time"]
            lesson_display = entry["lesson"]
            if len(lesson_display) > 80:
                lesson_display = lesson_display[:77] + "..."
            schedule_text += f"- <b>{time_display}</b> - {lesson_display}\n"

    schedule_text += f"\n<b>День {current_day_index + 1} из {len(days_in_week)}</b>"
    return schedule_text, target_week, current_day_index, days_in_week

def get_group_info(course, group_code):
    """Возвращает информацию о группе."""
    if course in group_info_data and group_code in group_info_data[course]:
        return group_info_data[course][group_code]
    return [course, group_code, "Информация не найдена"]

# Хендлер для выбора курса
async def choose_course(message: types.Message):
    """Показывает пользователю список курсов."""
    keyboard = types.InlineKeyboardMarkup(row_width=2)

    course_names = sorted({_course_name(course_key) for course_key in schedule_data.keys()})

    if not course_names:
        await message.answer("⚠️ Расписание еще не загружено. Попробуйте позже.")
        return

    buttons = [
        types.InlineKeyboardButton(text=name[:64], callback_data=f"course_{name}") 
        for name in course_names
    ]
    keyboard.add(*buttons)

    await message.answer("🎓 Выберите курс:", reply_markup=keyboard)


async def process_course_choice(callback_query: types.CallbackQuery):
    """Обрабатывает выбор курса и показывает группы."""
    course_name = callback_query.data.split("_", 1)[1]

    keyboard = types.InlineKeyboardMarkup(row_width=2)

    groups = set()
    for course_key in _course_keys_by_name(course_name):
        groups.update(schedule_data.get(course_key, {}).keys())

    for group_code in sorted(groups):
        group_info = _group_info_for_course_name(course_name, group_code)
        group_name = f"{group_code}"
        if len(group_info) > 2 and group_info[2]:
            group_name += f" - {group_info[2]}"

        keyboard.add(types.InlineKeyboardButton(
            text=group_name[:50],
            callback_data=f"group_{course_name}_{group_code}"
        ))

    keyboard.add(types.InlineKeyboardButton(
        text="⬅️ Назад к выбору курса",
        callback_data="back_to_courses"
    ))

    await callback_query.message.edit_text(
        f"✅ Выбран курс: {course_name}\n👥 Выберите вашу группу:",
        reply_markup=keyboard
    )
    await callback_query.answer()


async def _safe_edit_text(message: types.Message, text: str, reply_markup=None, parse_mode: str | None = None):
    """Безопасно редактирует сообщение."""
    try:
        await message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except MessageNotModified:
        pass


def _search_normalize_text(value):
    """Нормализует текст для поиска."""
    return _normalize_cell_text(value).casefold().replace('ё', 'е')


def _search_cache_put(payload):
    """Сохраняет payload поиска в кэш и возвращает токен."""
    token = secrets.token_hex(6)
    while token in SEARCH_CALLBACK_CACHE:
        token = secrets.token_hex(6)

    SEARCH_CALLBACK_CACHE[token] = payload

    while len(SEARCH_CALLBACK_CACHE) > SEARCH_CALLBACK_LIMIT:
        first_key = next(iter(SEARCH_CALLBACK_CACHE))
        SEARCH_CALLBACK_CACHE.pop(first_key, None)

    return token


async def _store_search_payload(payload_type, payload):
    """Сохраняет payload поиска в память и базу данных."""
    token = _search_cache_put(payload)
    await db_commands.save_bot_callback_payload(token, payload_type, payload)
    return token


async def _load_search_payload(token, payload_type):
    """Загружает payload поиска из памяти или базы данных."""
    payload = SEARCH_CALLBACK_CACHE.get(token)
    if payload is not None:
        return payload

    payload = await db_commands.get_bot_callback_payload(token, payload_type)
    if payload is not None:
        SEARCH_CALLBACK_CACHE[token] = payload
    return payload


def _extract_subject_from_lesson(lesson_text):
    """Извлекает предмет из текста занятия."""
    text = _normalize_cell_text(lesson_text)
    if not text:
        return ''
    if '|' in text:
        return text.split('|', 1)[0].strip(' ,;')
    return text


def _extract_teacher_from_lesson(lesson_text):
    """Извлекает преподавателя из текста занятия."""
    text = _normalize_cell_text(lesson_text)
    if '|' not in text:
        return ''

    meta = text.split('|', 1)[1].strip()
    meta = ROOM_PATTERN.sub('', meta)
    meta = re.sub(r'\s+', ' ', meta)
    meta = re.sub(r'^[,; ]+|[,; ]+$', '', meta)
    return meta


def _extract_room_from_lesson(lesson_text):
    """Извлекает аудиторию из текста занятия."""
    text = _normalize_cell_text(lesson_text)
    if not text:
        return ''

    rooms = []
    for match in ROOM_PATTERN.findall(text):
        room_text = re.sub(r'\s+', ' ', match).strip(' ,;')
        if room_text and room_text not in rooms:
            rooms.append(room_text)

    return ' / '.join(rooms)


def _collect_search_occurrences():
    """Собирает занятия для поиска по преподавателю, аудитории и предмету."""
    occurrences = []

    for course_key in sorted(schedule_data.keys()):
        for group_code in sorted(schedule_data.get(course_key, {}).keys()):
            weeks = schedule_data.get(course_key, {}).get(group_code, {})
            for week_label in sorted(weeks.keys(), key=_week_sort_key):
                for entry in weeks.get(week_label, []):
                    lesson_text = _normalize_cell_text(entry.get('lesson'))
                    if not lesson_text:
                        continue

                    parsed_date = _parse_schedule_date(entry.get('date'))
                    date_obj = parsed_date.date() if parsed_date else None
                    date_text = _normalize_cell_text(entry.get('date')) or DEFAULT_DATE
                    day_name = _normalize_cell_text(entry.get('day')).lower()

                    occurrences.append(
                        {
                            'course': course_key,
                            'course_name': _course_name(course_key),
                            'period_label': _period_label_for_course(course_key),
                            'group_code': group_code,
                            'week_label': week_label,
                            'day_name': day_name,
                            'date_obj': date_obj,
                            'date_text': date_text,
                            'time_text': _normalize_cell_text(entry.get('time')),
                            'lesson_text': lesson_text,
                            'subject': _extract_subject_from_lesson(lesson_text),
                            'teacher': _extract_teacher_from_lesson(lesson_text),
                            'room': _extract_room_from_lesson(lesson_text),
                        }
                    )

    return occurrences


def _search_entity_matches(search_kind, query_text):
    """Ищет сущности нужного типа по подстроке."""
    normalized_query = _search_normalize_text(query_text)
    if len(normalized_query) < 2:
        return []

    matches = {}
    for occurrence in _collect_search_occurrences():
        entity_value = _normalize_cell_text(occurrence.get(search_kind))
        if not entity_value:
            continue

        normalized_value = _search_normalize_text(entity_value)
        if normalized_query not in normalized_value:
            continue

        item = matches.setdefault(
            entity_value,
            {
                'entity': entity_value,
                'normalized': normalized_value,
                'lessons_count': 0,
                'dates': set(),
                'first_date': occurrence.get('date_obj'),
            },
        )
        item['lessons_count'] += 1
        item['dates'].add(occurrence.get('date_text'))

        current_date = occurrence.get('date_obj')
        if item['first_date'] is None or (current_date is not None and current_date < item['first_date']):
            item['first_date'] = current_date

    result = []
    for item in matches.values():
        item['dates_count'] = len(item['dates'])
        result.append(item)

    result.sort(
        key=lambda item: (
            not item['normalized'].startswith(normalized_query),
            item['normalized'] != normalized_query,
            -item['lessons_count'],
            item['first_date'] is None,
            item['first_date'] or datetime.max.date(),
            item['entity'],
        )
    )
    return result


def _search_occurrences_for_entity(search_kind, entity_value):
    """Возвращает все занятия для выбранной сущности."""
    occurrences = [
        occurrence
        for occurrence in _collect_search_occurrences()
        if _normalize_cell_text(occurrence.get(search_kind)) == _normalize_cell_text(entity_value)
    ]
    occurrences.sort(
        key=lambda item: (
            item['date_obj'] is None,
            item['date_obj'] or datetime.max.date(),
            _normalize_cell_text(item.get('time_text')),
            item.get('group_code'),
            item.get('course_name'),
        )
    )
    return occurrences


def _format_search_date_caption(date_obj, date_text, day_name):
    """Форматирует дату для результатов поиска."""
    if date_obj is not None:
        weekday_names = (
            'понедельник',
            'вторник',
            'среда',
            'четверг',
            'пятница',
            'суббота',
            'воскресенье',
        )
        return f"{date_obj.strftime('%d.%m.%y')} ({weekday_names[date_obj.weekday()]})"
    fallback_day = _normalize_cell_text(day_name)
    if fallback_day:
        return f"{_normalize_cell_text(date_text)} ({fallback_day})"
    return _normalize_cell_text(date_text)


def _group_search_occurrences_by_date(occurrences):
    """Группирует найденные занятия по датам."""
    groups = []
    current_key = None
    current_group = None

    for occurrence in occurrences:
        group_key = (occurrence.get('date_obj'), occurrence.get('date_text'), occurrence.get('day_name'))
        if current_key != group_key:
            current_group = {
                'date_obj': occurrence.get('date_obj'),
                'date_text': occurrence.get('date_text'),
                'day_name': occurrence.get('day_name'),
                'items': [],
            }
            groups.append(current_group)
            current_key = group_key

        current_group['items'].append(occurrence)

    return groups


def _truncate_button_text(text, limit=58):
    """Сокращает подпись inline-кнопки, чтобы она оставалась читабельной с телефона."""
    normalized = _normalize_cell_text(text)
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 1)].rstrip() + '…'


def _nearest_search_page_index(date_groups):
    """Возвращает страницу с датами, которая ближе всего к текущему дню."""
    if not date_groups:
        return 0

    today = datetime.now().date()
    best_group_index = 0
    best_key = None

    for group_index, group in enumerate(date_groups):
        date_obj = group.get('date_obj')
        if date_obj is None:
            continue

        delta_days = (date_obj - today).days
        sort_key = (abs(delta_days), 0 if delta_days >= 0 else 1, group_index)
        if best_key is None or sort_key < best_key:
            best_key = sort_key
            best_group_index = group_index

    return best_group_index // SEARCH_PAGE_SIZE


def _paginate_search_groups(occurrences, page_index):
    """Разбивает результаты поиска на страницы по датам."""
    date_groups = _group_search_occurrences_by_date(occurrences)
    pages_count = max(1, (len(date_groups) + SEARCH_PAGE_SIZE - 1) // SEARCH_PAGE_SIZE)

    if page_index is None:
        page_index = _nearest_search_page_index(date_groups)

    page_index = max(0, min(page_index, pages_count - 1))
    start = page_index * SEARCH_PAGE_SIZE
    end = start + SEARCH_PAGE_SIZE
    page_groups = date_groups[start:end]
    return date_groups, page_groups, page_index, pages_count


def _search_occurrence_button_texts(search_kind, occurrence):
    """Формирует компактные подписи для кнопок найденного занятия."""
    time_text = _normalize_cell_text(occurrence.get('time_text')) or 'Время не указано'
    group_code = _normalize_cell_text(occurrence.get('group_code')) or 'Группа не указана'
    subject = _normalize_cell_text(occurrence.get('subject')) or _normalize_cell_text(occurrence.get('lesson_text'))
    teacher = _normalize_cell_text(occurrence.get('teacher'))
    room = _normalize_cell_text(occurrence.get('room'))

    title_line = _truncate_button_text(f"🕒 {time_text} • 👥 {group_code}", 58)

    details_parts = []
    if search_kind != 'subject' and subject:
        details_parts.append(f"📚 {subject}")
    if search_kind != 'teacher' and teacher:
        details_parts.append(f"👨‍🏫 {teacher}")
    if search_kind != 'room' and room:
        details_parts.append(f"🏫 {room}")

    if not details_parts:
        details_parts.append(f"📚 {subject or 'Информация уточняется'}")

    details_line = _truncate_button_text(' • '.join(details_parts), 62)
    return [title_line, details_line]


def _build_search_entity_text():
    """Возвращает минимальный заголовок экрана результатов поиска."""
    return '<b>🔎 Результаты поиска</b>'


def _build_search_type_keyboard():
    """Строит клавиатуру выбора типа поиска."""
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(types.InlineKeyboardButton(text='👨‍🏫 По преподавателю', callback_data='search_kind_teacher'))
    keyboard.add(types.InlineKeyboardButton(text='🏫 По аудитории', callback_data='search_kind_room'))
    keyboard.add(types.InlineKeyboardButton(text='📚 По предмету', callback_data='search_kind_subject'))
    return keyboard


def _build_search_query_keyboard():
    """Строит клавиатуру экрана ввода поискового запроса."""
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(types.InlineKeyboardButton(text='↩️ К видам поиска', callback_data='search_start'))
    return keyboard


def _build_search_matches_keyboard(match_buttons):
    """Строит клавиатуру списка найденных вариантов."""
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    for button in match_buttons:
        keyboard.add(button)
    keyboard.add(types.InlineKeyboardButton(text='🔎 Новый поиск', callback_data='search_start'))
    return keyboard


def _build_search_entity_keyboard(search_kind, entity_value, token, date_groups, page_groups, page_index, pages_count, lessons_count):
    """Строит клавиатуру с полной выдачей поиска в inline-кнопках."""
    meta = SEARCH_TYPES[search_kind]
    keyboard = types.InlineKeyboardMarkup(row_width=1)

    keyboard.add(
        types.InlineKeyboardButton(
            text=_truncate_button_text(f"{meta['entity_emoji']} {entity_value}", 62),
            callback_data='search_noop',
        )
    )
    keyboard.add(
        types.InlineKeyboardButton(
            text=_truncate_button_text(f"📚 {lessons_count} пар • 📅 {len(date_groups)} дней • 📄 {page_index + 1}/{pages_count}", 62),
            callback_data='search_noop',
        )
    )

    if not page_groups:
        keyboard.add(
            types.InlineKeyboardButton(
                text='📭 По выбранной сущности занятий не найдено',
                callback_data='search_noop',
            )
        )
    else:
        for group in page_groups:
            date_caption = _format_search_date_caption(group.get('date_obj'), group.get('date_text'), group.get('day_name'))
            keyboard.add(
                types.InlineKeyboardButton(
                    text=_truncate_button_text(f"📅 {date_caption}", 62),
                    callback_data='search_noop',
                )
            )
            for occurrence in group.get('items', []):
                for button_text in _search_occurrence_button_texts(search_kind, occurrence):
                    keyboard.add(types.InlineKeyboardButton(text=button_text, callback_data='search_noop'))

    if pages_count > 1:
        keyboard.row(
            types.InlineKeyboardButton(
                text='⬅️',
                callback_data=f'search_page_{token}_{page_index - 1}' if page_index > 0 else 'search_noop',
            ),
            types.InlineKeyboardButton(text=f'📄 {page_index + 1}/{pages_count}', callback_data='search_noop'),
            types.InlineKeyboardButton(
                text='➡️',
                callback_data=f'search_page_{token}_{page_index + 1}'
                if page_index + 1 < pages_count
                else 'search_noop',
            ),
        )

    keyboard.add(types.InlineKeyboardButton(text='🔎 Новый поиск', callback_data='search_start'))
    return keyboard


async def _render_search_start(message_or_callback, state: FSMContext, *, edit: bool):
    """Показывает стартовый экран поиска."""
    await state.finish()
    text = (
        "<b>🔎 Поиск по расписанию</b>\n\n"
        "Можно искать не только по группе, но и по:\n"
        "• преподавателю\n"
        "• аудитории\n"
        "• предмету\n\n"
        "👇 Выберите, что именно хотите найти."
    )

    if edit:
        await _safe_edit_text(
            message_or_callback.message,
            text,
            reply_markup=_build_search_type_keyboard(),
            parse_mode='HTML',
        )
        await message_or_callback.answer()
    else:
        await message_or_callback.answer(text, reply_markup=_build_search_type_keyboard(), parse_mode='HTML')


async def _render_search_entity(message, token, page_index: int | None, *, edit: bool):
    """Показывает страницу найденной сущности."""
    payload = await _load_search_payload(token, 'search_entity')
    if not payload:
        if edit:
            await message.answer('Не удалось открыть сохраненный результат поиска.', show_alert=True)
        else:
            await message.answer('Не удалось открыть сохраненный результат поиска.')
        return False

    search_kind = payload.get('search_kind')
    entity_value = payload.get('entity')
    if search_kind not in SEARCH_TYPES or not entity_value:
        if edit:
            await message.answer('Некорректные данные поиска.', show_alert=True)
        else:
            await message.answer('Некорректные данные поиска.')
        return False

    occurrences = _search_occurrences_for_entity(search_kind, entity_value)
    if not occurrences:
        if edit:
            await message.answer('По выбранной сущности занятий больше не найдено.', show_alert=True)
        else:
            await message.answer('По выбранной сущности занятий больше не найдено.')
        return False

    date_groups, page_groups, page_index, pages_count = _paginate_search_groups(occurrences, page_index)
    text = _build_search_entity_text()
    keyboard = _build_search_entity_keyboard(
        search_kind,
        entity_value,
        token,
        date_groups,
        page_groups,
        page_index,
        pages_count,
        len(occurrences),
    )

    if edit:
        await _safe_edit_text(message.message, text, reply_markup=keyboard, parse_mode='HTML')
        await message.answer()
    else:
        await message.answer(text, reply_markup=keyboard, parse_mode='HTML')

    return True


async def search_entrypoint(message: types.Message, state: FSMContext):
    """Открывает меню поиска."""
    await _render_search_start(message, state, edit=False)


async def search_start_callback(callback_query: types.CallbackQuery, state: FSMContext):
    """Открывает меню поиска по callback-кнопке."""
    await _render_search_start(callback_query, state, edit=True)


async def search_choose_kind(callback_query: types.CallbackQuery, state: FSMContext):
    """Выбирает тип поиска и просит ввести запрос."""
    search_kind = callback_query.data[len('search_kind_'):]
    if search_kind not in SEARCH_TYPES:
        await callback_query.answer('⚠️ Неизвестный тип поиска', show_alert=True)
        return

    await state.set_state(SearchDialog.waiting_query.state)
    await state.update_data(search_kind=search_kind)

    meta = SEARCH_TYPES[search_kind]
    text = (
        f"<b>{meta['title']}</b>\n\n"
        f"{meta['prompt']}\n\n"
        "Я найду подходящие варианты и покажу расписание красиво по датам."
    )
    await _safe_edit_text(
        callback_query.message,
        text,
        reply_markup=_build_search_query_keyboard(),
        parse_mode='HTML',
    )
    await callback_query.answer()


async def _process_search_query(message: types.Message, state: FSMContext, search_kind: str, text_value: str):
    """Обрабатывает текстовый запрос поиска и показывает результаты."""
    if search_kind not in SEARCH_TYPES:
        await _render_search_start(message, state, edit=False)
        return

    normalized_text = _normalize_cell_text(text_value)
    meta = SEARCH_TYPES[search_kind]
    if not normalized_text:
        await message.answer(
            f"⚠️ После команды укажите запрос.\nПример: <code>{meta['command_example']}</code>",
            parse_mode='HTML',
        )
        return

    matches = _search_entity_matches(search_kind, normalized_text)
    if not matches:
        await message.answer(
            f"😕 {meta['empty']}\n\nПопробуйте написать короче или точнее.\n{meta['prompt']}",
            parse_mode='HTML',
        )
        return

    await state.finish()

    if len(matches) == 1:
        token = await _store_search_payload(
            'search_entity',
            {
                'search_kind': search_kind,
                'entity': matches[0]['entity'],
            },
        )
        await _render_search_entity(message, token, None, edit=False, show_empty=False)
        return

    buttons = []
    for match in matches[:10]:
        token = await _store_search_payload(
            'search_entity',
            {
                'search_kind': search_kind,
                'entity': match['entity'],
            },
        )
        button_text = (
            f"{meta['entity_emoji']} {match['entity']} · "
            f"{match['dates_count']} дн. / {match['lessons_count']} пар"
        )
        buttons.append(
            types.InlineKeyboardButton(
                text=button_text[:64],
                callback_data=f'search_entity_{token}',
            )
        )

    lines = [
        f"<b>{meta['title']}</b>",
        f"🔎 Запрос: <b>{html.escape(normalized_text)}</b>",
        '',
        f"Найдено вариантов: <b>{len(matches)}</b>",
        '👇 Выберите нужный вариант:',
    ]
    if len(matches) > 10:
        lines.append('')
        lines.append('Показываю первые 10 совпадений. Если нужно, уточните запрос.')

    await message.answer(
        '\n'.join(lines),
        reply_markup=_build_search_matches_keyboard(buttons),
        parse_mode='HTML',
    )


async def search_receive_query(message: types.Message, state: FSMContext):
    """Принимает текст запроса для поиска."""
    text_value = _normalize_cell_text(message.text)
    if not text_value:
        await message.answer('Введите текст для поиска.')
        return

    if text_value == '📅 Мое расписание':
        await state.finish()
        await my_schedule(message, state)
        return
    if text_value == '💼 Настройки':
        await state.finish()
        await settings(message, state)
        return
    if text_value == '🔎 Поиск':
        await _render_search_start(message, state, edit=False)
        return
    if _search_normalize_text(text_value) in {'отмена', 'cancel'}:
        await state.finish()
        await message.answer('Поиск отменен.', reply_markup=get_main_keyboard())
        return

    state_data = await state.get_data()
    search_kind = state_data.get('search_kind')
    await _process_search_query(message, state, search_kind, text_value)


async def _handle_search_command(message: types.Message, state: FSMContext, search_kind: str):
    """Обрабатывает slash-команду поиска с аргументом."""
    await state.finish()
    await _process_search_query(message, state, search_kind, message.get_args())


async def search_teacher_command(message: types.Message, state: FSMContext):
    """Ищет расписание преподавателя по команде /teacher."""
    await _handle_search_command(message, state, 'teacher')


async def search_room_command(message: types.Message, state: FSMContext):
    """Ищет расписание аудитории по команде /room."""
    await _handle_search_command(message, state, 'room')


async def search_subject_command(message: types.Message, state: FSMContext):
    """Ищет расписание предмета по команде /subject."""
    await _handle_search_command(message, state, 'subject')


async def group_command(message: types.Message, state: FSMContext):
    """Выбирает группу по команде /group."""
    await state.finish()
    query_text = _normalize_cell_text(message.get_args())
    if not query_text:
        await message.answer(
            "⚠️ После команды укажите код группы.\nПример: <code>/group ИД 30.1/Б3-22</code>",
            parse_mode='HTML',
        )
        return

    matches = _find_group_matches(query_text)
    if not matches:
        await message.answer(
            f"😕 По запросу <b>{html.escape(query_text)}</b> группы не найдены.",
            parse_mode='HTML',
        )
        return

    if len(matches) == 1:
        match = matches[0]
        selected_course, selected_week_index = _resolve_group_selection(match['course_name'], match['group_code'])
        if not selected_course:
            await message.answer('📭 Для выбранной группы нет расписания.')
            return

        week_payload = build_week_schedule_payload(selected_course, match['group_code'], selected_week_index)
        if week_payload is None:
            await message.answer('📭 Для выбранной группы нет расписания.')
            return

        await _update_user_selection(
            state,
            _telegram_id_from_message(message),
            selected_course_name=match['course_name'],
            selected_course=selected_course,
            selected_group=match['group_code'],
            selected_week_index=week_payload['selected_week_index'],
            selected_day_index=None,
        )
        await message.answer(week_payload['text'], reply_markup=week_payload['keyboard'], parse_mode='HTML')
        return

    keyboard = types.InlineKeyboardMarkup(row_width=1)
    for match in matches[:10]:
        button_text = f"👥 {match['group_code']} · {match['course_name']}"
        keyboard.add(
            types.InlineKeyboardButton(
                text=button_text[:64],
                callback_data=f"group_{match['course_name']}_{match['group_code']}",
            )
        )

    lines = [
        "<b>👥 Выбор группы</b>",
        f"🔎 Запрос: <b>{html.escape(query_text)}</b>",
        '',
        f"Найдено вариантов: <b>{len(matches)}</b>",
        '👇 Выберите нужную группу:',
    ]
    if len(matches) > 10:
        lines.append('')
        lines.append('Показываю первые 10 совпадений. Уточните запрос, если нужно.')

    await message.answer('\n'.join(lines), reply_markup=keyboard, parse_mode='HTML')


async def date_command(message: types.Message, state: FSMContext):
    """Открывает расписание по конкретной дате для выбранной группы."""
    await state.finish()
    query_text = _normalize_cell_text(message.get_args())
    if not query_text:
        await message.answer(
            "⚠️ После команды укажите дату.\nПример: <code>/date 14.03.26</code>",
            parse_mode='HTML',
        )
        return

    target_date = _parse_schedule_date(query_text)
    if target_date is None:
        await message.answer(
            "⚠️ Не удалось распознать дату.\nПоддерживаются форматы: <code>14.03.26</code>, <code>14.03.2026</code>, <code>2026-03-14</code>.",
            parse_mode='HTML',
        )
        return

    user_data = await _get_user_data_with_db_fallback(state, _telegram_id_from_message(message))
    course = user_data.get('selected_course')
    group_code = user_data.get('selected_group')

    if not course or not group_code:
        await message.answer(
            "ℹ️ Сначала выберите группу.\nПример: <code>/group ИД 30.1/Б3-22</code>",
            parse_mode='HTML',
        )
        return

    course_name = user_data.get('selected_course_name') or _course_name(course)
    day_payload = build_day_schedule_payload_by_date(course_name, group_code, target_date)
    if day_payload is None:
        await message.answer(
            f"📭 На {target_date.strftime('%d.%m.%y')} для группы {group_code} нет занятий или дата еще не опубликована."
        )
        return

    await _update_user_selection(
        state,
        _telegram_id_from_message(message),
        selected_course_name=course_name,
        selected_course=day_payload['selected_course'],
        selected_group=group_code,
        selected_week_index=day_payload['selected_week_index'],
        selected_day_index=day_payload['selected_day_index'],
    )
    await message.answer(day_payload['text'], reply_markup=day_payload['keyboard'], parse_mode='HTML')


async def search_open_entity(callback_query: types.CallbackQuery, state: FSMContext):
    """Открывает расписание выбранной найденной сущности."""
    token = callback_query.data[len('search_entity_'):]
    await _render_search_entity(callback_query, token, None, edit=True, show_empty=False)


async def search_change_page(callback_query: types.CallbackQuery, state: FSMContext):
    """Листает страницы найденной сущности."""
    payload = callback_query.data[len('search_page_'):]
    token, separator, page_text = payload.rpartition('_')
    if not separator:
        await callback_query.answer('⚠️ Некорректная страница', show_alert=True)
        return

    try:
        page_index = int(page_text)
    except ValueError:
        await callback_query.answer('⚠️ Некорректная страница', show_alert=True)
        return

    await _render_search_entity(callback_query, token, page_index, edit=True)


async def search_noop(callback_query: types.CallbackQuery):
    """Служебная кнопка поиска."""
    await callback_query.answer()


def _search_month_label_v2(month_key):
    """Возвращает подпись месяца для нового календаря поиска."""
    if not month_key:
        return 'Месяц не указан'

    year, month = month_key
    month_names = [
        'Январь', 'Февраль', 'Март', 'Апрель',
        'Май', 'Июнь', 'Июль', 'Август',
        'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь',
    ]
    return f"{month_names[month - 1]} {year}"


def _search_week_month_key_v2(week_days):
    """Определяет месяц недели по первому дню с занятиями, иначе по первой дате."""
    for day_item in week_days:
        if day_item.get('has_lessons') and day_item.get('date_obj') is not None:
            day_date = day_item['date_obj']
            return day_date.year, day_date.month

    for day_item in week_days:
        if day_item.get('date_obj') is not None:
            day_date = day_item['date_obj']
            return day_date.year, day_date.month

    return None


def _build_search_weeks_v2(occurrences):
    """Строит непрерывную шкалу недель для найденной сущности."""
    dated_occurrences = [item for item in occurrences if item.get('date_obj') is not None]
    if not dated_occurrences:
        return [], []

    date_to_entries = {}
    for occurrence in dated_occurrences:
        date_to_entries.setdefault(occurrence['date_obj'], []).append(occurrence)

    for entries in date_to_entries.values():
        entries.sort(
            key=lambda item: (
                _normalize_cell_text(item.get('time_text')),
                _normalize_cell_text(item.get('group_code')),
                _normalize_cell_text(item.get('course_name')),
            )
        )

    all_dates = sorted(date_to_entries)
    first_date = all_dates[0]
    last_date = all_dates[-1]

    timeline_start = first_date - timedelta(days=first_date.weekday())
    timeline_end = last_date + timedelta(days=(6 - last_date.weekday()))
    today = datetime.now().date()

    weeks = []
    current_start = timeline_start
    while current_start <= timeline_end:
        week_days = []
        for offset, day_name in enumerate(WEEKDAY_ORDER):
            day_date = current_start + timedelta(days=offset)
            entries = date_to_entries.get(day_date, [])
            week_days.append(
                {
                    'day_name': day_name,
                    'day_title': day_name.capitalize(),
                    'date_obj': day_date,
                    'date_short': day_date.strftime('%d.%m.%y'),
                    'has_lessons': bool(entries),
                    'is_today': day_date == today,
                    'entries': entries,
                }
            )

        sunday_date = current_start + timedelta(days=6)
        sunday_entries = date_to_entries.get(sunday_date, [])
        if sunday_entries:
            week_days.append(
                {
                    'day_name': 'воскресенье',
                    'day_title': 'Воскресенье',
                    'date_obj': sunday_date,
                    'date_short': sunday_date.strftime('%d.%m.%y'),
                    'has_lessons': True,
                    'is_today': sunday_date == today,
                    'entries': sunday_entries,
                }
            )

        weeks.append(
            {
                'week_index': len(weeks),
                'start_date': current_start,
                'week_days': week_days,
            }
        )
        current_start += timedelta(days=7)

    months = []
    month_to_index = {}
    for week in weeks:
        month_key = _search_week_month_key_v2(week['week_days'])
        week['month_key'] = month_key

        if month_key not in month_to_index:
            month_to_index[month_key] = len(months)
            months.append(
                {
                    'month_index': len(months),
                    'month_key': month_key,
                    'label': _search_month_label_v2(month_key),
                    'week_indices': [],
                }
            )

        month_index = month_to_index[month_key]
        week['month_index'] = month_index
        months[month_index]['week_indices'].append(week['week_index'])

    for month in months:
        for week_number, week_index in enumerate(month['week_indices'], start=1):
            weeks[week_index]['month_week_number'] = week_number

    return weeks, months


def _pick_search_initial_week_index_v2(weeks):
    """Выбирает неделю поиска, ближайшую к сегодняшней дате."""
    if not weeks:
        return 0

    nearest_day_ref = _pick_search_nearest_day_ref_v2(weeks)
    return nearest_day_ref[0] if nearest_day_ref else 0


def _pick_search_week_index_for_month_v2(weeks, month_item):
    """Выбирает стартовую неделю для выбранного месяца."""
    week_indices = month_item.get('week_indices', [])
    if not week_indices:
        return 0

    nearest_day_ref = _pick_search_nearest_day_ref_v2(weeks, allowed_week_indices=week_indices)
    return nearest_day_ref[0] if nearest_day_ref else week_indices[0]


def _search_month_index_for_week_v2(weeks, week_index):
    """Возвращает индекс месяца для текущей недели поиска."""
    if not weeks:
        return 0

    week_index = max(0, min(week_index, len(weeks) - 1))
    return weeks[week_index].get('month_index', 0)


def _search_lesson_day_refs_v2(weeks, allowed_week_indices=None):
    """Возвращает все дни с занятиями в формате (week_index, day_index, date_obj)."""
    allowed_set = set(allowed_week_indices) if allowed_week_indices is not None else None
    refs = []

    for week in weeks:
        week_index = week.get('week_index', 0)
        if allowed_set is not None and week_index not in allowed_set:
            continue

        for day_index, day_item in enumerate(week.get('week_days', [])):
            if not day_item.get('has_lessons'):
                continue

            date_obj = day_item.get('date_obj')
            if date_obj is None:
                continue

            refs.append((week_index, day_index, date_obj))

    refs.sort(key=lambda item: (item[2], item[0], item[1]))
    return refs


def _pick_search_nearest_day_ref_v2(weeks, allowed_week_indices=None):
    """Возвращает ближайший к сегодняшней дате день с занятиями."""
    refs = _search_lesson_day_refs_v2(weeks, allowed_week_indices=allowed_week_indices)
    if not refs:
        return None

    today = datetime.now().date()
    return min(
        refs,
        key=lambda item: (
            abs((item[2] - today).days),
            item[2] < today,
            item[2],
            item[0],
            item[1],
        ),
    )


def _pick_search_next_day_ref_v2(weeks, allowed_week_indices=None):
    """Возвращает ближайший следующий день с занятиями, начиная с сегодняшней даты."""
    refs = _search_lesson_day_refs_v2(weeks, allowed_week_indices=allowed_week_indices)
    if not refs:
        return None

    today = datetime.now().date()
    for ref in refs:
        if ref[2] >= today:
            return ref
    return None


def _search_show_empty_flag(value):
    """Преобразует флаг показа пустых дней к bool."""
    return str(value) == '1'


def _search_week_callback_data(token, week_index, show_empty):
    """Собирает callback открытия недели поиска."""
    return f"search_week_{token}_{week_index}_{int(show_empty)}"


def _search_shift_callback_data(token, week_index, delta, show_empty):
    """Собирает callback перелистывания недель поиска."""
    return f"search_shift_{token}_{week_index}_{delta}_{int(show_empty)}"


def _search_day_callback_data(token, week_index, day_index, show_empty):
    """Собирает callback открытия дня поиска."""
    return f"search_day_{token}_{week_index}_{day_index}_{int(show_empty)}"


def _search_nearest_callback_data(token, show_empty):
    """Собирает callback открытия ближайшего следующего дня поиска."""
    return f"search_nearest_{token}_{int(show_empty)}"


def _search_parse_week_payload(payload):
    """Разбирает callback недели поиска с поддержкой старого формата."""
    parts = payload.rsplit('_', 2)
    if len(parts) == 3 and parts[2] in {'0', '1'}:
        token, week_index_text, show_empty_text = parts
        return token, int(week_index_text), _search_show_empty_flag(show_empty_text)

    token, separator, week_index_text = payload.rpartition('_')
    if not separator:
        raise ValueError
    return token, int(week_index_text), False


def _search_parse_shift_payload(payload):
    """Разбирает callback перелистывания недели поиска с поддержкой старого формата."""
    parts = payload.rsplit('_', 3)
    if len(parts) == 4 and parts[3] in {'0', '1'}:
        token, week_index_text, delta_text, show_empty_text = parts
        return token, int(week_index_text), int(delta_text), _search_show_empty_flag(show_empty_text)

    parts = payload.rsplit('_', 2)
    if len(parts) != 3:
        raise ValueError
    token, week_index_text, delta_text = parts
    return token, int(week_index_text), int(delta_text), False


def _search_parse_day_payload(payload):
    """Разбирает callback дня поиска с поддержкой старого формата."""
    parts = payload.rsplit('_', 3)
    if len(parts) == 4 and parts[3] in {'0', '1'}:
        token, week_index_text, day_index_text, show_empty_text = parts
        return token, int(week_index_text), int(day_index_text), _search_show_empty_flag(show_empty_text)

    parts = payload.rsplit('_', 2)
    if len(parts) != 3:
        raise ValueError
    token, week_index_text, day_index_text = parts
    return token, int(week_index_text), int(day_index_text), False


def _search_parse_nearest_payload(payload):
    """Разбирает callback ближайшего дня поиска с поддержкой старого формата."""
    token, separator, show_empty_text = payload.rpartition('_')
    if separator and show_empty_text in {'0', '1'}:
        return token, _search_show_empty_flag(show_empty_text)
    return payload, False


def _find_search_week_with_lessons_v2(weeks, week_index, delta):
    """Находит соседнюю неделю, где есть хотя бы один день с занятиями."""
    if not weeks or delta == 0:
        return None

    step = 1 if delta > 0 else -1
    target_week_index = week_index + step
    while 0 <= target_week_index < len(weeks):
        week_item = weeks[target_week_index]
        if any(day.get('has_lessons') for day in week_item.get('week_days', [])):
            return target_week_index
        target_week_index += step

    return None


def _find_search_adjacent_day_ref_v2(weeks, week_index, day_index, delta):
    """Находит соседний день с занятиями, пропуская пустые даты и недели."""
    refs = _search_lesson_day_refs_v2(weeks)
    if not refs or delta == 0:
        return None

    step = 1 if delta > 0 else -1
    for position, (ref_week_index, ref_day_index, _) in enumerate(refs):
        if ref_week_index == week_index and ref_day_index == day_index:
            target_position = position + step
            if 0 <= target_position < len(refs):
                target_ref = refs[target_position]
                return target_ref[0], target_ref[1]
            return None

    current_date = None
    if 0 <= week_index < len(weeks):
        week_days = weeks[week_index].get('week_days', [])
        if 0 <= day_index < len(week_days):
            current_date = week_days[day_index].get('date_obj')

    if current_date is None:
        target_ref = refs[0] if step > 0 else refs[-1]
        return target_ref[0], target_ref[1]

    if step > 0:
        for ref_week_index, ref_day_index, ref_date in refs:
            if ref_date > current_date or (
                ref_date == current_date and (ref_week_index, ref_day_index) > (week_index, day_index)
            ):
                return ref_week_index, ref_day_index
        return None

    for ref_week_index, ref_day_index, ref_date in reversed(refs):
        if ref_date < current_date or (
            ref_date == current_date and (ref_week_index, ref_day_index) < (week_index, day_index)
        ):
            return ref_week_index, ref_day_index
    return None


def _find_search_adjacent_calendar_day_ref_v2(weeks, week_index, day_index, delta):
    """Находит соседний день по календарю, включая пустые даты."""
    if not weeks or delta == 0:
        return None

    step = 1 if delta > 0 else -1
    current_week_index = week_index
    current_day_index = day_index + step

    while 0 <= current_week_index < len(weeks):
        week_days = weeks[current_week_index].get('week_days', [])
        if 0 <= current_day_index < len(week_days):
            return current_week_index, current_day_index

        current_week_index += step
        if not (0 <= current_week_index < len(weeks)):
            break

        next_week_days = weeks[current_week_index].get('week_days', [])
        current_day_index = 0 if step > 0 else len(next_week_days) - 1

    return None


def _build_search_week_overview_text_v2(search_kind, entity_value, week_item):
    """Формирует страницу недели для найденной сущности."""
    meta = SEARCH_TYPES[search_kind]
    month_label = _search_month_label_v2(week_item.get('month_key'))
    week_range = _week_date_range_text(week_item['week_days'])
    lessons_days = sum(1 for item in week_item['week_days'] if item.get('has_lessons'))

    lines = [
        f"<b>{meta['title']}</b>",
        f"{meta['entity_emoji']} {meta['entity_label']}: <b>{html.escape(entity_value)}</b>",
        f"🗓️ Месяц: <b>{html.escape(month_label)}</b>",
        f"📆 Неделя месяца: <b>{week_item.get('month_week_number', '-')}</b> ({week_range})",
        f"📚 Дней с занятиями: <b>{lessons_days}</b>",
        '',
        '👇 Нажмите на нужный день.',
    ]
    return '\n'.join(lines)


def _search_empty_day_text_v2(search_kind):
    """Возвращает текст для пустого дня поиска."""
    if search_kind == 'teacher':
        return '😌 В этот день у преподавателя занятий нет.'
    if search_kind == 'room':
        return '😌 В этот день аудитория свободна.'
    return '😌 В этот день по этому предмету занятий нет.'


def _search_lesson_lines_v2(search_kind, occurrence):
    """Формирует строки одного занятия для детального экрана дня."""
    time_text = html.escape(_normalize_cell_text(occurrence.get('time_text')) or 'Время не указано')
    course_name = html.escape(_normalize_cell_text(occurrence.get('course_name')) or 'Курс не указан')
    group_code = html.escape(_normalize_cell_text(occurrence.get('group_code')) or 'Группа не указана')
    subject = html.escape(
        _normalize_cell_text(occurrence.get('subject')) or _normalize_cell_text(occurrence.get('lesson_text')) or 'Без названия'
    )
    teacher = html.escape(_normalize_cell_text(occurrence.get('teacher')))
    room = html.escape(_normalize_cell_text(occurrence.get('room')))
    period_label = html.escape(_normalize_cell_text(occurrence.get('period_label')))

    lines = [
        f"• ⏰ <b>{time_text}</b>",
        f"  🎓 {course_name} | 👥 {group_code}",
        f"  📚 {subject}",
    ]

    if teacher and search_kind != 'teacher':
        lines.append(f"  👨‍🏫 {teacher}")
    if room and search_kind != 'room':
        lines.append(f"  🏫 {room}")
    if period_label:
        lines.append(f"  🗂️ {period_label}")

    return lines


def _build_search_day_view_text_v2(search_kind, entity_value, week_item, day_index):
    """Формирует подробную страницу дня для найденной сущности."""
    week_days = week_item['week_days']
    if not week_days:
        return '⚠️ Неделя не содержит дней', 0

    day_index = day_index % len(week_days)
    day_item = week_days[day_index]
    meta = SEARCH_TYPES[search_kind]

    header_day = f"{day_item['date_short']} ({day_item['day_title']})"
    if day_item['is_today']:
        header_day += ' | Сегодня'

    month_label = _search_month_label_v2(week_item.get('month_key'))
    week_range = _week_date_range_text(week_days)
    lines = [
        f"<b>{meta['title']}</b>",
        f"{meta['entity_emoji']} {meta['entity_label']}: <b>{html.escape(entity_value)}</b>",
        f"🗓️ Месяц: <b>{html.escape(month_label)}</b>",
        f"📆 Неделя месяца: <b>{week_item.get('month_week_number', '-')}</b> ({week_range})",
        f"📅 День: <b>{html.escape(header_day)}</b>",
        '',
    ]

    entries = day_item.get('entries', [])
    if not entries:
        lines.append(_search_empty_day_text_v2(search_kind))
    else:
        for occurrence in entries:
            lines.extend(_search_lesson_lines_v2(search_kind, occurrence))
            lines.append('')

    return '\n'.join(line for line in lines if line is not None).strip(), day_index


def _build_search_week_keyboard_v2(token, week_item, show_empty):
    """Строит клавиатуру недели для найденной сущности."""
    week_index = week_item['week_index']
    keyboard = types.InlineKeyboardMarkup(row_width=1)

    for day_index, day_item in enumerate(week_item['week_days']):
        if not show_empty and not day_item.get('has_lessons'):
            continue

        day_button_text = f"{day_item['date_short']} ({day_item['day_title']})"
        if day_item['is_today']:
            day_button_text += ' | Сегодня 💠'
        elif show_empty and not day_item.get('has_lessons'):
            day_button_text += ' | Нет занятий ⚠️'

        keyboard.add(
            types.InlineKeyboardButton(
                text=day_button_text[:64],
                callback_data=_search_day_callback_data(token, week_index, day_index, show_empty),
            )
        )

    if not show_empty and not any(day.get('has_lessons') for day in week_item['week_days']):
        keyboard.add(types.InlineKeyboardButton(text='😌 Нет пар', callback_data='search_noop'))

    keyboard.row(
        types.InlineKeyboardButton(text='⬅️ Назад', callback_data=_search_shift_callback_data(token, week_index, -1, show_empty)),
        types.InlineKeyboardButton(text=f"📆 Неделя {week_item.get('month_week_number', '-')}"[:64], callback_data='search_noop'),
        types.InlineKeyboardButton(text='Вперед ➡️', callback_data=_search_shift_callback_data(token, week_index, 1, show_empty)),
    )
    keyboard.row(
        types.InlineKeyboardButton(
            text='🙈 Скрыть пустые' if show_empty else '👀 Показать пустые',
            callback_data=f"search_toggleweek_{token}_{week_index}_{0 if show_empty else 1}",
        ),
        types.InlineKeyboardButton(text='🎯 Ближайший день', callback_data=_search_nearest_callback_data(token, show_empty)),
    )
    keyboard.row(
        types.InlineKeyboardButton(text='🔎 Новый поиск', callback_data='search_start'),
    )
    return keyboard


def _build_search_day_keyboard_v2(token, weeks, week_index, selected_day_index, show_empty):
    """Строит клавиатуру дня для найденной сущности."""
    week_item = weeks[week_index]
    week_days = week_item['week_days']
    keyboard = types.InlineKeyboardMarkup(row_width=3)
    if show_empty:
        previous_day_ref = _find_search_adjacent_calendar_day_ref_v2(weeks, week_index, selected_day_index, -1)
        next_day_ref = _find_search_adjacent_calendar_day_ref_v2(weeks, week_index, selected_day_index, 1)
    else:
        previous_day_ref = _find_search_adjacent_day_ref_v2(weeks, week_index, selected_day_index, -1)
        next_day_ref = _find_search_adjacent_day_ref_v2(weeks, week_index, selected_day_index, 1)

    keyboard.row(
        types.InlineKeyboardButton(
            text='⬅️ Предыдущий день',
            callback_data=(
                _search_day_callback_data(token, previous_day_ref[0], previous_day_ref[1], show_empty)
                if previous_day_ref
                else 'search_noop'
            ),
        ),
        types.InlineKeyboardButton(
            text='Следующий день ➡️',
            callback_data=(
                _search_day_callback_data(token, next_day_ref[0], next_day_ref[1], show_empty)
                if next_day_ref
                else 'search_noop'
            ),
        ),
    )

    day_buttons = []
    for day_index, day_item in enumerate(week_days):
        if not show_empty and not day_item.get('has_lessons'):
            continue

        button_text = f"{_short_day_name(day_item['day_name'])} {day_item['date_short'][:5]}"
        if day_index == selected_day_index:
            button_text = f"* {button_text}"
        elif day_item['is_today']:
            button_text = f"{button_text} *"

        day_buttons.append(
            types.InlineKeyboardButton(
                text=button_text[:64],
                callback_data=_search_day_callback_data(token, week_index, day_index, show_empty),
            )
        )


    keyboard.row(
        types.InlineKeyboardButton(text='🎯 Ближайший день', callback_data=_search_nearest_callback_data(token, show_empty)),
    )
    keyboard.row(
        types.InlineKeyboardButton(text='↩️ Назад к неделе', callback_data=_search_week_callback_data(token, week_index, show_empty)),
    )

    keyboard.row(types.InlineKeyboardButton(text='🔎 Новый поиск', callback_data='search_start'))
    return keyboard


def _build_search_week_picker_keyboard_v2(token, weeks, months, current_week_index, current_month_index):
    """Строит клавиатуру выбора недели для найденной сущности."""
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    if not months:
        return keyboard

    current_month_index = max(0, min(current_month_index, len(months) - 1))
    month_item = months[current_month_index]

    for week_index in month_item['week_indices']:
        week_item = weeks[week_index]
        range_text = _week_date_range_text(week_item['week_days'])
        selected_prefix = '• ' if week_index == current_week_index else ''
        button_text = f"{selected_prefix}📆 Неделя {week_item.get('month_week_number', '-')} ({range_text})"
        keyboard.add(
            types.InlineKeyboardButton(
                text=button_text[:64],
                callback_data=f"search_weeksel_{token}_{week_index}",
            )
        )

    keyboard.row(
        types.InlineKeyboardButton(
            text='⬅️ Назад',
            callback_data=f"search_monthview_{token}_{current_month_index - 1}" if current_month_index > 0 else 'search_noop',
        ),
        types.InlineKeyboardButton(text=f"🗓️ {month_item['label']}"[:64], callback_data='search_noop'),
        types.InlineKeyboardButton(
            text='Вперед ➡️',
            callback_data=f"search_monthview_{token}_{current_month_index + 1}"
            if current_month_index + 1 < len(months)
            else 'search_noop',
        ),
    )
    keyboard.row(
        types.InlineKeyboardButton(text='↩️ К неделе', callback_data=f"search_week_{token}_{current_week_index}"),
        types.InlineKeyboardButton(text='🗓️ Все месяцы', callback_data=f"search_monthpick_{token}_{current_week_index}"),
    )
    keyboard.add(types.InlineKeyboardButton(text='🔎 Новый поиск', callback_data='search_start'))
    return keyboard


def _build_search_month_picker_keyboard_v2(token, months, current_month_index, current_week_index):
    """Строит клавиатуру выбора месяца для найденной сущности."""
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    for month_item in months:
        selected_prefix = '• ' if month_item['month_index'] == current_month_index else ''
        button_text = f"{selected_prefix}🗓️ {month_item['label']}"
        keyboard.add(
            types.InlineKeyboardButton(
                text=button_text[:64],
                callback_data=f"search_monthsel_{token}_{month_item['month_index']}",
            )
        )

    keyboard.add(types.InlineKeyboardButton(text='↩️ К неделе', callback_data=f"search_week_{token}_{current_week_index}"))
    keyboard.add(types.InlineKeyboardButton(text='🔎 Новый поиск', callback_data='search_start'))
    return keyboard


async def _load_search_entity_context_v2(target, token, *, edit: bool):
    """Загружает найденную сущность и строит календарную структуру поиска."""
    payload = await _load_search_payload(token, 'search_entity')
    if not payload:
        if edit:
            await target.answer('Не удалось открыть сохраненный результат поиска.', show_alert=True)
        else:
            await target.answer('Не удалось открыть сохраненный результат поиска.')
        return None

    search_kind = payload.get('search_kind')
    entity_value = payload.get('entity')
    if search_kind not in SEARCH_TYPES or not entity_value:
        if edit:
            await target.answer('Некорректные данные поиска.', show_alert=True)
        else:
            await target.answer('Некорректные данные поиска.')
        return None

    occurrences = _search_occurrences_for_entity(search_kind, entity_value)
    if not occurrences:
        if edit:
            await target.answer('По выбранной сущности занятий больше не найдено.', show_alert=True)
        else:
            await target.answer('По выбранной сущности занятий больше не найдено.')
        return None

    weeks, months = _build_search_weeks_v2(occurrences)
    if not weeks:
        if edit:
            await target.answer('У найденной сущности нет дат для построения расписания.', show_alert=True)
        else:
            await target.answer('У найденной сущности нет дат для построения расписания.')
        return None

    return {
        'search_kind': search_kind,
        'entity_value': entity_value,
        'occurrences': occurrences,
        'weeks': weeks,
        'months': months,
    }

async def _render_search_entity(message, token, week_index: int | None, *, edit: bool, show_empty: bool = False):
    """Открывает найденную сущность в формате недельного расписания."""
    context = await _load_search_entity_context_v2(message, token, edit=edit)
    if not context:
        return False

    weeks = context['weeks']
    if week_index is None:
        week_index = _pick_search_initial_week_index_v2(weeks)

    week_index = max(0, min(week_index, len(weeks) - 1))
    week_item = weeks[week_index]
    text = _build_search_week_overview_text_v2(context['search_kind'], context['entity_value'], week_item)
    keyboard = _build_search_week_keyboard_v2(token, week_item, show_empty)

    if edit:
        await _safe_edit_text(message.message, text, reply_markup=keyboard, parse_mode='HTML')
        await message.answer()
    else:
        await message.answer(text, reply_markup=keyboard, parse_mode='HTML')

    return True


async def _render_search_day_view_v2(callback_query: types.CallbackQuery, token, week_index, day_index, show_empty: bool = False):
    """Показывает подробный экран дня для найденной сущности."""
    context = await _load_search_entity_context_v2(callback_query, token, edit=True)
    if not context:
        return

    weeks = context['weeks']
    week_index = max(0, min(week_index, len(weeks) - 1))
    week_item = weeks[week_index]
    week_days = week_item['week_days']

    if not week_days:
        await callback_query.answer('⚠️ Неделя не содержит дней', show_alert=True)
        return

    text, selected_day_index = _build_search_day_view_text_v2(
        context['search_kind'],
        context['entity_value'],
        week_item,
        day_index,
    )
    keyboard = _build_search_day_keyboard_v2(token, weeks, week_index, selected_day_index, show_empty)

    await _safe_edit_text(callback_query.message, text, reply_markup=keyboard, parse_mode='HTML')
    await callback_query.answer()


async def _render_search_week_picker_v2(callback_query: types.CallbackQuery, token, week_index, month_index: int | None = None):
    """Показывает выбор недели для найденной сущности."""
    context = await _load_search_entity_context_v2(callback_query, token, edit=True)
    if not context:
        return

    weeks = context['weeks']
    months = context['months']
    week_index = max(0, min(week_index, len(weeks) - 1))

    if month_index is None:
        month_index = _search_month_index_for_week_v2(weeks, week_index)
    month_index = max(0, min(month_index, len(months) - 1))

    month_item = months[month_index]
    meta = SEARCH_TYPES[context['search_kind']]
    text = (
        f"<b>{meta['title']}</b>\n"
        f"{meta['entity_emoji']} {meta['entity_label']}: <b>{html.escape(context['entity_value'])}</b>\n"
        f"🗓️ Месяц: <b>{html.escape(month_item['label'])}</b>\n\n"
        "👇 Выберите неделю."
    )
    keyboard = _build_search_week_picker_keyboard_v2(token, weeks, months, week_index, month_index)
    await _safe_edit_text(callback_query.message, text, reply_markup=keyboard, parse_mode='HTML')
    await callback_query.answer()


async def _render_search_month_picker_v2(callback_query: types.CallbackQuery, token, week_index):
    """Показывает выбор месяца для найденной сущности."""
    context = await _load_search_entity_context_v2(callback_query, token, edit=True)
    if not context:
        return

    weeks = context['weeks']
    months = context['months']
    week_index = max(0, min(week_index, len(weeks) - 1))
    current_month_index = _search_month_index_for_week_v2(weeks, week_index)
    meta = SEARCH_TYPES[context['search_kind']]

    text = (
        f"<b>{meta['title']}</b>\n"
        f"{meta['entity_emoji']} {meta['entity_label']}: <b>{html.escape(context['entity_value'])}</b>\n\n"
        "👇 Выберите месяц."
    )
    keyboard = _build_search_month_picker_keyboard_v2(token, months, current_month_index, week_index)
    await _safe_edit_text(callback_query.message, text, reply_markup=keyboard, parse_mode='HTML')
    await callback_query.answer()


async def search_open_entity(callback_query: types.CallbackQuery, state: FSMContext):
    """Открывает расписание выбранной найденной сущности."""
    token = callback_query.data[len('search_entity_'):]
    await _render_search_entity(callback_query, token, None, edit=True)


async def search_open_week(callback_query: types.CallbackQuery, state: FSMContext):
    """Открывает выбранную неделю найденной сущности."""
    payload = callback_query.data[len('search_week_'):]
    try:
        token, week_index, show_empty = _search_parse_week_payload(payload)
    except ValueError:
        await callback_query.answer('⚠️ Некорректная неделя', show_alert=True)
        return

    await _render_search_entity(callback_query, token, week_index, edit=True, show_empty=show_empty)


async def search_shift_week(callback_query: types.CallbackQuery, state: FSMContext):
    """Переключает неделю поиска вперед или назад."""
    payload = callback_query.data[len('search_shift_'):]
    try:
        token, week_index, delta, show_empty = _search_parse_shift_payload(payload)
    except ValueError:
        await callback_query.answer('⚠️ Некорректный шаг недели', show_alert=True)
        return

    context = await _load_search_entity_context_v2(callback_query, token, edit=True)
    if not context:
        return

    week_index = max(0, min(week_index, len(context['weeks']) - 1))
    if show_empty:
        target_week_index = week_index + delta
        if target_week_index < 0:
            target_week_index = None
        elif target_week_index >= len(context['weeks']):
            target_week_index = None
    else:
        target_week_index = _find_search_week_with_lessons_v2(context['weeks'], week_index, delta)
    if delta < 0 and target_week_index is None:
        await callback_query.answer('📭 Предыдущих недель больше нет.', show_alert=True)
        return
    if delta > 0 and target_week_index is None:
        await callback_query.answer(
            '📭 Следующих недель нет. Как они появятся, мы вам обязательно сообщим.',
            show_alert=True,
        )
        return

    await _render_search_entity(callback_query, token, target_week_index, edit=True, show_empty=show_empty)


async def search_open_day(callback_query: types.CallbackQuery, state: FSMContext):
    """Открывает день найденной сущности со сквозной навигацией между неделями."""
    payload = callback_query.data[len('search_day_'):]
    try:
        token, week_index, day_index, show_empty = _search_parse_day_payload(payload)
    except ValueError:
        await callback_query.answer('⚠️ Некорректный день', show_alert=True)
        return

    context = await _load_search_entity_context_v2(callback_query, token, edit=True)
    if not context:
        return

    weeks = context['weeks']
    if not weeks:
        await callback_query.answer('📭 Для выбранной сущности нет расписания', show_alert=True)
        return

    week_index = max(0, min(week_index, len(weeks) - 1))
    current_week_days = weeks[week_index]['week_days']

    if 0 <= day_index < len(current_week_days):
        if show_empty or current_week_days[day_index].get('has_lessons'):
            await _render_search_day_view_v2(callback_query, token, week_index, day_index, show_empty=show_empty)
            return

    direction = -1 if day_index < 0 else 1
    anchor_day_index = 0 if day_index < 0 else max(len(current_week_days) - 1, 0)
    if show_empty:
        target_day_ref = _find_search_adjacent_calendar_day_ref_v2(weeks, week_index, anchor_day_index, direction)
    else:
        target_day_ref = _find_search_adjacent_day_ref_v2(weeks, week_index, anchor_day_index, direction)
    if target_day_ref:
        await _render_search_day_view_v2(
            callback_query,
            token,
            target_day_ref[0],
            target_day_ref[1],
            show_empty=show_empty,
        )
        return

    if direction < 0:
        await callback_query.answer('📭 Предыдущих дней больше нет.', show_alert=True)
        return

    await callback_query.answer(
        '📭 Следующих дней нет. Как они появятся, мы вам обязательно сообщим.',
        show_alert=True,
    )


async def search_open_nearest_days(callback_query: types.CallbackQuery, state: FSMContext):
    """Открывает ближайший следующий день с занятиями."""
    payload = callback_query.data[len('search_nearest_'):]
    token, show_empty = _search_parse_nearest_payload(payload)
    context = await _load_search_entity_context_v2(callback_query, token, edit=True)
    if not context:
        return

    nearest_day_ref = _pick_search_next_day_ref_v2(context['weeks'])
    if not nearest_day_ref:
        await callback_query.answer('📭 Ближайших следующих дней с занятиями пока нет.', show_alert=True)
        return

    await _render_search_day_view_v2(
        callback_query,
        token,
        nearest_day_ref[0],
        nearest_day_ref[1],
        show_empty=show_empty,
    )


async def search_toggle_empty_week(callback_query: types.CallbackQuery, state: FSMContext):
    """Переключает показ пустых дней на экране недели поиска."""
    payload = callback_query.data[len('search_toggleweek_'):]
    parts = payload.rsplit('_', 2)
    if len(parts) != 3:
        await callback_query.answer('⚠️ Некорректный режим отображения', show_alert=True)
        return

    token, week_index_text, show_empty_text = parts
    try:
        week_index = int(week_index_text)
    except ValueError:
        await callback_query.answer('⚠️ Некорректный режим отображения', show_alert=True)
        return

    show_empty = _search_show_empty_flag(show_empty_text)
    context = await _load_search_entity_context_v2(callback_query, token, edit=True)
    if not context:
        return

    weeks = context['weeks']
    week_index = max(0, min(week_index, len(weeks) - 1))
    if not show_empty and not any(day.get('has_lessons') for day in weeks[week_index]['week_days']):
        week_index = _pick_search_initial_week_index_v2(weeks)

    await _render_search_entity(callback_query, token, week_index, edit=True, show_empty=show_empty)


async def search_toggle_empty_day(callback_query: types.CallbackQuery, state: FSMContext):
    """Переключает показ пустых дней на экране дня поиска."""
    payload = callback_query.data[len('search_toggleday_'):]
    parts = payload.rsplit('_', 3)
    if len(parts) != 4:
        await callback_query.answer('⚠️ Некорректный режим отображения', show_alert=True)
        return

    token, week_index_text, day_index_text, show_empty_text = parts
    try:
        week_index = int(week_index_text)
        day_index = int(day_index_text)
    except ValueError:
        await callback_query.answer('⚠️ Некорректный режим отображения', show_alert=True)
        return

    show_empty = _search_show_empty_flag(show_empty_text)
    context = await _load_search_entity_context_v2(callback_query, token, edit=True)
    if not context:
        return

    weeks = context['weeks']
    week_index = max(0, min(week_index, len(weeks) - 1))
    week_days = weeks[week_index]['week_days']

    if show_empty:
        if not week_days:
            await callback_query.answer('📭 Для выбранной недели нет дней.', show_alert=True)
            return
        day_index = max(0, min(day_index, len(week_days) - 1))
        await _render_search_day_view_v2(callback_query, token, week_index, day_index, show_empty=True)
        return

    if 0 <= day_index < len(week_days) and week_days[day_index].get('has_lessons'):
        await _render_search_day_view_v2(callback_query, token, week_index, day_index, show_empty=False)
        return

    nearest_day_ref = _pick_search_next_day_ref_v2(weeks) or _pick_search_nearest_day_ref_v2(weeks)
    if not nearest_day_ref:
        await callback_query.answer('📭 Для выбранной сущности нет дней с занятиями.', show_alert=True)
        return

    await _render_search_day_view_v2(
        callback_query,
        token,
        nearest_day_ref[0],
        nearest_day_ref[1],
        show_empty=False,
    )


async def search_pick_week(callback_query: types.CallbackQuery, state: FSMContext):
    """Открывает выбор недели для найденной сущности."""
    payload = callback_query.data[len('search_weekpick_'):]
    token, separator, week_index_text = payload.rpartition('_')
    if not separator:
        await callback_query.answer('⚠️ Некорректный выбор недели', show_alert=True)
        return

    try:
        week_index = int(week_index_text)
    except ValueError:
        await callback_query.answer('⚠️ Некорректный выбор недели', show_alert=True)
        return

    await _render_search_week_picker_v2(callback_query, token, week_index)


async def search_choose_week(callback_query: types.CallbackQuery, state: FSMContext):
    """Выбирает неделю из списка для найденной сущности."""
    payload = callback_query.data[len('search_weeksel_'):]
    token, separator, week_index_text = payload.rpartition('_')
    if not separator:
        await callback_query.answer('⚠️ Некорректная неделя', show_alert=True)
        return

    try:
        week_index = int(week_index_text)
    except ValueError:
        await callback_query.answer('⚠️ Некорректная неделя', show_alert=True)
        return

    await _render_search_entity(callback_query, token, week_index, edit=True)


async def search_pick_month(callback_query: types.CallbackQuery, state: FSMContext):
    """Открывает выбор месяца для найденной сущности."""
    payload = callback_query.data[len('search_monthpick_'):]
    token, separator, week_index_text = payload.rpartition('_')
    if not separator:
        await callback_query.answer('⚠️ Некорректный выбор месяца', show_alert=True)
        return

    try:
        week_index = int(week_index_text)
    except ValueError:
        await callback_query.answer('⚠️ Некорректный выбор месяца', show_alert=True)
        return

    await _render_search_month_picker_v2(callback_query, token, week_index)


async def search_view_month(callback_query: types.CallbackQuery, state: FSMContext):
    """Листает месяцы на экране выбора недели для найденной сущности."""
    payload = callback_query.data[len('search_monthview_'):]
    token, separator, month_index_text = payload.rpartition('_')
    if not separator:
        await callback_query.answer('⚠️ Некорректный месяц', show_alert=True)
        return

    try:
        month_index = int(month_index_text)
    except ValueError:
        await callback_query.answer('⚠️ Некорректный месяц', show_alert=True)
        return

    context = await _load_search_entity_context_v2(callback_query, token, edit=True)
    if not context:
        return

    months = context['months']
    if not months:
        await callback_query.answer('📭 Для выбранной сущности нет месяцев', show_alert=True)
        return

    if month_index < 0 or month_index >= len(months):
        await callback_query.answer('📭 Больше месяцев нет.', show_alert=True)
        return

    current_week_index = _pick_search_week_index_for_month_v2(context['weeks'], months[month_index])
    await _render_search_week_picker_v2(callback_query, token, current_week_index, month_index)


async def search_choose_month(callback_query: types.CallbackQuery, state: FSMContext):
    """Выбирает месяц и открывает ближайшую неделю этого месяца."""
    payload = callback_query.data[len('search_monthsel_'):]
    token, separator, month_index_text = payload.rpartition('_')
    if not separator:
        await callback_query.answer('⚠️ Некорректный месяц', show_alert=True)
        return

    try:
        month_index = int(month_index_text)
    except ValueError:
        await callback_query.answer('⚠️ Некорректный месяц', show_alert=True)
        return

    context = await _load_search_entity_context_v2(callback_query, token, edit=True)
    if not context:
        return

    months = context['months']
    if month_index < 0 or month_index >= len(months):
        await callback_query.answer('⚠️ Некорректный месяц', show_alert=True)
        return

    week_index = _pick_search_week_index_for_month_v2(context['weeks'], months[month_index])
    await _render_search_entity(callback_query, token, week_index, edit=True)


def _build_period_to_course(course_name, group_code):
    """Строит связку периода и курса для группы."""
    period_items = []
    seen_periods = set()

    course_keys = sorted(
        _course_keys_by_name(course_name),
        key=lambda item: (
            _course_start_date(item, group_code) is None,
            _course_start_date(item, group_code) or datetime.max.date(),
            _period_label_for_course(item),
        ),
    )

    for course_key in course_keys:
        if group_code not in schedule_data.get(course_key, {}):
            continue

        period_label = _period_label_for_course(course_key)
        if period_label in seen_periods:
            continue

        seen_periods.add(period_label)
        period_items.append((period_label, course_key))

    return period_items


def _resolve_group_selection(course_name, group_code):
    """Подбирает период и стартовую неделю для выбранной группы."""
    period_to_course = _build_period_to_course(course_name, group_code)
    if not period_to_course:
        return None, None

    selected_course = None
    selected_week_index = 0

    for _, course_key in period_to_course:
        weeks = _get_available_weeks(course_key, group_code)
        if not weeks:
            continue
        if _period_has_current_month(course_key, group_code, weeks):
            selected_course = course_key
            selected_week_index = _pick_initial_week_index(course_key, group_code, weeks)
            break

    if selected_course is None:
        selected_course = period_to_course[0][1]
        weeks = _get_available_weeks(selected_course, group_code)
        if weeks:
            selected_week_index = _pick_initial_week_index(selected_course, group_code, weeks)

    return selected_course, selected_week_index


def _get_available_weeks(course, group_code):
    """Возвращает отсортированные недели группы."""
    if course not in schedule_data:
        return []
    if group_code not in schedule_data[course]:
        return []
    return sorted(schedule_data[course][group_code].keys(), key=_week_sort_key)


def _week_dates(week_days):
    """Возвращает отсортированные даты недели."""
    return sorted(item['date_obj'] for item in week_days if item['date_obj'] is not None)


def _course_start_date(course, group_code):
    """Возвращает дату начала периода для группы."""
    weeks = _get_available_weeks(course, group_code)
    start_dates = []

    for week_label in weeks:
        week_days = _build_week_day_items(course, group_code, week_label)
        dates = _week_dates(week_days)
        if dates:
            start_dates.append(dates[0])

    if start_dates:
        return min(start_dates)
    return None


def _week_number_text(week_label):
    """Извлекает номер недели из заголовка XLS (служебно)."""
    week_text = _normalize_cell_text(week_label)
    for token in week_text.replace('-', ' ').split():
        if token.isdigit():
            return token
    return week_text


def _build_week_day_items(course, group_code, week_label):
    """Формирует список дней недели с датами."""
    week_schedule = schedule_data.get(course, {}).get(group_code, {}).get(week_label, [])
    days_in_week = _collect_days_for_week(course, week_label, week_schedule)
    today = datetime.now().date()

    items = []
    for day_name, date_text in days_in_week:
        parsed_date = _parse_schedule_date(date_text)
        day_date = parsed_date.date() if parsed_date else None
        has_lessons = any(
            _normalize_cell_text(entry.get('day')).lower() == day_name
            for entry in week_schedule
        )
        date_short = parsed_date.strftime('%d.%m.%y') if parsed_date else _normalize_cell_text(date_text)

        items.append(
            {
                'day_name': day_name,
                'day_title': day_name.capitalize(),
                'date_obj': day_date,
                'date_short': date_short,
                'has_lessons': has_lessons,
                'is_today': day_date == today,
            }
        )

    return items


def _month_week_number(week_days):
    """Возвращает номер недели в месяце по первой дате недели."""
    dates = _week_dates(week_days)
    if not dates:
        return None

    reference_date = dates[0]
    return ((reference_date.day - 1) // 7) + 1


def _month_week_number_text(week_days):
    """Текстовое представление номера недели месяца."""
    month_week_number = _month_week_number(week_days)
    if month_week_number is None:
        return '-'
    return str(month_week_number)


def _build_group_week_timeline(course_name, group_code):
    """Строит непрерывную шкалу недель группы по всем периодам."""
    timeline = []
    course_keys = sorted(
        _course_keys_by_name(course_name),
        key=lambda item: (
            _course_start_date(item, group_code) is None,
            _course_start_date(item, group_code) or datetime.max.date(),
            _period_label_for_course(item),
        ),
    )

    for course_key in course_keys:
        weeks = _get_available_weeks(course_key, group_code)
        for week_index, week_label in enumerate(weeks):
            week_days = _build_week_day_items(course_key, group_code, week_label)
            dates = _week_dates(week_days)
            start_date = dates[0] if dates else None

            timeline.append(
                {
                    'course': course_key,
                    'week_label': week_label,
                    'week_index': week_index,
                    'start_date': start_date,
                }
            )

    timeline.sort(
        key=lambda item: (
            item['start_date'] is None,
            item['start_date'] or datetime.max.date(),
            item['course'],
            item['week_index'],
        )
    )

    return timeline


def _find_day_by_date(course_name, group_code, target_date):
    """Ищет день расписания по конкретной дате среди всех периодов группы."""
    if isinstance(target_date, datetime):
        target_date = target_date.date()

    timeline = _build_group_week_timeline(course_name, group_code)

    for item in timeline:
        week_days = _build_week_day_items(item['course'], group_code, item['week_label'])
        for day_index, day_item in enumerate(week_days):
            if day_item['date_obj'] == target_date:
                return {
                    'course': item['course'],
                    'week_index': item['week_index'],
                    'week_label': item['week_label'],
                    'day_index': day_index,
                    'week_days': week_days,
                }

    return None

def _pick_today_week_for_group(course_name, group_code):
    """Выбирает неделю, в которую попадает сегодняшняя дата."""
    today = datetime.now().date()
    timeline = _build_group_week_timeline(course_name, group_code)

    if not timeline:
        return None

    nearest_item = None
    nearest_key = None

    for item in timeline:
        week_days = _build_week_day_items(item['course'], group_code, item['week_label'])
        dates = _week_dates(week_days)
        if not dates:
            continue

        week_start = dates[0]
        week_end = dates[-1]

        if week_start <= today <= (week_end + timedelta(days=1)):
            return item['course'], item['week_index']

        if today < week_start:
            distance = (week_start - today).days
        else:
            distance = (today - week_end).days

        candidate_key = (distance, week_start)
        if nearest_key is None or candidate_key < nearest_key:
            nearest_key = candidate_key
            nearest_item = item

    if nearest_item is not None:
        return nearest_item['course'], nearest_item['week_index']

    first_item = timeline[0]
    return first_item['course'], first_item['week_index']

def _period_has_current_month(course, group_code, weeks):
    """Проверяет, есть ли в периоде недели текущего месяца."""
    today = datetime.now().date()
    for week_label in weeks:
        for day_item in _build_week_day_items(course, group_code, week_label):
            day_date = day_item['date_obj']
            if day_date and day_date.year == today.year and day_date.month == today.month:
                return True
    return False

def _pick_initial_week_index(course, group_code, weeks):
    """Выбирает стартовую неделю."""
    if not weeks:
        return 0

    today = datetime.now().date()
    if not _period_has_current_month(course, group_code, weeks):
        return 0

    for index, week_label in enumerate(weeks):
        week_days = _build_week_day_items(course, group_code, week_label)
        dates = [item['date_obj'] for item in week_days if item['date_obj'] is not None]
        if not dates:
            continue

        week_start = min(dates)
        week_end = max(dates)
        if week_start <= today <= (week_end + timedelta(days=1)):
            return index

    for index, week_label in enumerate(weeks):
        week_days = _build_week_day_items(course, group_code, week_label)
        if any(
            item['date_obj']
            and item['date_obj'].year == today.year
            and item['date_obj'].month == today.month
            for item in week_days
        ):
            return index

    return 0


def _week_date_range_text(week_days):
    """Форматирует диапазон дат недели."""
    dates = sorted(item['date_obj'] for item in week_days if item['date_obj'] is not None)
    if not dates:
        return 'даты не указаны'
    return f"{dates[0].strftime('%d.%m')} - {dates[-1].strftime('%d.%m')}"


def _get_day_lessons(week_schedule, day_name):
    """Возвращает занятия выбранного дня."""
    lessons = [
        entry
        for entry in week_schedule
        if _normalize_cell_text(entry.get('day')).lower() == day_name
    ]
    return sorted(lessons, key=lambda item: _normalize_cell_text(item.get('time')))


def _build_week_overview_text(course, group_code, week_label, week_days):
    """Формирует текст страницы недели."""
    month_week_number = _month_week_number_text(week_days)
    week_range = _week_date_range_text(week_days)

    lines = [
        f"<b>{html.escape(_period_label_for_course(course))}</b>\n",
        f"🎓 Курс: <b>{html.escape(_course_name(course))}</b>\n",
        f"👥 Группа: <b>{html.escape(group_code)}</b>\n",
        f"🗓️ Неделя месяца: <b>{month_week_number}</b> ({week_range})\n\n",
    ]

    lines.append('👇 Нажмите, чтобы открыть расписание.')
    return ''.join(lines)


def _build_day_view_text(course, group_code, week_label, week_days, day_index):
    """Формирует текст страницы дня."""
    if not week_days:
        return '⚠️ Неделя не содержит дней', 0

    day_index = day_index % len(week_days)
    day_item = week_days[day_index]

    week_schedule = schedule_data.get(course, {}).get(group_code, {}).get(week_label, [])
    lessons = _get_day_lessons(week_schedule, day_item['day_name'])

    header_day = f"{day_item['date_short']} ({day_item['day_title']})"
    if day_item['is_today']:
        header_day += ' | Сегодня'

    month_week_number = _month_week_number_text(week_days)
    week_range = _week_date_range_text(week_days)

    lines = [
        f"🎓 Курс: <b>{html.escape(_course_name(course))}</b>",
        f"👥 Группа: <b>{html.escape(group_code)}</b>",
        f"📅 День: <b>{html.escape(header_day)}</b>",
        f"🗓️ Неделя месяца: <b>{month_week_number}</b> ({week_range})",
        '',
    ]

    if not lessons:
        lines.append('😌 Свободный день: занятий нет')
    else:
        for lesson in lessons:
            time_text = html.escape(_normalize_cell_text(lesson.get('time')) or 'Время не указано')
            lesson_text = html.escape(_normalize_cell_text(lesson.get('lesson')) or 'Без названия')
            if len(lesson_text) > 180:
                lesson_text = f"{lesson_text[:177]}..."
            lines.append(f"• ⏰ <b>{time_text}</b> {lesson_text}")

    return '\n'.join(lines), day_index


def build_day_schedule_payload_by_date(course_name, group_code, target_date):
    """Возвращает данные страницы дня по конкретной дате."""
    if isinstance(target_date, datetime):
        target_date = target_date.date()

    target_day = _find_day_by_date(course_name, group_code, target_date)
    if target_day is None:
        return None

    text, day_index = _build_day_view_text(
        target_day['course'],
        group_code,
        target_day['week_label'],
        target_day['week_days'],
        target_day['day_index'],
    )

    return {
        'selected_course': target_day['course'],
        'selected_week_index': target_day['week_index'],
        'selected_day_index': day_index,
        'week_label': target_day['week_label'],
        'week_days': target_day['week_days'],
        'text': text,
        'keyboard': _build_day_keyboard(target_day['week_days'], day_index),
    }


def build_week_schedule_payload(course, group_code, week_index=None):
    """Возвращает данные страницы недели."""
    weeks = _get_available_weeks(course, group_code)
    if not weeks:
        return None

    if week_index is None:
        week_index = 0

    week_index = week_index % len(weeks)
    week_label = weeks[week_index]
    week_days = _build_week_day_items(course, group_code, week_label)

    return {
        'selected_course': course,
        'selected_group': group_code,
        'selected_week_index': week_index,
        'week_label': week_label,
        'week_days': week_days,
        'text': _build_week_overview_text(course, group_code, week_label, week_days),
        'keyboard': _build_week_keyboard(week_label, week_days),
    }

def _build_week_keyboard(week_label, week_days):
    """Строит клавиатуру страницы недели."""
    keyboard = types.InlineKeyboardMarkup(row_width=1)

    for day_index, day_item in enumerate(week_days):
        day_button_text = f"{day_item['date_short']} ({day_item['day_title']})"
        if day_item['is_today']:
            day_button_text += ' | Сегодня 💠'

        keyboard.add(
            types.InlineKeyboardButton(
                text=day_button_text[:64],
                callback_data=f"cal_day_{day_index}",
            )
        )

    month_week_number = _month_week_number_text(week_days)
    keyboard.row(
        types.InlineKeyboardButton(text='⬅️ Назад', callback_data='cal_shift_-1'),
        types.InlineKeyboardButton(text=f"📆 Неделя {month_week_number}"[:64], callback_data='cal_noop'),
        types.InlineKeyboardButton(text='Вперед ➡️', callback_data='cal_shift_1'),
    )

    keyboard.row(
        types.InlineKeyboardButton(text='🗂️ Выбор недели', callback_data='cal_pick_week'),
        types.InlineKeyboardButton(text='🗓️ Выбор месяца', callback_data='cal_pick_month'),
    )

    return keyboard

def _build_day_keyboard(week_days, selected_day_index):
    """Строит клавиатуру страницы дня."""
    keyboard = types.InlineKeyboardMarkup(row_width=6)

    prev_day_index = selected_day_index - 1
    next_day_index = selected_day_index + 1

    keyboard.row(
        types.InlineKeyboardButton(text='⬅️ Предыдущий день', callback_data=f"cal_day_{prev_day_index}"),
        types.InlineKeyboardButton(text='Следующий день ➡️', callback_data=f"cal_day_{next_day_index}"),
    )

    day_buttons = []
    for day_index, day_item in enumerate(week_days):
        short_name = _short_day_name(day_item['day_name'])
        button_text = f"{short_name} {day_item['date_short'][:5]}"

        if day_index == selected_day_index:
            button_text = f"* {button_text}"
        elif day_item['is_today']:
            button_text = f"{button_text} *"

        day_buttons.append(
            types.InlineKeyboardButton(
                text=button_text[:64],
                callback_data=f"cal_day_{day_index}",
            )
        )



    keyboard.row(types.InlineKeyboardButton(text='↩️ Назад к неделе', callback_data='cal_back_week'))

    return keyboard

def _build_week_picker_keyboard(course, group_code, weeks, current_week_index):
    """Строит клавиатуру выбора недели."""
    keyboard = types.InlineKeyboardMarkup(row_width=2)

    if not weeks:
        return keyboard

    current_week_index = current_week_index % len(weeks)

    for week_index, week_label in enumerate(weeks):
        week_days = _build_week_day_items(course, group_code, week_label)
        range_text = _week_date_range_text(week_days)
        month_week_number = _month_week_number_text(week_days)
        selected_prefix = '* ' if week_index == current_week_index else ''
        button_text = f"{selected_prefix}📆 Неделя {month_week_number} ({range_text})"

        keyboard.add(
            types.InlineKeyboardButton(
                text=button_text[:64],
                callback_data=f"cal_weekpick_{week_index}",
            )
        )

    current_week_days = _build_week_day_items(course, group_code, weeks[current_week_index])
    current_dates = _week_dates(current_week_days)
    month_names = [
        'Январь', 'Февраль', 'Март', 'Апрель',
        'Май', 'Июнь', 'Июль', 'Август',
        'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь',
    ]

    if current_dates:
        month_date = current_dates[0]
        month_text = f"🗓️ {month_names[month_date.month - 1]} {month_date.year}"
    else:
        month_text = '🗓️ Месяц'

    keyboard.row(
        types.InlineKeyboardButton(text='⬅️ Назад', callback_data='cal_month_shift_-1'),
        types.InlineKeyboardButton(text=month_text[:64], callback_data='cal_noop'),
        types.InlineKeyboardButton(text='Вперед ➡️', callback_data='cal_month_shift_1'),
    )

    keyboard.row(
        types.InlineKeyboardButton(text='🎯 Перейти к нынешней неделе', callback_data='cal_current_week'),
    )


    return keyboard

async def _render_period_picker(callback_query: types.CallbackQuery, state: FSMContext, course_name: str, group_code: str):
    """Показывает выбор месяца или периода."""
    await _update_user_selection(
        state,
        _telegram_id_from_callback(callback_query),
        selected_course_name=course_name,
        selected_group=group_code,
    )

    period_to_course = _build_period_to_course(course_name, group_code)
    if not period_to_course:
        return False

    keyboard = types.InlineKeyboardMarkup(row_width=1)
    group_info = _group_info_for_course_name(course_name, group_code)
    group_display = f"{group_code}"
    if len(group_info) > 2 and group_info[2]:
        group_display += f"\n📚 Направление: {group_info[2]}"

    for period_label, course_key in period_to_course:
        keyboard.add(
            types.InlineKeyboardButton(
                text=f"{period_label}"[:64],
                callback_data=f"show_schedule_{course_key}_{group_code}",
            )
        )

    keyboard.add(
        types.InlineKeyboardButton(
            text='👥 Выбрать другую группу',
            callback_data=f"course_{course_name}",
        )
    )
    keyboard.add(
        types.InlineKeyboardButton(
            text='🏠 В главное меню',
            callback_data='main_menu',
        )
    )

    await _safe_edit_text(
        callback_query.message,
        f"<b>✅ Вы выбрали:</b>\n"
        f"🎓 Курс: {html.escape(course_name)}\n"
        f"👥 Группа: {html.escape(group_display)}\n\n"
        f"🗓️ Теперь выберите месяц/период:",
        reply_markup=keyboard,
        parse_mode='HTML',
    )
    return True

async def _render_week_view(callback_query: types.CallbackQuery, state: FSMContext, week_index: int | None = None):
    """Показывает страницу недели."""
    user_data = await _get_user_data_with_db_fallback(state, _telegram_id_from_callback(callback_query))
    course = user_data.get('selected_course')
    group_code = user_data.get('selected_group')

    if not course or not group_code:
        await callback_query.answer('⚠️ Сначала выберите курс, группу и месяц', show_alert=True)
        return

    weeks = _get_available_weeks(course, group_code)
    if not weeks:
        await callback_query.answer('📭 Для выбранной группы нет расписания', show_alert=True)
        return

    if week_index is None:
        week_index = user_data.get('selected_week_index', 0)

    week_index = week_index % len(weeks)
    week_label = weeks[week_index]
    week_days = _build_week_day_items(course, group_code, week_label)

    await _update_user_selection(
        state,
        _telegram_id_from_callback(callback_query),
        selected_week_index=week_index,
    )

    text = _build_week_overview_text(course, group_code, week_label, week_days)
    keyboard = _build_week_keyboard(week_label, week_days)

    await _safe_edit_text(callback_query.message, text, reply_markup=keyboard, parse_mode='HTML')
    await callback_query.answer()


async def _render_day_view(callback_query: types.CallbackQuery, state: FSMContext, day_index: int | None = None):
    """Показывает страницу дня."""
    user_data = await _get_user_data_with_db_fallback(state, _telegram_id_from_callback(callback_query))
    course = user_data.get('selected_course')
    group_code = user_data.get('selected_group')

    if not course or not group_code:
        await callback_query.answer('⚠️ Сначала выберите курс, группу и месяц', show_alert=True)
        return

    weeks = _get_available_weeks(course, group_code)
    if not weeks:
        await callback_query.answer('📭 Для выбранной группы нет расписания', show_alert=True)
        return

    week_index = user_data.get('selected_week_index', 0) % len(weeks)
    week_label = weeks[week_index]
    week_days = _build_week_day_items(course, group_code, week_label)

    if not week_days:
        await callback_query.answer('⚠️ Неделя не содержит дней', show_alert=True)
        return

    if day_index is None:
        day_index = user_data.get('selected_day_index')

    if day_index is None:
        today_index = next((idx for idx, item in enumerate(week_days) if item['is_today']), None)
        day_index = today_index if today_index is not None else 0

    text, day_index = _build_day_view_text(course, group_code, week_label, week_days, day_index)

    await _update_user_selection(
        state,
        _telegram_id_from_callback(callback_query),
        selected_week_index=week_index,
        selected_day_index=day_index,
    )

    keyboard = _build_day_keyboard(week_days, day_index)

    await _safe_edit_text(
        callback_query.message,
        text,
        reply_markup=keyboard,
        parse_mode='HTML',
    )
    await callback_query.answer()


async def _show_relative_day_schedule(message: types.Message, state: FSMContext, day_shift: int):
    """Показывает расписание на сегодня или завтра по сохраненной группе пользователя."""
    user_data = await _get_user_data_with_db_fallback(state, _telegram_id_from_message(message))
    course = user_data.get('selected_course')
    group_code = user_data.get('selected_group')

    if not course or not group_code:
        await message.answer("ℹ️ Сначала выберите группу. Давайте выберем её сейчас:")
        await choose_course(message)
        return

    course_name = user_data.get('selected_course_name') or _course_name(course)
    target_date = datetime.now().date() + timedelta(days=day_shift)
    day_payload = build_day_schedule_payload_by_date(course_name, group_code, target_date)

    if day_payload is None:
        await message.answer(
            f"📭 На {target_date.strftime('%d.%m.%y')} для группы {group_code} нет занятий или дата еще не опубликована."
        )
        return

    await _update_user_selection(
        state,
        _telegram_id_from_message(message),
        selected_course_name=course_name,
        selected_course=day_payload['selected_course'],
        selected_group=group_code,
        selected_week_index=day_payload['selected_week_index'],
        selected_day_index=day_payload['selected_day_index'],
    )
    await message.answer(day_payload['text'], reply_markup=day_payload['keyboard'], parse_mode='HTML')


async def schedule_today(message: types.Message, state: FSMContext):
    """Показывает расписание на сегодня."""
    await _show_relative_day_schedule(message, state, 0)


async def schedule_tomorrow(message: types.Message, state: FSMContext):
    """Показывает расписание на завтра."""
    await _show_relative_day_schedule(message, state, 1)

async def _render_week_picker(callback_query: types.CallbackQuery, state: FSMContext):
    """Показывает список недель."""
    user_data = await _get_user_data_with_db_fallback(state, _telegram_id_from_callback(callback_query))
    course = user_data.get('selected_course')
    group_code = user_data.get('selected_group')

    if not course or not group_code:
        await callback_query.answer('⚠️ Сначала выберите курс, группу и месяц', show_alert=True)
        return

    weeks = _get_available_weeks(course, group_code)
    if not weeks:
        await callback_query.answer('📭 Для выбранной группы нет расписания', show_alert=True)
        return

    current_week_index = user_data.get('selected_week_index', 0) % len(weeks)
    keyboard = _build_week_picker_keyboard(course, group_code, weeks, current_week_index)

    lines = [
        '<b>🗂️ Выбор недели</b>',
        f"🎓 Курс: <b>{html.escape(_course_name(course))}</b>",
        f"👥 Группа: <b>{html.escape(group_code)}</b>",
        '',
    ]


    await _safe_edit_text(
        callback_query.message,
        '\n'.join(lines),
        reply_markup=keyboard,
        parse_mode='HTML',
    )
    await callback_query.answer()

async def process_group_choice(callback_query: types.CallbackQuery, state: FSMContext):
    """Обрабатывает выбор группы."""
    payload = callback_query.data[len('group_'):]
    if '_' not in payload:
        await callback_query.answer('⚠️ Некорректные данные группы', show_alert=True)
        return

    course_name, group_code = payload.rsplit('_', 1)
    selected_course, selected_week_index = _resolve_group_selection(course_name, group_code)
    if not selected_course:
        await callback_query.answer('📭 Для выбранной группы нет расписания', show_alert=True)
        return

    await _update_user_selection(
        state,
        _telegram_id_from_callback(callback_query),
        selected_course_name=course_name,
        selected_course=selected_course,
        selected_group=group_code,
        selected_week_index=selected_week_index,
        selected_day_index=None,
    )
    await _render_week_view(callback_query, state, selected_week_index)

async def show_schedule_day(callback_query: types.CallbackQuery, state: FSMContext):
    """Точка входа в расписание."""
    callback_data = callback_query.data

    course = None
    group_code = None
    target_week_label = None
    target_day_index = None
    open_day_view = False
    use_auto_week = False

    if callback_data.startswith('show_schedule_'):
        payload = callback_data[len('show_schedule_'):]
        if '_' in payload:
            course, group_code = payload.rsplit('_', 1)
            use_auto_week = True
    elif callback_data.startswith('show_day_'):
        payload = callback_data[len('show_day_'):]
        parts = payload.rsplit('_', 2)
        if len(parts) == 3 and parts[1].isdigit():
            course_group, day_token, week_token = parts
            if '_' in course_group:
                course, group_code = course_group.rsplit('_', 1)
                target_day_index = int(day_token)
                target_week_label = week_token
                open_day_view = True
        elif len(parts) == 2 and parts[1].isdigit():
            course_group, day_token = parts
            if '_' in course_group:
                course, group_code = course_group.rsplit('_', 1)
                target_day_index = int(day_token)
                open_day_view = True
    elif callback_data.startswith('show_week_'):
        payload = callback_data[len('show_week_'):]
        if '_' in payload:
            course_group, target_week_label = payload.rsplit('_', 1)
            if '_' in course_group:
                course, group_code = course_group.rsplit('_', 1)

    if not course or not group_code:
        await callback_query.answer('⚠️ Сначала выберите курс, группу и месяц', show_alert=True)
        return

    weeks = _get_available_weeks(course, group_code)
    if not weeks:
        await callback_query.answer('📭 Для выбранной группы нет расписания', show_alert=True)
        return

    if target_week_label and target_week_label in weeks:
        week_index = weeks.index(target_week_label)
    elif use_auto_week:
        week_index = _pick_initial_week_index(course, group_code, weeks)
    else:
        user_data = await _get_user_data_with_db_fallback(state, _telegram_id_from_callback(callback_query))
        week_index = user_data.get('selected_week_index', 0) % len(weeks)

    await _update_user_selection(
        state,
        _telegram_id_from_callback(callback_query),
        selected_course=course,
        selected_course_name=_course_name(course),
        selected_group=group_code,
        selected_week_index=week_index,
    )

    if open_day_view:
        await _render_day_view(callback_query, state, target_day_index)
    else:
        await _render_week_view(callback_query, state, week_index)


async def calendar_shift_week(callback_query: types.CallbackQuery, state: FSMContext):
    """Переключает неделю вперед или назад, включая переход между месяцами."""
    try:
        delta = int(callback_query.data.split('_', 2)[2])
    except (ValueError, IndexError):
        await callback_query.answer('⚠️ Некорректный шаг недели', show_alert=True)
        return

    user_data = await _get_user_data_with_db_fallback(state, _telegram_id_from_callback(callback_query))
    course = user_data.get('selected_course')
    group_code = user_data.get('selected_group')
    course_name = user_data.get('selected_course_name')

    if not course or not group_code:
        await callback_query.answer('⚠️ Сначала выберите курс, группу и месяц', show_alert=True)
        return

    if not course_name:
        course_name = _course_name(course)

    weeks = _get_available_weeks(course, group_code)
    if not weeks:
        await callback_query.answer('📭 Для выбранной группы нет расписания', show_alert=True)
        return

    current_week_index = user_data.get('selected_week_index', 0) % len(weeks)
    current_week_label = weeks[current_week_index]

    timeline = _build_group_week_timeline(course_name, group_code)
    if not timeline:
        await callback_query.answer('📭 Для выбранной группы нет расписания', show_alert=True)
        return

    current_timeline_index = next(
        (
            idx
            for idx, item in enumerate(timeline)
            if item['course'] == course and item['week_label'] == current_week_label
        ),
        None,
    )

    if current_timeline_index is None:
        current_timeline_index = 0

    target_timeline_index = current_timeline_index + delta

    if target_timeline_index < 0:
        await callback_query.answer('📭 Предыдущих недель больше нет.', show_alert=True)
        return

    if target_timeline_index >= len(timeline):
        await callback_query.answer(
            '📭 Следующих недель нет. Как они появятся, мы вам обязательно сообщим.',
            show_alert=True,
        )
        return

    target = timeline[target_timeline_index]

    await _update_user_selection(
        state,
        _telegram_id_from_callback(callback_query),
        selected_course_name=course_name,
        selected_course=target['course'],
        selected_group=group_code,
        selected_week_index=target['week_index'],
        selected_day_index=None,
    )

    await _render_week_view(callback_query, state, target['week_index'])

async def calendar_open_day(callback_query: types.CallbackQuery, state: FSMContext):
    """Открывает расписание дня со сквозной навигацией между неделями и месяцами."""
    try:
        day_index = int(callback_query.data.split('_', 2)[2])
    except (ValueError, IndexError):
        await callback_query.answer('⚠️ Некорректный день', show_alert=True)
        return

    user_data = await _get_user_data_with_db_fallback(state, _telegram_id_from_callback(callback_query))
    course = user_data.get('selected_course')
    group_code = user_data.get('selected_group')

    if not course or not group_code:
        await callback_query.answer('⚠️ Сначала выберите курс, группу и месяц', show_alert=True)
        return

    weeks = _get_available_weeks(course, group_code)
    if not weeks:
        await callback_query.answer('📭 Для выбранной группы нет расписания', show_alert=True)
        return

    current_week_index = user_data.get('selected_week_index', 0) % len(weeks)
    current_week_label = weeks[current_week_index]
    current_week_days = _build_week_day_items(course, group_code, current_week_label)

    if not current_week_days:
        await callback_query.answer('⚠️ Неделя не содержит дней', show_alert=True)
        return

    if 0 <= day_index < len(current_week_days):
        await _render_day_view(callback_query, state, day_index)
        return

    direction = -1 if day_index < 0 else 1
    course_name = user_data.get('selected_course_name') or _course_name(course)
    timeline = _build_group_week_timeline(course_name, group_code)
    if not timeline:
        await callback_query.answer('📭 Для выбранной группы нет расписания', show_alert=True)
        return

    current_timeline_index = next(
        (
            idx
            for idx, item in enumerate(timeline)
            if item['course'] == course and item['week_label'] == current_week_label
        ),
        None,
    )

    if current_timeline_index is None:
        current_timeline_index = 0

    target_timeline_index = current_timeline_index + direction
    while 0 <= target_timeline_index < len(timeline):
        target = timeline[target_timeline_index]
        target_week_days = _build_week_day_items(target['course'], group_code, target['week_label'])
        if target_week_days:
            target_day_index = len(target_week_days) - 1 if direction < 0 else 0
            await _update_user_selection(
                state,
                _telegram_id_from_callback(callback_query),
                selected_course_name=course_name,
                selected_course=target['course'],
                selected_group=group_code,
                selected_week_index=target['week_index'],
                selected_day_index=target_day_index,
            )
            await _render_day_view(callback_query, state, target_day_index)
            return
        target_timeline_index += direction

    if direction < 0:
        await callback_query.answer('📭 Предыдущих дней больше нет.', show_alert=True)
        return

    await callback_query.answer(
        '📭 Следующих дней нет. Как они появятся, мы вам обязательно сообщим.',
        show_alert=True,
    )



async def calendar_shift_month_in_week_picker(callback_query: types.CallbackQuery, state: FSMContext):
    """Листает месяцы на экране выбора недели."""
    try:
        delta = int(callback_query.data.split('_', 3)[3])
    except (ValueError, IndexError):
        await callback_query.answer('⚠️ Некорректное направление', show_alert=True)
        return

    user_data = await _get_user_data_with_db_fallback(state, _telegram_id_from_callback(callback_query))
    selected_course = user_data.get('selected_course')
    group_code = user_data.get('selected_group')
    course_name = user_data.get('selected_course_name')

    if not selected_course or not group_code:
        await callback_query.answer('⚠️ Сначала выберите курс, группу и месяц', show_alert=True)
        return

    if not course_name:
        course_name = _course_name(selected_course)

    period_to_course = _build_period_to_course(course_name, group_code)
    if not period_to_course:
        await callback_query.answer('📭 Для выбранной группы нет расписания', show_alert=True)
        return

    course_keys = [course_key for _, course_key in period_to_course]
    if selected_course in course_keys:
        current_index = course_keys.index(selected_course)
    else:
        current_index = 0

    target_index = current_index + delta
    if target_index < 0 or target_index >= len(course_keys):
        await callback_query.answer('📭 Больше месяцев нет', show_alert=True)
        return

    target_course = course_keys[target_index]
    weeks = _get_available_weeks(target_course, group_code)
    week_index = _pick_initial_week_index(target_course, group_code, weeks) if weeks else 0

    await _update_user_selection(
        state,
        _telegram_id_from_callback(callback_query),
        selected_course_name=course_name,
        selected_course=target_course,
        selected_group=group_code,
        selected_week_index=week_index,
        selected_day_index=None,
    )

    await _render_week_picker(callback_query, state)



async def calendar_open_current_week(callback_query: types.CallbackQuery, state: FSMContext):
    """Открывает неделю по сегодняшней дате для текущей группы."""
    user_data = await _get_user_data_with_db_fallback(state, _telegram_id_from_callback(callback_query))
    course = user_data.get('selected_course')
    group_code = user_data.get('selected_group')
    course_name = user_data.get('selected_course_name')

    if not course or not group_code:
        await callback_query.answer('⚠️ Сначала выберите курс, группу и месяц', show_alert=True)
        return

    if not course_name:
        course_name = _course_name(course)

    target_week = _pick_today_week_for_group(course_name, group_code)
    if target_week is None:
        await callback_query.answer('📭 Для этой группы нет доступных недель', show_alert=True)
        return

    target_course, week_index = target_week

    await _update_user_selection(
        state,
        _telegram_id_from_callback(callback_query),
        selected_course_name=course_name,
        selected_course=target_course,
        selected_group=group_code,
        selected_week_index=week_index,
        selected_day_index=None,
    )
    await _render_week_view(callback_query, state, week_index)

async def calendar_pick_week(callback_query: types.CallbackQuery, state: FSMContext):
    """Открывает выбор недели."""
    await _render_week_picker(callback_query, state)


async def calendar_choose_week(callback_query: types.CallbackQuery, state: FSMContext):
    """Выбирает неделю из списка."""
    try:
        week_index = int(callback_query.data.split('_', 2)[2])
    except (ValueError, IndexError):
        await callback_query.answer('⚠️ Некорректный индекс недели', show_alert=True)
        return

    await _update_user_selection(
        state,
        _telegram_id_from_callback(callback_query),
        selected_week_index=week_index,
    )
    await _render_week_view(callback_query, state, week_index)


async def calendar_back_week(callback_query: types.CallbackQuery, state: FSMContext):
    """Возвращает к неделе."""
    user_data = await _get_user_data_with_db_fallback(state, _telegram_id_from_callback(callback_query))
    week_index = user_data.get('selected_week_index', 0)
    await _render_week_view(callback_query, state, week_index)


async def calendar_pick_month(callback_query: types.CallbackQuery, state: FSMContext):
    """Возвращает к выбору месяца."""
    user_data = await _get_user_data_with_db_fallback(state, _telegram_id_from_callback(callback_query))
    course_name = user_data.get('selected_course_name')
    selected_course = user_data.get('selected_course')
    group_code = user_data.get('selected_group')

    if not course_name and selected_course:
        course_name = _course_name(selected_course)

    if not course_name or not group_code:
        await callback_query.answer('⚠️ Сначала выберите курс и группу', show_alert=True)
        return

    rendered = await _render_period_picker(callback_query, state, course_name, group_code)
    if not rendered:
        await callback_query.answer('📭 Для выбранной группы нет расписания', show_alert=True)
        return

    await callback_query.answer()


async def calendar_noop(callback_query: types.CallbackQuery):
    """Служебная кнопка."""
    await callback_query.answer()


async def choose_day(callback_query: types.CallbackQuery, state: FSMContext):
    """Обрабатывает старый choose_day callback."""
    payload = callback_query.data[len('choose_day_'):]
    parts = payload.rsplit('_', 2)
    if len(parts) < 2:
        await callback_query.answer('⚠️ Некорректные данные выбора дня', show_alert=True)
        return

    if len(parts) == 3:
        course_group, _, target_week_label = parts
    else:
        course_group, _ = parts
        target_week_label = None

    if '_' not in course_group:
        await callback_query.answer('⚠️ Некорректные данные курса и группы', show_alert=True)
        return

    course, group_code = course_group.rsplit('_', 1)

    weeks = _get_available_weeks(course, group_code)
    if not weeks:
        await callback_query.answer('📭 Для выбранной группы нет расписания', show_alert=True)
        return

    week_index = 0
    if target_week_label and target_week_label in weeks:
        week_index = weeks.index(target_week_label)

    await _update_user_selection(
        state,
        _telegram_id_from_callback(callback_query),
        selected_course=course,
        selected_course_name=_course_name(course),
        selected_group=group_code,
        selected_week_index=week_index,
    )

    await _render_week_view(callback_query, state, week_index)


async def show_week(callback_query: types.CallbackQuery, state: FSMContext):
    """Обрабатывает старый show_week callback."""
    await show_schedule_day(callback_query, state)


async def refresh_schedule(callback_query: types.CallbackQuery, state: FSMContext):
    """Перезагружает расписание."""
    payload = callback_query.data[len('refresh_'):]
    course = None
    group_code = None

    if '_' in payload:
        course, group_code = payload.rsplit('_', 1)

    user_data = await _get_user_data_with_db_fallback(state, _telegram_id_from_callback(callback_query))
    course = course or user_data.get('selected_course')
    group_code = group_code or user_data.get('selected_group')

    await _safe_edit_text(callback_query.message, '🔄 Обновляю расписание...', parse_mode='HTML')

    init_schedule()
    try:
        db_stats = await persist_schedule_to_db()
        print(
            "Расписание сохранено в БД: "
            "периодов={periods}, курсов={courses}, групп={groups}, недель={weeks}, дней={days}, занятий={lessons}".format(**db_stats)
        )
    except Exception as db_error:
        print(f"ERROR: cannot save schedule to DB: {db_error}")


    if course and group_code and course in schedule_data and group_code in schedule_data.get(course, {}):
        await _update_user_selection(
            state,
            _telegram_id_from_callback(callback_query),
            selected_course=course,
            selected_group=group_code,
            selected_course_name=_course_name(course),
        )
        callback_query.data = f"show_schedule_{course}_{group_code}"
        await show_schedule_day(callback_query, state)
        await callback_query.answer('✅ Расписание обновлено')
        return

    await callback_query.answer('✅ Расписание обновлено, выберите месяц заново', show_alert=True)


async def back_to_courses(callback_query: types.CallbackQuery):
    """Возвращает пользователя к выбору курса."""
    await choose_course(callback_query.message)
    await callback_query.answer()

# Колбек для возврата в главное меню
async def main_menu(callback_query: types.CallbackQuery):
    """Возвращает пользователя в главное меню."""
    await callback_query.message.answer(
        "🏠 Главное меню",
        reply_markup=get_main_keyboard()
    )
    await callback_query.answer()

# Хендлер для команды "Мое расписание"
async def my_schedule(message: types.Message, state: FSMContext):
    """Показывает текущее выбранное расписание пользователя."""
    user_data = await _get_user_data_with_db_fallback(state, _telegram_id_from_message(message))

    course = user_data.get('selected_course')
    group_code = user_data.get('selected_group')

    if course and group_code:
        keyboard = types.InlineKeyboardMarkup(row_width=1)
        keyboard.add(
            types.InlineKeyboardButton(
                text="📅 Показать расписание",
                callback_data=f"show_schedule_{course}_{group_code}"
            )
        )

        group_info = get_group_info(course, group_code)
        group_display = f"{group_code}"
        if len(group_info) > 2 and group_info[2]:
            group_display += f"\n📚 Направление: {group_info[2]}"

        await message.answer(
            f"<b>📋 Ваше текущее расписание:</b>\n"
            f"<b>🎓 Курс:</b> {_course_display(course)}\n"
            f"<b>👥 Группа:</b> {group_display}\n\n"
            "👇 Нажмите кнопку ниже, чтобы открыть расписание.",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    else:
        await message.answer(
            "ℹ️ Вы еще не выбрали группу. Давайте выберем её сейчас:"
        )
        await choose_course(message)

async def change_group(callback_query: types.CallbackQuery):
    """Запускает повторный выбор группы."""
    await choose_course(callback_query.message)
    await callback_query.answer()


async def settings_open(callback_query: types.CallbackQuery, state: FSMContext):
    """Открывает или обновляет экран настроек."""
    await _render_settings_view(
        callback_query.message,
        state,
        _telegram_id_from_callback(callback_query),
        edit=True,
    )
    await callback_query.answer()


async def settings_toggle_digest(callback_query: types.CallbackQuery, state: FSMContext):
    """Переключает ежедневную вечернюю рассылку."""
    telegram_id = _telegram_id_from_callback(callback_query)
    user_data = await _get_user_data_with_db_fallback(state, telegram_id)
    digest_enabled, _, _ = _digest_settings_from_user_data(user_data)

    await _update_user_selection(
        state,
        telegram_id,
        daily_digest_enabled=not digest_enabled,
    )
    await _render_settings_view(callback_query.message, state, telegram_id, edit=True)
    await callback_query.answer('Настройки рассылки обновлены')


async def settings_shift_digest_time(callback_query: types.CallbackQuery, state: FSMContext):
    """Сдвигает время ежедневной рассылки на час."""
    try:
        delta = int(callback_query.data.rsplit('_', 1)[1])
    except (ValueError, IndexError):
        await callback_query.answer('⚠️ Некорректное время', show_alert=True)
        return

    telegram_id = _telegram_id_from_callback(callback_query)
    user_data = await _get_user_data_with_db_fallback(state, telegram_id)
    _, digest_hour, digest_minute = _digest_settings_from_user_data(user_data)
    digest_hour = (digest_hour + delta) % 24

    await _update_user_selection(
        state,
        telegram_id,
        daily_digest_hour=digest_hour,
        daily_digest_minute=digest_minute,
    )
    await _render_settings_view(callback_query.message, state, telegram_id, edit=True)
    await callback_query.answer(f'Новое время: {digest_hour:02d}:{digest_minute:02d} МСК')


async def settings_toggle_event_notifications(callback_query: types.CallbackQuery, state: FSMContext):
    """Переключает уведомления пользователя о событиях."""
    telegram_id = _telegram_id_from_callback(callback_query)
    user_data = await _get_user_data_with_db_fallback(state, telegram_id)
    current_value = _event_notifications_enabled_from_user_data(user_data)

    await _update_user_selection(
        state,
        telegram_id,
        event_notifications_enabled=not current_value,
    )
    await _render_settings_view(callback_query.message, state, telegram_id, edit=True)
    await callback_query.answer('Настройки событий обновлены')


async def settings_time_noop(callback_query: types.CallbackQuery):
    """Служебная кнопка времени на экране настроек."""
    await callback_query.answer()


# Регистрация хендлеров
def register_handlers_client(dp: Dispatcher):
    """Регистрирует обработчики сообщений и callback-кнопок."""
    dp.register_message_handler(cmd_start, commands=['start'])
    dp.register_message_handler(cmd_help, commands=['help'], state='*')
    dp.register_message_handler(admin_command, commands=['admin'], state='*')
    dp.register_message_handler(starosta_command, commands=['starosta'], state='*')
    dp.register_message_handler(search_entrypoint, commands=['search'], state='*')
    dp.register_message_handler(group_command, commands=['group'], state='*')
    dp.register_message_handler(date_command, commands=['date'], state='*')
    dp.register_message_handler(search_teacher_command, commands=['teacher'], state='*')
    dp.register_message_handler(search_room_command, commands=['room'], state='*')
    dp.register_message_handler(search_subject_command, commands=['subject'], state='*')
    dp.register_message_handler(schedule_today, commands=['today'])
    dp.register_message_handler(schedule_tomorrow, commands=['tomorrow'])
    dp.register_message_handler(search_entrypoint, text='🔎 Поиск', state='*')
    dp.register_message_handler(user_events, text='🎉 События', state='*')
    dp.register_message_handler(settings, text='💼 Настройки')
    dp.register_message_handler(my_schedule, text='📅 Мое расписание')
    dp.register_message_handler(schedule_today, lambda message: message.text and message.text.casefold() == 'на сегодня')
    dp.register_message_handler(schedule_tomorrow, lambda message: message.text and message.text.casefold() == 'на завтра')
    dp.register_message_handler(admin_receive_login, state=AdminAuthDialog.waiting_login)
    dp.register_message_handler(admin_receive_password, state=AdminAuthDialog.waiting_password)
    dp.register_message_handler(admin_receive_message_text, state=AdminMessageDialog.waiting_text)
    dp.register_message_handler(starosta_receive_login, state=StarostaAuthDialog.waiting_login)
    dp.register_message_handler(starosta_receive_password, state=StarostaAuthDialog.waiting_password)
    dp.register_message_handler(starosta_receive_message_text, state=StarostaMessageDialog.waiting_text)
    dp.register_message_handler(admin_event_receive_datetime, state=AdminEventDialog.waiting_datetime)
    dp.register_message_handler(admin_event_receive_text, state=AdminEventDialog.waiting_text)
    dp.register_message_handler(
        admin_event_receive_attachment,
        state=AdminEventDialog.waiting_attachment,
        content_types=types.ContentTypes.ANY,
    )
    dp.register_message_handler(search_receive_query, state=SearchDialog.waiting_query)
    
    # Регистрация колбеков
    dp.register_callback_query_handler(search_start_callback, Text(equals='search_start'), state='*')
    dp.register_callback_query_handler(search_choose_kind, Text(startswith='search_kind_'), state='*')
    dp.register_callback_query_handler(search_open_entity, Text(startswith='search_entity_'), state='*')
    dp.register_callback_query_handler(search_change_page, Text(startswith='search_page_'), state='*')
    dp.register_callback_query_handler(search_open_week, Text(startswith='search_week_'), state='*')
    dp.register_callback_query_handler(search_shift_week, Text(startswith='search_shift_'), state='*')
    dp.register_callback_query_handler(search_open_day, Text(startswith='search_day_'), state='*')
    dp.register_callback_query_handler(search_open_nearest_days, Text(startswith='search_nearest_'), state='*')
    dp.register_callback_query_handler(search_toggle_empty_week, Text(startswith='search_toggleweek_'), state='*')
    dp.register_callback_query_handler(search_toggle_empty_day, Text(startswith='search_toggleday_'), state='*')
    dp.register_callback_query_handler(search_pick_week, Text(startswith='search_weekpick_'), state='*')
    dp.register_callback_query_handler(search_choose_week, Text(startswith='search_weeksel_'), state='*')
    dp.register_callback_query_handler(search_pick_month, Text(startswith='search_monthpick_'), state='*')
    dp.register_callback_query_handler(search_view_month, Text(startswith='search_monthview_'), state='*')
    dp.register_callback_query_handler(search_choose_month, Text(startswith='search_monthsel_'), state='*')
    dp.register_callback_query_handler(search_noop, Text(equals='search_noop'), state='*')
    dp.register_callback_query_handler(process_course_choice, Text(startswith="course_"))
    dp.register_callback_query_handler(process_group_choice, Text(startswith="group_"))
    dp.register_callback_query_handler(show_schedule_day, Text(startswith="show_schedule_"))
    dp.register_callback_query_handler(show_schedule_day, Text(startswith="show_day_"))
    dp.register_callback_query_handler(show_week, Text(startswith="show_week_"))
    dp.register_callback_query_handler(choose_day, Text(startswith="choose_day_"))

    dp.register_callback_query_handler(calendar_shift_week, Text(startswith="cal_shift_"))
    dp.register_callback_query_handler(calendar_open_day, Text(startswith="cal_day_"))

    dp.register_callback_query_handler(calendar_pick_week, Text(equals="cal_pick_week"))
    dp.register_callback_query_handler(calendar_open_current_week, Text(equals="cal_current_week"))
    dp.register_callback_query_handler(calendar_shift_month_in_week_picker, Text(startswith="cal_month_shift_"))

    dp.register_callback_query_handler(calendar_choose_week, Text(startswith="cal_weekpick_"))
    dp.register_callback_query_handler(calendar_back_week, Text(equals="cal_back_week"))
    dp.register_callback_query_handler(calendar_pick_month, Text(equals="cal_pick_month"))
    dp.register_callback_query_handler(calendar_noop, Text(equals="cal_noop"))

    dp.register_callback_query_handler(refresh_schedule, Text(startswith="refresh_"))
    dp.register_callback_query_handler(back_to_courses, Text(startswith="back_to_courses"))
    dp.register_callback_query_handler(main_menu, Text(startswith="main_menu"))
    dp.register_callback_query_handler(settings_open, Text(equals="settings_open"))
    dp.register_callback_query_handler(settings_toggle_digest, Text(equals="settings_digest_toggle"))
    dp.register_callback_query_handler(settings_shift_digest_time, Text(startswith="settings_digest_time_"))
    dp.register_callback_query_handler(settings_toggle_event_notifications, Text(equals='settings_events_toggle'))
    dp.register_callback_query_handler(settings_time_noop, Text(equals="settings_time_noop"))
    dp.register_callback_query_handler(admin_open, Text(equals='admin_open'), state='*')
    dp.register_callback_query_handler(admin_refresh, Text(equals='admin_refresh'), state='*')
    dp.register_callback_query_handler(admin_logout, Text(equals='admin_logout'), state='*')
    dp.register_callback_query_handler(starosta_open, Text(equals='starosta_open'), state='*')
    dp.register_callback_query_handler(starosta_logout, Text(equals='starosta_logout'), state='*')
    dp.register_callback_query_handler(starosta_pick_group, Text(startswith='starosta_group_pick_'), state='*')
    dp.register_callback_query_handler(starosta_set_group, Text(startswith='starosta_group_set_'), state='*')
    dp.register_callback_query_handler(starosta_users_open, Text(equals='starosta_users'), state='*')
    dp.register_callback_query_handler(starosta_users_change_page, Text(startswith='starosta_users_page_'), state='*')
    dp.register_callback_query_handler(starosta_users_back_to_list, Text(equals='starosta_users_back'), state='*')
    dp.register_callback_query_handler(starosta_users_show_details, Text(startswith='starosta_users_info_'), state='*')
    dp.register_callback_query_handler(starosta_start_single_message, Text(startswith='starosta_users_message_'), state='*')
    dp.register_callback_query_handler(starosta_start_group_message, Text(equals='starosta_group_message'), state='*')
    dp.register_callback_query_handler(starosta_start_group_message, Text(equals='starosta_users_broadcast'), state='*')
    dp.register_callback_query_handler(starosta_message_cancel, Text(equals='starosta_message_cancel'), state='*')
    dp.register_callback_query_handler(starosta_noop, Text(equals='starosta_noop'), state='*')
    dp.register_callback_query_handler(admin_event_create_start, Text(equals='admin_event_create'), state='*')
    dp.register_callback_query_handler(admin_event_cancel, Text(equals='admin_event_cancel'), state='*')
    dp.register_callback_query_handler(admin_event_skip_attachment, Text(equals='admin_event_skip'), state='*')
    dp.register_callback_query_handler(admin_users_open, Text(equals='admin_users'), state='*')
    dp.register_callback_query_handler(admin_users_change_page, Text(startswith='admin_users_page_'), state='*')
    dp.register_callback_query_handler(admin_users_pick_course, Text(startswith='admin_users_coursepick_'), state='*')
    dp.register_callback_query_handler(admin_users_pick_group, Text(startswith='admin_users_grouppick_'), state='*')
    dp.register_callback_query_handler(admin_users_set_course, Text(startswith='admin_users_course_set_'), state='*')
    dp.register_callback_query_handler(admin_users_set_group, Text(startswith='admin_users_group_set_'), state='*')
    dp.register_callback_query_handler(admin_users_back_to_list, Text(equals='admin_users_back'), state='*')
    dp.register_callback_query_handler(admin_users_start_filtered_message, Text(equals='admin_users_broadcast'), state='*')
    dp.register_callback_query_handler(admin_users_start_single_message, Text(startswith='admin_users_message_'), state='*')
    dp.register_callback_query_handler(admin_message_cancel, Text(equals='admin_message_cancel'), state='*')
    dp.register_callback_query_handler(admin_users_toggle_starosta_assignment, Text(startswith='admin_users_starosta_toggle_'), state='*')
    dp.register_callback_query_handler(admin_users_show_details, Text(startswith='admin_users_info_'), state='*')
    dp.register_callback_query_handler(admin_users_toggle_starosta_filter, Text(equals='admin_users_starosta_toggle'), state='*')
    dp.register_callback_query_handler(admin_users_clear_course, Text(equals='admin_users_course_clear'), state='*')
    dp.register_callback_query_handler(admin_users_clear_group, Text(equals='admin_users_group_clear'), state='*')
    dp.register_callback_query_handler(admin_users_reset_filters, Text(equals='admin_users_reset'), state='*')
    dp.register_callback_query_handler(admin_users_noop, Text(equals='admin_users_noop'), state='*')
    dp.register_callback_query_handler(user_events_change_page, Text(startswith='events_page_'), state='*')
    dp.register_callback_query_handler(user_events_back_to_list, Text(startswith='events_back_'), state='*')
    dp.register_callback_query_handler(user_events_open_detail, Text(startswith='events_detail_'), state='*')
    dp.register_callback_query_handler(user_events_jump, Text(equals='events_open_all'), state='*')
    dp.register_callback_query_handler(user_events_open_past, Text(equals='events_open_past'), state='*')
    dp.register_callback_query_handler(user_events_open_upcoming, Text(equals='events_open_upcoming'), state='*')
    dp.register_callback_query_handler(user_events_jump, Text(startswith='events_jump_'), state='*')
    dp.register_callback_query_handler(user_events_shift_detail, Text(startswith='events_prev_'), state='*')
    dp.register_callback_query_handler(user_events_shift_detail, Text(startswith='events_next_'), state='*')
    dp.register_callback_query_handler(user_events_open_attachment, Text(startswith='events_media_'), state='*')
    dp.register_callback_query_handler(user_events_noop, Text(equals='events_noop'), state='*')
    dp.register_callback_query_handler(change_group, Text(startswith="change_group"))

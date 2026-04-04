import os
import re
import time
from urllib.parse import unquote, urljoin, urlparse

PAGE_URL = "https://www.muiv.ru/studentu/fakultet-it/raspisaniya/"
BASE_URL = "https://www.muiv.ru"

DOWNLOAD_DIR = "schedules"
POLL_SECONDS = 3600

TEXT_PREFIX = "Расписание ФИТ очная"
EXCEL_RE = re.compile(r"\.(xls|xlsx)$", re.IGNORECASE)

os.makedirs(DOWNLOAD_DIR, exist_ok=True)


def filename_from_url(url: str) -> str:
    """Извлекает имя файла из URL и очищает запрещенные символы."""
    path = urlparse(url).path
    name = unquote(os.path.basename(path))
    name = re.sub(r'[<>:"/\\|?*\n\r\t]', '_', name).strip()
    return name or 'file.xls'


def _download_or_update_file(page, file_url: str, download_dir: str):
    """Каждый раз скачивает Excel в память и сравнивает с локальным файлом; при отличиях перезаписывает файл."""
    file_name = filename_from_url(file_url)
    out_path = os.path.join(download_dir, file_name)

    response = page.context.request.get(file_url)
    if not response.ok:
        raise RuntimeError(f'HTTP {response.status} for {file_url}')

    data = response.body()

    if os.path.exists(out_path):
        with open(out_path, 'rb') as existing_file:
            old_data = existing_file.read()

        if old_data == data:
            return 'unchanged', out_path

        with open(out_path, 'wb') as output_file:
            output_file.write(data)
        return 'updated', out_path

    with open(out_path, 'wb') as output_file:
        output_file.write(data)
    return 'new', out_path


def _extract_links_from_rendered_dom(page):
    """Возвращает список пар (text, href) для ссылок download__src."""
    try:
        page.wait_for_selector('a.download__src', timeout=15000)
    except Exception:
        pass

    elements = page.query_selector_all('a.download__src')
    pairs = []
    for element in elements:
        href = element.get_attribute('href') or ''
        text = (element.inner_text() or '').strip().replace('\n', ' ')
        pairs.append((text, href))
    return pairs


def _open_schedule_page(page, page_url: str):
    """Открывает страницу расписания с резервными сценариями для медленного сайта."""
    attempts = [
        ('domcontentloaded', 45000),
        ('load', 45000),
    ]
    errors = []

    for wait_until, timeout_ms in attempts:
        try:
            page.goto(page_url, wait_until=wait_until, timeout=timeout_ms)
            return
        except Exception as open_error:
            # Иногда переход завершается по таймауту, хотя DOM уже частично доступен.
            try:
                if page.query_selector('a.download__src'):
                    return
            except Exception:
                pass
            errors.append(f'{wait_until}: {open_error}')

    raise RuntimeError(
        'Не удалось открыть страницу расписания: ' + ' | '.join(errors)
    )


def check_for_schedule_updates(
    page_url: str = PAGE_URL,
    base_url: str = BASE_URL,
    download_dir: str = DOWNLOAD_DIR,
    text_prefix: str = TEXT_PREFIX,
):
    """Один проход мониторинга: ищет ссылки и скачивает новые/обновленные Excel."""
    os.makedirs(download_dir, exist_ok=True)

    report = {
        'new_files': [],
        'updated_files': [],
        'unchanged_files': [],
        'matched_links': 0,
        'errors': [],
    }

    try:
        from playwright.sync_api import sync_playwright
    except Exception as import_error:
        report['errors'].append(f'Playwright import error: {import_error}')
        return report

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()

            _open_schedule_page(page, page_url)
            pairs = _extract_links_from_rendered_dom(page)

            for text, href in pairs:
                if not href or not text.startswith(text_prefix):
                    continue

                full_url = urljoin(base_url, href)
                if not EXCEL_RE.search(urlparse(full_url).path):
                    continue

                report['matched_links'] += 1

                try:
                    status, path = _download_or_update_file(page, full_url, download_dir)
                    if status == 'new':
                        report['new_files'].append(path)
                    elif status == 'updated':
                        report['updated_files'].append(path)
                    else:
                        report['unchanged_files'].append(path)
                except Exception as download_error:
                    report['errors'].append(f'{full_url}: {download_error}')

            browser.close()

    except Exception as monitor_error:
        report['errors'].append(str(monitor_error))

    return report


def run_forever(poll_seconds: int = POLL_SECONDS):
    """Запускает мониторинг в бесконечном цикле (для отдельного запуска скрипта)."""
    print(f'[START] Monitoring page: {PAGE_URL}')
    print(f'[START] Download dir: {os.path.abspath(DOWNLOAD_DIR)}')
    print('[START] Press Ctrl+C to stop.')

    while True:
        report = check_for_schedule_updates()
        print(
            '[CHECK] matched={matched} new={new} updated={updated} unchanged={unchanged} errors={errors}'.format(
                matched=report['matched_links'],
                new=len(report['new_files']),
                updated=len(report['updated_files']),
                unchanged=len(report['unchanged_files']),
                errors=len(report['errors']),
            )
        )
        if report['errors']:
            for error_text in report['errors']:
                print(f'  [ERR] {error_text}')

        time.sleep(poll_seconds)


if __name__ == '__main__':
    run_forever()

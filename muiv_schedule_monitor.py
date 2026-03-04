import os
import time
import re
from urllib.parse import urljoin, urlparse, unquote

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

PAGE_URL = "https://www.muiv.ru/studentu/fakultet-it/raspisaniya/"
BASE_URL = "https://www.muiv.ru"

DOWNLOAD_DIR = "schedules"
POLL_SECONDS = 3600

TEXT_PREFIX = "Расписание ФИТ очная"
EXCEL_RE = re.compile(r"\.(xls|xlsx)$", re.IGNORECASE)

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
})


def filename_from_url(url: str) -> str:
    path = urlparse(url).path
    name = unquote(os.path.basename(path))
    # подчищаем имя
    name = re.sub(r'[<>:"/\\|?*\n\r\t]', "_", name).strip()
    return name or "file.xls"
def download_file(page, file_url):
    print(f"  [DL] Скачиваю через браузер: {file_url}")

    fname = filename_from_url(file_url)
    out_path = os.path.join(DOWNLOAD_DIR, fname)

    if os.path.exists(out_path):
        print(f"  [SKIP] Уже есть: {fname}")
        return

    try:
        response = page.context.request.get(file_url)

        if not response.ok:
            print("  [ERR] HTTP ошибка:", response.status)
            return

        data = response.body()

        with open(out_path, "wb") as f:
            f.write(data)

        print(f"  [OK] Файл сохранён: {fname} ({len(data)} bytes)")

    except Exception as e:
        print("  [ERR] Ошибка скачивания:", e)


def extract_links_from_rendered_dom(page) -> list[tuple[str, str]]:
    """
    Возвращает список (text, href) для a.download__src
    """
    # Ждём, пока появятся нужные элементы (или хоть какие-то ссылки скачивания)
    try:
        page.wait_for_selector("a.download__src", timeout=15000)
    except PWTimeout:
        print("  [WARN] Не дождался a.download__src за 15s. Попробую собрать что есть в DOM...")

    elements = page.query_selector_all("a.download__src")
    print(f"  [INFO] Найдено a.download__src: {len(elements)}")

    results = []
    for el in elements:
        href = el.get_attribute("href") or ""
        # текст бывает в span, но проще взять innerText всей ссылки
        text = (el.inner_text() or "").strip().replace("\n", " ")
        results.append((text, href))
    return results


def main():
    print(f"[START] Страница: {PAGE_URL}")
    print(f"[START] Папка: {os.path.abspath(DOWNLOAD_DIR)}")
    print("[START] Ctrl+C чтобы остановить.\n")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        while True:
            print("\n===== Проверка сайта =====")
            try:
                page.goto(PAGE_URL, wait_until="networkidle", timeout=60000)
                print("  [OK] Страница открыта (networkidle)")

                pairs = extract_links_from_rendered_dom(page)

                if not pairs:
                    html_snippet = page.content()[:500].replace("\n", " ")
                    print("  [DEBUG] DOM snippet:", html_snippet)
                    time.sleep(POLL_SECONDS)
                    continue

                matched = 0
                for text, href in pairs:
                    if not href:
                        continue

                    if "Расписание" in text:
                        print(f"  [SEEN] {text} -> {href}")

                    if text.startswith(TEXT_PREFIX):
                        matched += 1
                        full_url = urljoin(BASE_URL, href)

                        if not EXCEL_RE.search(urlparse(full_url).path):
                            print(f"  [WARN] Подходит по тексту, но не похоже на Excel: {full_url}")
                            continue

                        print(f"  [MATCH] {text}")
                        print(f"  [LINK]  {full_url}")
                        download_file(page, full_url)

                print(f"  [INFO] Подходящих по '{TEXT_PREFIX}': {matched}")

            except Exception as e:
                print(f"  [ERR] Ошибка цикла: {e}")

            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
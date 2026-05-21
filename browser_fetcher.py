import os
import re
import time
import random
from pathlib import Path

from playwright.sync_api import sync_playwright
from playwright_stealth import stealth_sync

DEBUG_DIR = Path(".debug")
DEBUG_DIR.mkdir(exist_ok=True)

PROFILE_DIR = ".playwright-profile"

BLOCK_PATTERNS = [
    "Se ha detectado un uso indebido",
    "El acceso se ha bloqueado",
    "¿No consigues pasar de aquí?",
    "403 Forbidden",
]

def normalize_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "")
    return text.strip()

def is_blocked(text: str) -> bool:
    text = text.lower()

    return any(
        pattern.lower() in text
        for pattern in BLOCK_PATTERNS
    )

def fetch_with_browser(url: str, name: str) -> str:

    headless = os.getenv("HEADLESS", "true").lower() == "true"

    delay_min = int(os.getenv("RANDOM_DELAY_MIN_SECONDS", "5"))
    delay_max = int(os.getenv("RANDOM_DELAY_MAX_SECONDS", "15"))

    time.sleep(random.randint(delay_min, delay_max))

    with sync_playwright() as p:

        context = p.chromium.launch_persistent_context(
            PROFILE_DIR,
            headless=headless,
            viewport={"width": 1366, "height": 768},
            locale="es-ES",
            timezone_id="Europe/Madrid",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )

        page = context.new_page()

        stealth_sync(page)

        try:

            page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=60000,
            )

            page.wait_for_timeout(8000)

            # Scroll lento
            for _ in range(5):
                page.mouse.wheel(0, 1500)
                page.wait_for_timeout(1000)

            # Intentar aceptar cookies
            cookie_buttons = [
                "Aceptar",
                "Aceptar todo",
                "Consentir",
            ]

            for text in cookie_buttons:
                try:
                    locator = page.get_by_text(text)
                    locator.first.click(timeout=2000)
                    page.wait_for_timeout(1000)
                    break
                except:
                    pass

            title = page.title()

            body_text = page.locator("body").inner_text()

            body_text = normalize_text(body_text)

            screenshot_path = DEBUG_DIR / f"{name}.png"
            html_path = DEBUG_DIR / f"{name}.html"
            text_path = DEBUG_DIR / f"{name}.txt"

            page.screenshot(path=str(screenshot_path))

            html = page.content()

            html_path.write_text(
                html,
                encoding="utf-8",
            )

            text_path.write_text(
                body_text,
                encoding="utf-8",
            )

            print(f"Título: {title}")
            print(f"Texto extraído: {len(body_text)} caracteres")

            if is_blocked(body_text):
                raise RuntimeError(
                    "Idealista ha bloqueado el acceso"
                )

            return body_text

        finally:
            context.close()
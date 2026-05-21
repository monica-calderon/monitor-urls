from __future__ import annotations

from dotenv import load_dotenv
load_dotenv()

import os

import difflib
import base64
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterable
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

STATE_DIR = Path(os.getenv("STATE_DIR", ".monitor_state"))
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
TELEGRAM_ALLOWED_USER_ID = os.getenv("TELEGRAM_ALLOWED_USER_ID", "").strip()
GH_SECRETS_PAT = os.getenv("GH_SECRETS_PAT", "").strip()
GITHUB_REPOSITORY = os.getenv("GITHUB_REPOSITORY", "monica-calderon/monitor-urls").strip()
TELEGRAM_UPDATES_JSON = os.getenv("TELEGRAM_UPDATES_JSON", "").strip()
MONITOR_STATE_JSON = os.getenv("MONITOR_STATE_JSON", "").strip()
DRY_RUN = os.getenv("DRY_RUN", "").strip().lower() in {"1", "true", "yes", "si"}
ACTION_TO_RUN = os.getenv("ACTION_TO_RUN", "normal").strip().lower()
MAX_TEXT_FILE_BYTES = 10 * 1024 * 1024
MADRID_TIMEZONE = ZoneInfo("Europe/Madrid")
TELEGRAM_OFFSET_PATH = STATE_DIR / "telegram_update_offset.txt"
URL_PATTERN = re.compile(r"https?://[^\s<>()\"']+", re.IGNORECASE)
URLS_SECRET_NAME = "MONITOR_URLS_JSON"
STATE_SECRET_NAME = "MONITOR_STATE_JSON"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

PLAYWRIGHT_PROFILE_DIR = Path(os.getenv("PLAYWRIGHT_PROFILE_DIR", ".playwright-profile"))
HEADLESS = os.getenv("HEADLESS", "true").strip().lower() in {"1", "true", "yes", "si"}
SLOW_MO_MS = int(os.getenv("SLOW_MO_MS", "0"))
PAGE_LOAD_TIMEOUT_MS = int(os.getenv("PAGE_LOAD_TIMEOUT_MS", "60000"))


BLOCKED_MARKERS = [
    "captcha",
    "checking your browser",
    "cloudflare",
    "verificacion",
    "verificación",
    "verifica que eres",
    "device verification",
    "access denied",
    "forbidden",
    "demasiadas peticiones",
    "no eres un robot",
    "robot",
    "se ha detectado un uso indebido",
    "el acceso se ha bloqueado",
    "no consigues pasar de aquí",
    "no consigues pasar de aqui",
]

NOISE_TAGS = [
    "script",
    "style",
    "noscript",
    "svg",
    "iframe",
]
STRUCTURAL_NOISE_TAGS = [
    "header",
    "nav",
    "footer",
    "aside",
]
NOISE_ROLES = [
    "navigation",
    "banner",
    "contentinfo",
]
NOISE_ATTR_PARTS = [
    "menu",
    "submenu",
    "navbar",
    "breadcrumb",
    "cookie",
    "modal",
    "popup",
    "footer",
    "header",
    "social",
    "share",
    "sidebar",
]
MAIN_ATTR_VALUES = {
    "content",
    "main",
    "property",
    "detail",
    "entry",
    "post",
}
MAIN_CONTENT_SELECTORS = [
    "main",
    '[role="main"]',
    "article",
    ".content",
    ".main",
    ".property",
    ".detail",
    ".entry",
    ".post",
]
NOISE_LINES = {
    "toggle submenu",
    "abrir menu",
    "abrir menú",
    "cerrar menu",
    "cerrar menú",
    "menu",
    "menú",
    "saltar al contenido",
    "aceptar cookies",
    "configurar cookies",
}
MIN_MAIN_TEXT_LENGTH = 200


@dataclass
class FetchResult:
    text: str
    method: str
    status_code: int | None = None
    status_message: str = ""
    detail: str = ""


def load_url_configs() -> list[dict[str, object]]:
    raw_config = os.getenv("MONITOR_URLS_JSON", "").strip()
    if not raw_config:
        raise RuntimeError(
            "Falta MONITOR_URLS_JSON. Configura las URLs como un secret de GitHub Actions."
        )

    try:
        configs = json.loads(raw_config)
    except json.JSONDecodeError as exc:
        raise RuntimeError("MONITOR_URLS_JSON no contiene JSON valido.") from exc

    if not isinstance(configs, list) or not configs:
        raise RuntimeError("MONITOR_URLS_JSON debe ser una lista con al menos una URL.")

    for index, config in enumerate(configs, start=1):
        if not isinstance(config, dict):
            raise RuntimeError(f"La URL #{index} debe ser un objeto JSON.")
        if not isinstance(config.get("name"), str) or not config["name"].strip():
            raise RuntimeError(f"La URL #{index} no tiene un campo name valido.")
        if not isinstance(config.get("url"), str) or not config["url"].strip():
            raise RuntimeError(f"La URL #{index} no tiene un campo url valido.")
        mode = config.get("mode", "auto")
        if mode not in {"auto", "manual_summary"}:
            raise RuntimeError(
                f"La URL #{index} tiene un mode no valido. Usa auto o manual_summary."
            )

    return configs


def validate_action_to_run() -> str:
    if ACTION_TO_RUN not in {"normal", "debug"}:
        raise RuntimeError("ACTION_TO_RUN debe ser normal o debug.")
    return ACTION_TO_RUN


def is_debug_mode(action_to_run: str) -> bool:
    return action_to_run == "debug"


def is_monitoring_window(now_madrid: datetime) -> bool:
    return 8 <= now_madrid.hour < 22


def is_error_reminder_window(now_madrid: datetime) -> bool:
    return now_madrid.hour == 12 and now_madrid.minute < 15


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def build_auto_name_from_url(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.lower().split("@")[-1].split(":")[0]
    host = host.removeprefix("www.")
    return slugify(host) or "nueva-url"


def normalize_url(value: str) -> str:
    return value.strip().rstrip(".,;:!?)\"]}")


def extract_first_url(text: str) -> str | None:
    match = URL_PATTERN.search(text)
    if not match:
        return None

    url = normalize_url(match.group(0))
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return url


def load_telegram_offset() -> int:
    if not TELEGRAM_OFFSET_PATH.exists():
        return 0
    raw_offset = TELEGRAM_OFFSET_PATH.read_text(encoding="utf-8").strip()
    if not raw_offset:
        return 0
    try:
        return int(raw_offset)
    except ValueError:
        return 0


def save_telegram_offset(offset: int) -> None:
    TELEGRAM_OFFSET_PATH.parent.mkdir(parents=True, exist_ok=True)
    TELEGRAM_OFFSET_PATH.write_text(str(offset), encoding="utf-8")


def remove_basic_noise(soup: BeautifulSoup) -> None:
    for tag in soup(NOISE_TAGS):
        tag.decompose()


def remove_structural_noise(soup: BeautifulSoup) -> None:
    for tag in soup(STRUCTURAL_NOISE_TAGS):
        tag.decompose()

    for role in NOISE_ROLES:
        for tag in soup.select(f'[role="{role}"]'):
            tag.decompose()

    noisy_tags = []
    for tag in list(soup.find_all(True)):
        if tag.parent is None:
            continue

        attr_values = []
        tag_id = tag.get("id")
        if isinstance(tag_id, str):
            attr_values.append(tag_id)

        classes = tag.get("class")
        if isinstance(classes, list):
            attr_values.extend(str(item) for item in classes)
        elif isinstance(classes, str):
            attr_values.append(classes)

        normalized_attrs = [value.lower() for value in attr_values]
        if any(value in MAIN_ATTR_VALUES for value in normalized_attrs):
            continue

        joined_attrs = " ".join(normalized_attrs)
        if joined_attrs and any(noise in joined_attrs for noise in NOISE_ATTR_PARTS):
            noisy_tags.append(tag)

    for tag in noisy_tags:
        if tag.parent is not None:
            tag.decompose()


def extract_lines_from_root(content_root: object) -> str:
    text = content_root.get_text("\n")
    lines = []
    for line in text.splitlines():
        cleaned = re.sub(r"\s+", " ", line).strip()
        if len(cleaned) >= 2 and cleaned.casefold() not in NOISE_LINES:
            lines.append(cleaned)

    deduplicated = []
    previous = None
    for line in lines:
        if line != previous:
            deduplicated.append(line)
        previous = line

    return "\n".join(deduplicated)


def select_main_content_root(soup: BeautifulSoup) -> object:
    content_root = None
    for selector in MAIN_CONTENT_SELECTORS:
        content_root = soup.select_one(selector)
        if content_root is not None:
            break
    if content_root is None:
        content_root = soup.body or soup
    return content_root


def clean_text(html: str | bytes) -> str:
    soup = BeautifulSoup(html, "html.parser")
    remove_basic_noise(soup)

    broad_root = soup.body or soup
    broad_text = extract_lines_from_root(broad_root)

    focused_soup = BeautifulSoup(str(soup), "html.parser")
    remove_structural_noise(focused_soup)
    focused_root = select_main_content_root(focused_soup)
    focused_text = extract_lines_from_root(focused_root)

    if len(focused_text) >= MIN_MAIN_TEXT_LENGTH:
        return focused_text
    return broad_text


def canonical_text(text: str) -> str:
    lines = []
    seen = set()

    for line in text.splitlines():
        cleaned = re.sub(r"\s+", " ", line).strip()
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        lines.append(cleaned)

    return "\n".join(sorted(lines, key=str.casefold))


def looks_blocked(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in BLOCKED_MARKERS)


def has_expected_terms(text: str, expected_terms: Iterable[str]) -> bool:
    lowered = text.lower()
    return all(term.lower() in lowered for term in expected_terms)


def is_idealista_url(url: str) -> bool:
    return "idealista.com" in urlparse(url).netloc.lower()


def unique_lines(lines: Iterable[str]) -> list[str]:
    result = []
    seen = set()
    for line in lines:
        cleaned = re.sub(r"\s+", " ", line).strip()
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


def meta_content(soup: BeautifulSoup, selector: str) -> str:
    tag = soup.select_one(selector)
    if tag is None:
        return ""
    value = tag.get("content")
    return value.strip() if isinstance(value, str) else ""


def extract_idealista_text(html: str | bytes) -> str:
    soup = BeautifulSoup(html, "html.parser")
    remove_basic_noise(soup)

    lines = []
    for selector in (
        "title",
        'meta[name="description"]',
        'meta[property="og:title"]',
        'meta[property="og:description"]',
    ):
        if selector.startswith("meta"):
            lines.append(meta_content(soup, selector))
        else:
            tag = soup.select_one(selector)
            if tag is not None:
                lines.append(tag.get_text(" ", strip=True))

    useful_selectors = [
        ".main-info__title-main",
        ".main-info__title-minor",
        ".info-data",
        ".info-features",
        ".commentsContainer .comment",
        "#details",
        ".table__new-dev-typologies",
        "#headerMap",
        "#stats",
        ".ad-reference-container",
        ".professional-name",
    ]
    for selector in useful_selectors:
        for tag in soup.select(selector):
            lines.append(tag.get_text("\n", strip=True))

    if not any(lines):
        return clean_text(html)

    return "\n".join(unique_lines(lines))


def has_useful_listing_content(text: str, url: str) -> bool:
    lowered = text.lower()
    if len(text) < 200 or looks_blocked(text):
        return False

    if is_idealista_url(url):
        strong_markers = [
            "obra nueva",
            "pisos disponibles",
            "referencia del anuncio",
            "comentario del anunciante",
            "características básicas",
            "precio",
            "desde",
        ]
        return sum(1 for marker in strong_markers if marker in lowered) >= 2

    return True


def extract_text_for_url(html: str | bytes, url: str) -> str:
    if is_idealista_url(url):
        idealista_text = extract_idealista_text(html)
        if has_useful_listing_content(idealista_text, url):
            return idealista_text
    return clean_text(html)


def fetch_with_http(url: str) -> FetchResult:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    }
    with httpx.Client(headers=headers, follow_redirects=True, timeout=30) as client:
        response = client.get(url)
        text = extract_text_for_url(response.content, url)
        if response.is_error and not has_useful_listing_content(text, url):
            response.raise_for_status()

        method = "http"
        detail = ""
        if response.is_error:
            method = f"http_partial_{response.status_code}"
            detail = (
                "Se extrajo contenido util del HTML recibido aunque la respuesta "
                f"fue {response.status_code}."
            )

        return FetchResult(
            text=text,
            method=method,
            status_code=response.status_code,
            status_message=response.reason_phrase,
            detail=detail,
        )


def fetch_with_browser_mode(url: str, java_script_enabled: bool) -> FetchResult:
    try:
        from playwright.sync_api import sync_playwright
        from playwright_stealth import Stealth
    except Exception as exc:  # pragma: no cover - depends on optional browser install
        raise RuntimeError(
            "Playwright o playwright-stealth no estan instalados o Chromium no esta disponible. "
            "Ejecuta: pip install playwright playwright-stealth && python -m playwright install chromium"
        ) from exc

    method = "browser" if java_script_enabled else "browser_no_js"
    title = ""
    final_url = url
    response_status: int | None = None

    PLAYWRIGHT_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(PLAYWRIGHT_PROFILE_DIR),
            headless=HEADLESS,
            slow_mo=SLOW_MO_MS,
            user_agent=USER_AGENT,
            locale="es-ES",
            timezone_id="Europe/Madrid",
            viewport={"width": 1366, "height": 900},
            java_script_enabled=java_script_enabled,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )

        page = context.new_page()
        Stealth().apply_stealth_sync(page)

        try:
            response = page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=PAGE_LOAD_TIMEOUT_MS,
            )
            response_status = response.status if response else None
            page.wait_for_timeout(8_000)

            for selector in [
                "button:has-text('Aceptar')",
                "button:has-text('Aceptar todo')",
                "button:has-text('Consentir')",
                "button:has-text('Guardar configuración')",
                "button:has-text('Guardar configuracion')",
                "button:has-text('Rechazar')",
            ]:
                try:
                    locator = page.locator(selector)
                    if locator.count() > 0:
                        locator.first.click(timeout=3_000)
                        page.wait_for_timeout(2_000)
                        break
                except Exception:
                    pass

            for _ in range(5):
                page.mouse.wheel(0, 1200)
                page.wait_for_timeout(1_000)

            title = page.title()
            final_url = page.url
            html = page.content()
        finally:
            context.close()

    text = extract_text_for_url(html, url)
    detail = f"Titulo: {title}. URL final: {final_url}. Longitud texto: {len(text)}."

    if response_status and response_status >= 400 and has_useful_listing_content(text, url):
        method = f"{method}_partial_{response_status}"
        detail += f" Se extrajo contenido util aunque la respuesta fue {response_status}."

    return FetchResult(
        text=text,
        method=method,
        status_code=response_status,
        status_message="",
        detail=detail,
    )

def fetch_with_browser(url: str) -> FetchResult:
    return fetch_with_browser_mode(url, java_script_enabled=True)


def fetch_with_browser_no_js(url: str) -> FetchResult:
    return fetch_with_browser_mode(url, java_script_enabled=False)


def fetch_page(config: dict[str, object]) -> FetchResult:
    name = str(config["name"])
    url = str(config["url"])
    expected_terms = list(config.get("expected_terms", []))
    strict_expected_terms = bool(config.get("strict_expected_terms", False))

    errors = []
    results = []

    if is_idealista_url(url):
        fetchers = (fetch_with_browser, fetch_with_http)
    else:
        fetchers = (fetch_with_http, fetch_with_browser, fetch_with_browser_no_js)

    for fetcher in fetchers:
        try:
            result = fetcher(url)
            results.append(result)
            is_too_short = len(result.text) < 200
            is_blocked = looks_blocked(result.text)
            lacks_expected_terms = expected_terms and not has_expected_terms(
                result.text, expected_terms
            )

            if not is_too_short and not is_blocked and not (
                strict_expected_terms and lacks_expected_terms
            ):
                return result

            reason_parts = []
            if is_too_short:
                reason_parts.append("texto demasiado corto")
            if is_blocked:
                reason_parts.append("posible bloqueo/verificacion")
            if strict_expected_terms and lacks_expected_terms:
                reason_parts.append("no aparecen los terminos esperados")
            errors.append(f"{result.method}: {', '.join(reason_parts)}")
        except Exception as exc:
            errors.append(f"{fetcher.__name__}: {exc}")

    if results and not strict_expected_terms and not is_idealista_url(url):
        best = max(results, key=lambda item: len(item.text))
        if best.text:
            return best

    joined_errors = " | ".join(errors) if errors else "sin detalle"
    raise RuntimeError(f"No se pudo acceder al contenido real de {name}: {joined_errors}")


def load_previous(path: Path) -> str | None:
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def save_current(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def load_state_hashes() -> dict[str, str]:
    if not MONITOR_STATE_JSON:
        return {}

    try:
        payload = json.loads(MONITOR_STATE_JSON)
    except json.JSONDecodeError:
        print("MONITOR_STATE_JSON no contiene JSON valido. Se ignora.", file=sys.stderr)
        return {}

    if not isinstance(payload, dict):
        print("MONITOR_STATE_JSON no es un objeto JSON. Se ignora.", file=sys.stderr)
        return {}

    hashes = {}
    for key, value in payload.items():
        if isinstance(key, str) and isinstance(value, str):
            hashes[key] = value
    return hashes


def make_before_after_summary(old: str, new: str, max_lines: int = 18) -> tuple[str, str]:
    before_lines = []
    after_lines = []

    diff = difflib.unified_diff(
        old.splitlines(),
        new.splitlines(),
        fromfile="antes",
        tofile="ahora",
        lineterm="",
        n=0,
    )
    for line in diff:
        if line.startswith(("+++", "---", "@@")):
            continue
        if line.startswith("-"):
            before_lines.append(line[1:])
        elif line.startswith("+"):
            after_lines.append(line[1:])
        if len(before_lines) >= max_lines and len(after_lines) >= max_lines:
            break

    before = "\n".join(before_lines[:max_lines]) or "Sin lineas eliminadas claras."
    after = "\n".join(after_lines[:max_lines]) or "Sin lineas nuevas claras."

    if len(before_lines) > max_lines:
        before += "\n... antes recortado"
    if len(after_lines) > max_lines:
        after += "\n... despues recortado"

    return before, after


def split_message(message: str, limit: int = 3900) -> list[str]:
    if len(message) <= limit:
        return [message]

    chunks = []
    current = ""
    for line in message.splitlines():
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def send_telegram(message: str) -> None:
    if DRY_RUN:
        print("\n--- MENSAJE TELEGRAM DRY_RUN ---")
        print(message)
        print("--- FIN MENSAJE ---\n")
        return

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram no configurado: faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID")
        print(message)
        return

    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    with httpx.Client(timeout=30) as client:
        for chunk in split_message(message):
            response = client.post(
                api_url,
                json={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": chunk,
                    "disable_web_page_preview": True,
                },
            )
            response.raise_for_status()


def send_telegram_document(path: Path, caption: str) -> None:
    is_text_document = path.suffix.lower() == ".txt"
    content_type = "text/plain" if is_text_document else "image/png"

    if DRY_RUN:
        print("\n--- DOCUMENTO TELEGRAM DRY_RUN ---")
        print(f"Archivo: {path.name}")
        print(f"Caption: {caption}")
        if path.exists():
            print(f"Tamaño aproximado: {path.stat().st_size} bytes")
        if is_text_document:
            preview = path.read_text(encoding="utf-8", errors="replace")[:1200]
            print("Vista previa:")
            print(preview)
        print("--- FIN DOCUMENTO ---\n")
        return

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram no configurado: faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID")
        print(f"No se envio el documento {path.name}")
        return

    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"
    with httpx.Client(timeout=60) as client:
        with path.open("rb") as document:
            response = client.post(
                api_url,
                data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption},
                files={"document": (path.name, document, content_type)},
            )
            response.raise_for_status()


def fetch_telegram_updates(offset: int) -> list[dict[str, object]]:
    if TELEGRAM_UPDATES_JSON:
        try:
            updates = json.loads(TELEGRAM_UPDATES_JSON)
        except json.JSONDecodeError as exc:
            raise RuntimeError("TELEGRAM_UPDATES_JSON no contiene JSON valido.") from exc
        if not isinstance(updates, list):
            raise RuntimeError("TELEGRAM_UPDATES_JSON debe ser una lista.")
        return [update for update in updates if isinstance(update, dict)]

    if not TELEGRAM_BOT_TOKEN:
        print("Telegram no configurado: no se revisan mensajes entrantes.")
        return []

    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    params: dict[str, object] = {
        "timeout": 0,
        "allowed_updates": json.dumps(["message"]),
    }
    if offset:
        params["offset"] = offset + 1

    with httpx.Client(timeout=30) as client:
        response = client.get(api_url, params=params)
        response.raise_for_status()
        payload = response.json()

    if not payload.get("ok"):
        raise RuntimeError("Telegram getUpdates no devolvio ok=true.")

    result = payload.get("result", [])
    if not isinstance(result, list):
        raise RuntimeError("Telegram getUpdates devolvio un formato no esperado.")
    return [update for update in result if isinstance(update, dict)]


def try_send_telegram(message: str) -> None:
    try:
        send_telegram(message)
    except Exception as exc:
        print(f"No se pudo enviar el mensaje de Telegram: {exc}", file=sys.stderr)
        print(
            "El mensaje no se muestra en logs para no publicar URLs privadas.",
            file=sys.stderr,
        )


def try_send_telegram_document(path: Path, caption: str) -> None:
    try:
        send_telegram_document(path, caption)
    except Exception as exc:
        print(f"No se pudo enviar el documento de Telegram: {exc}", file=sys.stderr)
        print(
            "El documento no se muestra en logs para no publicar URLs privadas.",
            file=sys.stderr,
        )


def encrypt_github_secret(public_key_value: str, secret_value: str) -> str:
    try:
        from nacl import encoding, public
    except Exception as exc:  # pragma: no cover - depends on dependency install
        raise RuntimeError("Falta PyNaCl para cifrar el secret de GitHub.") from exc

    public_key = public.PublicKey(
        public_key_value.encode("utf-8"),
        encoding.Base64Encoder(),
    )
    sealed_box = public.SealedBox(public_key)
    encrypted = sealed_box.encrypt(secret_value.encode("utf-8"))
    return base64.b64encode(encrypted).decode("utf-8")


def update_github_secret(
    secret_name: str,
    secret_value: str,
    dry_run_detail: str = "",
) -> None:
    if DRY_RUN:
        print("\n--- ACTUALIZACION SECRET DRY_RUN ---")
        print(f"Secret: {secret_name}")
        if dry_run_detail:
            print(dry_run_detail)
        print("--- FIN ACTUALIZACION SECRET ---\n")
        return

    if not GH_SECRETS_PAT:
        raise RuntimeError(f"Falta GH_SECRETS_PAT para actualizar {secret_name}.")
    if not GITHUB_REPOSITORY or "/" not in GITHUB_REPOSITORY:
        raise RuntimeError("GITHUB_REPOSITORY no tiene formato owner/repo.")

    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {GH_SECRETS_PAT}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    base_url = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/actions/secrets"

    with httpx.Client(headers=headers, timeout=30) as client:
        public_key_response = client.get(f"{base_url}/public-key")
        public_key_response.raise_for_status()
        public_key_payload = public_key_response.json()

        encrypted_value = encrypt_github_secret(
            str(public_key_payload["key"]),
            secret_value,
        )
        update_response = client.put(
            f"{base_url}/{secret_name}",
            json={
                "encrypted_value": encrypted_value,
                "key_id": public_key_payload["key_id"],
            },
        )
        update_response.raise_for_status()


def update_monitor_urls_secret(configs: list[dict[str, object]]) -> None:
    secret_value = json.dumps(configs, ensure_ascii=False, indent=2)
    update_github_secret(URLS_SECRET_NAME, secret_value, f"URLs totales: {len(configs)}")


def update_monitor_state_secret(state_hashes: dict[str, str]) -> None:
    secret_value = json.dumps(state_hashes, ensure_ascii=False, indent=2, sort_keys=True)
    update_github_secret(
        STATE_SECRET_NAME,
        secret_value,
        f"Estados totales: {len(state_hashes)}",
    )


def is_allowed_telegram_user(message: dict[str, object]) -> bool:
    sender = message.get("from")
    if not isinstance(sender, dict):
        return False

    sender_id = sender.get("id")
    allowed_id = TELEGRAM_ALLOWED_USER_ID or TELEGRAM_CHAT_ID
    return bool(allowed_id) and str(sender_id) == allowed_id


def add_urls_from_telegram_messages(
    configs: list[dict[str, object]],
) -> list[dict[str, object]]:
    offset = load_telegram_offset()
    updates = fetch_telegram_updates(offset)
    if not updates:
        return configs

    existing_urls = {normalize_url(str(config["url"])) for config in configs}
    new_configs = list(configs)
    urls_to_add: list[str] = []
    max_processed_update_id = offset

    for update in updates:
        update_id = update.get("update_id")
        if not isinstance(update_id, int) or update_id <= offset:
            continue

        message = update.get("message")
        if not isinstance(message, dict):
            max_processed_update_id = max(max_processed_update_id, update_id)
            continue

        text = message.get("text")
        if not isinstance(text, str):
            max_processed_update_id = max(max_processed_update_id, update_id)
            continue

        if not is_allowed_telegram_user(message):
            max_processed_update_id = max(max_processed_update_id, update_id)
            continue

        url = extract_first_url(text)
        if not url:
            max_processed_update_id = max(max_processed_update_id, update_id)
            continue

        if url in existing_urls:
            try_send_telegram(
                f"ℹ️ URL ya existia\n\n🔗 URL: {url}\n\nNo se ha añadido de nuevo."
            )
            max_processed_update_id = max(max_processed_update_id, update_id)
            continue
        if url in urls_to_add:
            max_processed_update_id = max(max_processed_update_id, update_id)
            continue

        urls_to_add.append(url)
        max_processed_update_id = max(max_processed_update_id, update_id)

    if not urls_to_add:
        save_telegram_offset(max_processed_update_id)
        return configs

    for url in urls_to_add:
        new_configs.append(
            {
                "name": build_auto_name_from_url(url),
                "url": url,
                "mode": "auto",
            }
        )

    try:
        update_monitor_urls_secret(new_configs)
    except Exception as exc:
        detail = clean_error_detail(exc)
        for url in urls_to_add:
            try_send_telegram(
                f"⚠️ No se pudo añadir la URL\n\n🔗 URL: {url}\n\n🧾 Detalle: {detail}"
            )
        print(
            "No se pudo actualizar MONITOR_URLS_JSON. "
            "No se avanza el offset de Telegram para poder reintentar.",
            file=sys.stderr,
        )
        return configs

    for url in urls_to_add:
        try_send_telegram(
            f"✅ URL añadida\n\n"
            f"🏷️ Nombre: {build_auto_name_from_url(url)}\n"
            f"🔗 URL: {url}\n\n"
            "Se monitorizara en modo auto."
        )

    save_telegram_offset(max_processed_update_id)
    return new_configs


def format_response_status(
    status_code: int | None,
    status_message: str = "",
) -> str:
    response = "sin codigo"
    if status_code is not None:
        response = str(status_code)
        if status_message:
            response += f" {status_message}"
    return response


def status_icon(status: str) -> str:
    return {
        "baseline": "🆕",
        "unchanged": "✅",
        "changed": "🔔",
        "manual_summary": "👀",
        "error": "⚠️",
    }.get(status, "ℹ️")


def build_debug_summary(
    checked_at: datetime,
    items: list[dict[str, object]],
) -> str:
    total = len(items)
    changed = sum(1 for item in items if item.get("status") == "changed")
    errors = sum(1 for item in items if item.get("status") == "error")
    unchanged = sum(1 for item in items if item.get("status") == "unchanged")
    baseline = sum(1 for item in items if item.get("status") == "baseline")
    manual = sum(1 for item in items if item.get("status") == "manual_summary")

    lines = [
        "🧪 Resumen debug",
        "",
        f"🕒 Fecha Madrid: {checked_at.isoformat()}",
        f"📌 URLs revisadas: {total}",
        f"🔔 Cambios: {changed}",
        f"✅ Sin cambios: {unchanged}",
        f"🆕 Baseline: {baseline}",
        f"👀 Manuales: {manual}",
        f"⚠️ Errores: {errors}",
        "",
        "Detalle:",
    ]

    for item in items:
        status = str(item.get("status", "unknown"))
        method = str(item.get("method", "unknown"))
        status_code = item.get("status_code")
        code = status_code if isinstance(status_code, int) else None
        response = format_response_status(code, str(item.get("status_message") or ""))

        lines.extend(
            [
                "",
                f"{status_icon(status)} {item.get('name')}",
                f"URL: {item.get('url')}",
                f"Estado: {status}",
                f"Metodo: {method}",
                f"Respuesta: {response}",
            ]
        )
        detail = str(item.get("detail") or "").strip()
        if detail:
            lines.append(f"Detalle: {detail}")

    return "\n".join(lines)


def send_debug_summary(
    checked_at: datetime,
    items: list[dict[str, object]],
) -> None:
    message = (
        build_debug_summary(checked_at, items)
        if items
        else f"🧪 Resumen debug\n\n🕒 Fecha Madrid: {checked_at.isoformat()}\n📌 No se reviso ninguna URL."
    )
    try_send_telegram(message)


def clean_error_detail(error: Exception) -> str:
    detail = str(error).splitlines()[0]
    detail = detail.split(" | browser:", 1)[0]
    return re.sub(r" for url 'https?://[^']+'$", "", detail)


def make_text_file_content(
    name: str,
    url: str,
    method: str,
    status: str,
    checked_at: datetime,
    text: str,
) -> str:
    content = (
        f"Nombre: {name}\n"
        f"URL: {url}\n"
        f"Metodo usado: {method}\n"
        f"Estado: {status}\n"
        f"Fecha Madrid: {checked_at.isoformat()}\n\n"
        f"Texto extraido:\n{text}\n"
    )
    encoded = content.encode("utf-8")
    if len(encoded) <= MAX_TEXT_FILE_BYTES:
        return content

    suffix = (
        "\n\n[Texto recortado porque el archivo era demasiado grande "
        "para enviarlo de forma estable por Telegram.]\n"
    )
    suffix_bytes = suffix.encode("utf-8")
    allowed = MAX_TEXT_FILE_BYTES - len(suffix_bytes)
    truncated = encoded[:allowed].decode("utf-8", errors="ignore")
    return truncated + suffix


def write_text_report(
    directory: Path,
    name: str,
    url: str,
    method: str,
    status: str,
    checked_at: datetime,
    text: str,
) -> Path:
    path = directory / f"{slugify(name)}.txt"
    content = make_text_file_content(name, url, method, status, checked_at, text)
    path.write_text(content, encoding="utf-8")
    return path


def capture_error_screenshot(url: str, name: str, directory: Path) -> Path:
    try:
        from playwright.sync_api import sync_playwright
        from playwright_stealth import stealth_sync
    except Exception as exc:  # pragma: no cover - depends on optional browser install
        raise RuntimeError(
            "Playwright o playwright-stealth no estan instalados o Chromium no esta disponible. "
            "Ejecuta: pip install playwright playwright-stealth && python -m playwright install chromium"
        ) from exc

    path = directory / f"screenshot-{slugify(name)}.png"
    PLAYWRIGHT_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(PLAYWRIGHT_PROFILE_DIR),
            headless=HEADLESS,
            slow_mo=SLOW_MO_MS,
            user_agent=USER_AGENT,
            locale="es-ES",
            timezone_id="Europe/Madrid",
            viewport={"width": 1366, "height": 900},
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        page = context.new_page()
        stealth_sync(page)
        try:
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT_MS)
                page.wait_for_timeout(5_000)
            except Exception:
                page.wait_for_timeout(1_000)
            page.screenshot(path=str(path), full_page=True)
        finally:
            context.close()

    return path

def handle_manual_summary(
    config: dict[str, object],
    notify_telegram: bool,
) -> dict[str, object]:
    name = str(config["name"])
    url = str(config["url"])
    summary = str(config.get("summary", "")).strip()

    message = (
        f"👀 Revision manual necesaria\n\n"
        f"🏷️ Web: {name}\n"
        f"🔗 URL: {url}\n\n"
        "Esta web no se monitoriza automaticamente para evitar saltar restricciones "
        "anti-bot. Revisa el enlace manualmente."
    )
    if summary:
        message += f"\n\n📝 Resumen conocido:\n{summary}"

    print(f"Revision manual necesaria: {name}")
    print("URL: oculta en logs publicos")
    if notify_telegram:
        try_send_telegram(message)

    return {
        "name": name,
        "status": "manual_summary",
        "method": "manual",
    }


def process_url(
    config: dict[str, object],
    checked_at: datetime,
    state_hashes: dict[str, str],
) -> dict[str, object]:
    name = str(config["name"])
    url = str(config["url"])
    state_key = slugify(name)
    state_path = STATE_DIR / f"{state_key}.txt"

    result = fetch_page(config)
    current_text = canonical_text(result.text)
    previous = load_previous(state_path)
    save_current(state_path, current_text)

    current_hash = hashlib.sha256(current_text.encode("utf-8")).hexdigest()
    previous_hash = state_hashes.get(state_key)

    if previous_hash is None and previous is None:
        return {
            "name": name,
            "status": "baseline",
            "method": result.method,
            "hash": current_hash,
            "state_key": state_key,
            "status_code": result.status_code,
            "status_message": result.status_message,
            "detail": result.detail,
            "_url": url,
            "_text": result.text,
        }

    if previous_hash is None and previous is not None:
        previous_hash = hashlib.sha256(previous.encode("utf-8")).hexdigest()

    if previous_hash == current_hash:
        return {
            "name": name,
            "status": "unchanged",
            "method": result.method,
            "hash": current_hash,
            "state_key": state_key,
            "status_code": result.status_code,
            "status_message": result.status_message,
            "detail": result.detail,
            "_url": url,
            "_text": result.text,
        }

    before, after = make_before_after_summary(previous or "", current_text)
    message = (
        f"🔔 Cambio detectado\n\n"
        f"🏷️ Web: {name}\n"
        f"🕒 Fecha Madrid: {checked_at.isoformat()}\n"
        f"🔗 URL: {url}\n"
        f"🛠️ Metodo usado: {result.method}\n\n"
        f"⬅️ Antes:\n{before}\n\n"
        f"➡️ Despues:\n{after}"
    )
    try_send_telegram(message)

    return {
        "name": name,
        "status": "changed",
        "method": result.method,
        "hash": current_hash,
        "state_key": state_key,
        "status_code": result.status_code,
        "status_message": result.status_message,
        "detail": result.detail,
        "_url": url,
        "_text": result.text,
    }


def main() -> int:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    action_to_run = validate_action_to_run()
    debug_mode = is_debug_mode(action_to_run)
    now_utc = datetime.now(timezone.utc)
    now_madrid = now_utc.astimezone(MADRID_TIMEZONE)

    url_configs = load_url_configs()
    try:
        url_configs = add_urls_from_telegram_messages(url_configs)
    except Exception as exc:
        detail = clean_error_detail(exc)
        print(
            "No se pudieron revisar los mensajes de Telegram.",
            file=sys.stderr,
        )
        if debug_mode:
            try_send_telegram(
                "⚠️ No se pudieron revisar los mensajes de Telegram\n\n"
                f"🧾 Detalle: {detail}"
            )

    if not debug_mode and not is_monitoring_window(now_madrid):
        print(
            "Fuera de ventana de monitorizacion "
            f"({now_madrid.isoformat()}). Ejecucion normal finalizada sin monitorizar."
        )
        return 0

    state_hashes = load_state_hashes()
    updated_state_hashes = dict(state_hashes)
    state_has_changes = False
    results = []
    debug_items: list[dict[str, object]] = []

    with TemporaryDirectory() as temp_dir:
        report_dir = Path(temp_dir)

        for config in url_configs:
            name = str(config["name"])
            try:
                print(f"Revisando: {name}")
                if config.get("mode") == "manual_summary":
                    result = handle_manual_summary(config, notify_telegram=False)
                    print(json.dumps(result, ensure_ascii=False))
                    results.append(result)
                    if debug_mode:
                        debug_items.append(
                            {
                                "name": name,
                                "url": str(config["url"]),
                                "status": "manual_summary",
                                "method": "manual",
                                "detail": "URL configurada para revision manual.",
                            }
                        )
                    continue

                result = process_url(config, now_madrid, state_hashes)

                url = str(result.pop("_url"))
                text = str(result.pop("_text"))
                state_key = str(result.pop("state_key"))
                current_hash = str(result["hash"])
                if updated_state_hashes.get(state_key) != current_hash:
                    updated_state_hashes[state_key] = current_hash
                    state_has_changes = True
                print(json.dumps(result, ensure_ascii=False))
                results.append(result)

                if debug_mode:
                    debug_items.append(
                        {
                            "name": name,
                            "url": url,
                            "status": str(result["status"]),
                            "method": str(result["method"]),
                            "status_code": result.get("status_code"),
                            "status_message": str(result.get("status_message") or ""),
                            "detail": str(result.get("detail") or ""),
                        }
                    )

                if debug_mode or result["status"] == "changed":
                    report_path = write_text_report(
                        report_dir,
                        name,
                        url,
                        str(result["method"]),
                        str(result["status"]),
                        now_madrid,
                        text,
                    )
                    try_send_telegram_document(
                        report_path,
                        f"📄 Texto extraido de {name} ({result['status']})",
                    )
            except Exception as exc:
                error_detail = clean_error_detail(exc)
                should_send_error_screenshot = debug_mode or is_error_reminder_window(
                    now_madrid
                )
                screenshot_detail = ""
                if should_send_error_screenshot:
                    try:
                        screenshot_path = capture_error_screenshot(
                            str(config["url"]),
                            name,
                            report_dir,
                        )
                        try_send_telegram_document(
                            screenshot_path,
                            f"🖼️ Screenshot de error: {name}",
                        )
                    except Exception as screenshot_exc:
                        screenshot_detail = (
                            " | No se pudo capturar screenshot: "
                            f"{clean_error_detail(screenshot_exc)}"
                        )

                telegram_error_message = (
                    f"⚠️ Error monitorizando {name}\n\n"
                    f"🔗 URL: {config['url']}\n\n"
                    f"🧾 Detalle: {error_detail}{screenshot_detail}\n\n"
                    "✅ El bot continua con el resto de paginas."
                )
                log_error_message = (
                    f"Error monitorizando {name}\n\n"
                    "URL: oculta en logs publicos\n\n"
                    f"Detalle: {error_detail}{screenshot_detail}\n\n"
                    "El bot continua con el resto de paginas."
                )
                print(log_error_message, file=sys.stderr)
                if debug_mode:
                    debug_items.append(
                        {
                            "name": name,
                            "url": str(config["url"]),
                            "status": "error",
                            "method": "error",
                            "detail": f"{error_detail}{screenshot_detail}",
                        }
                    )
                elif is_error_reminder_window(now_madrid):
                    try_send_telegram(
                        "⏰ Recordatorio de error de monitorizacion\n\n"
                        + telegram_error_message
                    )
                results.append({"name": name, "status": "error", "error": str(exc)})

    if state_has_changes:
        try:
            update_monitor_state_secret(updated_state_hashes)
        except Exception as exc:
            print(
                "No se pudo actualizar MONITOR_STATE_JSON. "
                "La cache local sigue guardando los textos, pero puede repetirse algun aviso.",
                file=sys.stderr,
            )
            if debug_mode:
                try_send_telegram(
                    "⚠️ No se pudo actualizar el estado privado de monitorizacion\n\n"
                    f"🧾 Detalle: {clean_error_detail(exc)}"
                )

    if debug_mode:
        send_debug_summary(now_madrid, debug_items)

    print("\nResumen final:")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import difflib
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

import httpx
from bs4 import BeautifulSoup


STATE_DIR = Path(os.getenv("STATE_DIR", ".monitor_state"))
LAST_SCAN_PATH = STATE_DIR / "last_scan_at.txt"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
DRY_RUN = os.getenv("DRY_RUN", "").strip().lower() in {"1", "true", "yes", "si"}
SCAN_INTERVAL_MINUTES = int(os.getenv("SCAN_INTERVAL_MINUTES", "15"))
MAX_TEXT_FILE_BYTES = 10 * 1024 * 1024

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

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
]


@dataclass
class FetchResult:
    text: str
    method: str


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

    return configs


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def clean_text(html: str | bytes) -> str:
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "noscript", "svg", "iframe"]):
        tag.decompose()

    text = soup.get_text("\n")
    lines = []
    for line in text.splitlines():
        cleaned = re.sub(r"\s+", " ", line).strip()
        if len(cleaned) >= 2:
            lines.append(cleaned)

    deduplicated = []
    previous = None
    for line in lines:
        if line != previous:
            deduplicated.append(line)
        previous = line

    return "\n".join(deduplicated)


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


def fetch_with_http(url: str) -> FetchResult:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    }
    with httpx.Client(headers=headers, follow_redirects=True, timeout=30) as client:
        response = client.get(url)
        response.raise_for_status()
        return FetchResult(text=clean_text(response.content), method="http")


def fetch_with_browser(url: str) -> FetchResult:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover - depends on optional browser install
        raise RuntimeError(
            "Playwright no esta instalado o Chromium no esta disponible. "
            "Ejecuta: python -m playwright install chromium"
        ) from exc

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent=USER_AGENT,
            locale="es-ES",
            viewport={"width": 1366, "height": 900},
        )
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(4_000)
            html = page.content()
        finally:
            browser.close()

    return FetchResult(text=clean_text(html), method="browser")


def fetch_page(config: dict[str, object]) -> FetchResult:
    name = str(config["name"])
    url = str(config["url"])
    expected_terms = list(config.get("expected_terms", []))
    strict_expected_terms = bool(config.get("strict_expected_terms", False))

    errors = []
    results = []

    for fetcher in (fetch_with_http, fetch_with_browser):
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

    if results and not strict_expected_terms:
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


def should_skip_scheduled_scan(now: datetime) -> bool:
    if os.getenv("GITHUB_EVENT_NAME") != "schedule":
        return False
    if not LAST_SCAN_PATH.exists():
        return False

    try:
        last_scan = datetime.fromisoformat(LAST_SCAN_PATH.read_text(encoding="utf-8").strip())
    except ValueError:
        return False

    elapsed_seconds = (now - last_scan).total_seconds()
    minimum_seconds = SCAN_INTERVAL_MINUTES * 60
    if elapsed_seconds >= minimum_seconds:
        return False

    remaining_minutes = round((minimum_seconds - elapsed_seconds) / 60, 1)
    print(
        f"Saltando ejecucion programada: solo han pasado "
        f"{round(elapsed_seconds / 60, 1)} minutos desde el ultimo escaneo. "
        f"Faltan aproximadamente {remaining_minutes} minutos."
    )
    return True


def record_scan_time(now: datetime) -> None:
    LAST_SCAN_PATH.parent.mkdir(parents=True, exist_ok=True)
    LAST_SCAN_PATH.write_text(now.isoformat(), encoding="utf-8")


def make_diff_summary(old: str, new: str, max_lines: int = 24) -> str:
    old_lines = old.splitlines()
    new_lines = new.splitlines()
    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile="antes",
        tofile="ahora",
        lineterm="",
        n=2,
    )

    interesting = []
    for line in diff:
        if line.startswith(("+++", "---", "@@")):
            continue
        if line.startswith(("+", "-")):
            interesting.append(line)
        if len(interesting) >= max_lines:
            break

    if not interesting:
        return "El texto ha cambiado, pero no se pudo crear un resumen corto."

    summary = "\n".join(interesting)
    if len(interesting) >= max_lines:
        summary += "\n... resumen recortado por longitud"
    return summary


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
    if DRY_RUN:
        preview = path.read_text(encoding="utf-8", errors="replace")[:1200]
        print("\n--- DOCUMENTO TELEGRAM DRY_RUN ---")
        print(f"Archivo: {path.name}")
        print(f"Caption: {caption}")
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
                files={"document": (path.name, document, "text/plain")},
            )
            response.raise_for_status()


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
        f"Fecha UTC: {checked_at.isoformat()}\n\n"
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


def process_url(config: dict[str, object]) -> dict[str, object]:
    name = str(config["name"])
    url = str(config["url"])
    state_path = STATE_DIR / f"{slugify(name)}.txt"

    result = fetch_page(config)
    current_text = canonical_text(result.text)
    previous = load_previous(state_path)
    save_current(state_path, current_text)

    current_hash = hashlib.sha256(current_text.encode("utf-8")).hexdigest()

    if previous is None:
        return {
            "name": name,
            "status": "baseline",
            "method": result.method,
            "hash": current_hash,
            "_url": url,
            "_text": result.text,
        }

    previous_hash = hashlib.sha256(previous.encode("utf-8")).hexdigest()
    if previous_hash == current_hash:
        return {
            "name": name,
            "status": "unchanged",
            "method": result.method,
            "hash": current_hash,
            "_url": url,
            "_text": result.text,
        }

    summary = make_diff_summary(previous, current_text)
    message = (
        f"Cambio detectado en {name}\n\n"
        f"URL: {url}\n"
        f"Metodo usado: {result.method}\n\n"
        f"Resumen:\n{summary}"
    )
    try_send_telegram(message)

    return {
        "name": name,
        "status": "changed",
        "method": result.method,
        "hash": current_hash,
        "_url": url,
        "_text": result.text,
    }


def main() -> int:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    if should_skip_scheduled_scan(now):
        return 0

    url_configs = load_url_configs()
    record_scan_time(now)

    results = []

    with TemporaryDirectory() as temp_dir:
        report_dir = Path(temp_dir)

        for config in url_configs:
            name = str(config["name"])
            try:
                print(f"Revisando: {name}")
                result = process_url(config)

                url = str(result.pop("_url"))
                text = str(result.pop("_text"))
                print(json.dumps(result, ensure_ascii=False))
                results.append(result)

                report_path = write_text_report(
                    report_dir,
                    name,
                    url,
                    str(result["method"]),
                    str(result["status"]),
                    now,
                    text,
                )
                try_send_telegram_document(
                    report_path,
                    f"Texto extraido de {name} ({result['status']})",
                )
            except Exception as exc:
                telegram_error_message = (
                    f"Error monitorizando {name}\n\n"
                    f"URL: {config['url']}\n\n"
                    f"Detalle: {clean_error_detail(exc)}\n\n"
                    "El bot continua con el resto de paginas."
                )
                log_error_message = (
                    f"Error monitorizando {name}\n\n"
                    "URL: oculta en logs publicos\n\n"
                    f"Detalle: {clean_error_detail(exc)}\n\n"
                    "El bot continua con el resto de paginas."
                )
                print(log_error_message, file=sys.stderr)
                try_send_telegram(telegram_error_message)
                results.append({"name": name, "status": "error", "error": str(exc)})

    print("\nResumen final:")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

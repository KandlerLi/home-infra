#!/usr/bin/env python3
"""Build a Finanzfluss Sankey PNG from a Nextcloud budget workbook.

Runs as a systemd oneshot triggered by a timer (see
sankey-export.service/.timer). Reads the workbook and writes the PNG over
WebDAV only -- it never touches Nextcloud's data directory on disk. A
cheap WebDAV ETag check skips the expensive Playwright export entirely
when the workbook hasn't changed since the last run, which is what makes
running this every minute cheap rather than wasteful.

The chart itself is not drawn locally: this builds a finanzfluss.de
permalink from the parsed income/expenses, then drives headless Chromium
to that page and intercepts the site's own Highcharts "Download PNG"
export request rather than screenshotting the page. Finanzfluss' public
Highcharts export server can answer that exact request with HTTP 429, so
the export is aborted before it reaches their server and the same SVG is
rasterized locally instead.
"""

from __future__ import annotations

import base64
import json
import math
import os
import re
import sys
import xml.etree.ElementTree as ET
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote, urlencode, parse_qs

import requests
from openpyxl import load_workbook

BASE_URL = "https://www.finanzfluss.de/rechner/flussdiagramm/"
DAV = "DAV:"

ENDPOINT_HOST = os.environ.get("SANKEY_EXPORT_ENDPOINT_HOST", "127.0.0.1")
ENDPOINT_PORT = int(os.environ.get("SANKEY_EXPORT_ENDPOINT_PORT", "11000"))
HTTP_HOST = os.environ.get("SANKEY_EXPORT_HTTP_HOST", "nextcloud.jkandler.de")
USERNAME = os.environ.get("SANKEY_EXPORT_USERNAME", "")
APP_PASSWORD_FILE = os.environ.get(
    "SANKEY_EXPORT_APP_PASSWORD_FILE", "/etc/sankey-export/app-password"
)
REMOTE_DIR = os.environ.get("SANKEY_EXPORT_REMOTE_DIR", "Documents/Finanzen").strip("/")
WORKBOOK_NAME = os.environ.get("SANKEY_EXPORT_WORKBOOK_NAME", "Finanzfluss_Nextcloud.xlsx")
OUTPUT_DIR = Path(os.environ.get("SANKEY_EXPORT_OUTPUT_DIR", "/var/lib/sankey-export/out"))
STATE_FILE = Path(
    os.environ.get("SANKEY_EXPORT_STATE_FILE", "/var/lib/sankey-export/last-etag")
)


@dataclass(frozen=True)
class IncomeRow:
    name: str
    amount: float


@dataclass(frozen=True)
class CostRow:
    category: str
    subcategory: str
    amount: float


def read_app_password(path: str) -> str:
    value = Path(path).read_text(encoding="utf-8").strip()
    if len(value) < 16 or value == "CHANGE_ME":
        raise RuntimeError("Nextcloud app password is not configured")
    return value


def active(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "wahr", "yes", "ja", "x", "✓", "✅"}


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).strip().split())


def clean_amount(value: Any, *, row_description: str) -> float:
    if value is None or value == "":
        raise ValueError(f"Missing amount for {row_description}")
    if isinstance(value, str):
        normalized = value.strip().replace("€", "").replace(" ", "")
        if "," in normalized and "." in normalized:
            normalized = normalized.replace(".", "").replace(",", ".")
        else:
            normalized = normalized.replace(",", ".")
        value = normalized
    try:
        amount = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid amount for {row_description}: {value!r}") from exc
    if not math.isfinite(amount) or amount <= 0:
        raise ValueError(f"Amount must be greater than 0 for {row_description}: {amount}")
    return round(amount, 2)


# The workbook's sheet/tab names ("Einnahmen"/"Ausgaben") and its budget
# row name ("Budget") are the real template's literal structure, not our
# own UI text -- they must match the actual spreadsheet, not be translated.
def read_workbook(workbook_path: Path) -> tuple[list[IncomeRow], list[CostRow]]:
    wb = load_workbook(workbook_path, data_only=False)
    missing = {"Einnahmen", "Ausgaben"} - set(wb.sheetnames)
    if missing:
        raise ValueError(f"Missing worksheet(s): {', '.join(sorted(missing))}")

    income_sheet = wb["Einnahmen"]
    cost_sheet = wb["Ausgaben"]

    incomes: list[IncomeRow] = []
    for row in range(4, income_sheet.max_row + 1):
        if not active(income_sheet.cell(row, 1).value):
            continue
        name = clean_text(income_sheet.cell(row, 2).value)
        amount_value = income_sheet.cell(row, 3).value
        if not name and (amount_value is None or amount_value == ""):
            continue
        if not name:
            raise ValueError(f"'Einnahmen' sheet, row {row}: name is missing")
        amount = clean_amount(amount_value, row_description=f"'Einnahmen' row {row} ({name})")
        incomes.append(IncomeRow(name=name, amount=amount))

    costs: list[CostRow] = []
    for row in range(4, cost_sheet.max_row + 1):
        if not active(cost_sheet.cell(row, 1).value):
            continue
        category = clean_text(cost_sheet.cell(row, 2).value)
        subcategory = clean_text(cost_sheet.cell(row, 3).value)
        amount_value = cost_sheet.cell(row, 4).value
        if not category and not subcategory and (amount_value is None or amount_value == ""):
            continue
        if not category:
            raise ValueError(f"'Ausgaben' sheet, row {row}: category is missing")

        # "Budget" is calculated automatically from income minus real expenses.
        # A legacy Budget row in the workbook is therefore ignored completely.
        if category.casefold() == "budget":
            continue

        amount = clean_amount(amount_value, row_description=f"'Ausgaben' row {row} ({category})")
        costs.append(CostRow(category=category, subcategory=subcategory, amount=amount))

    if not incomes:
        raise ValueError("No active income rows found")
    if not costs:
        raise ValueError("No active expense rows found")
    return incomes, costs


def percentage(amount: float, total: float) -> float:
    return amount / total * 100 if total else 0.0


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def build_payload(incomes: Iterable[IncomeRow], costs: Iterable[CostRow]):
    income_list = list(incomes)
    cost_list = list(costs)

    income_total = round(sum(row.amount for row in income_list), 2)
    expense_total = round(sum(row.amount for row in cost_list), 2)
    budget = round(income_total - expense_total, 2)

    warnings: list[str] = []

    income_payload = [
        {"n": row.name, "v": row.amount, "p": percentage(row.amount, income_total)}
        for row in income_list
    ]

    grouped: "OrderedDict[str, list[CostRow]]" = OrderedDict()
    for row in cost_list:
        grouped.setdefault(row.category, []).append(row)

    cost_payload: list[dict[str, Any]] = []

    # Percentages are based on total income because Budget fills the remaining
    # amount and the complete Sankey therefore sums to the income total.
    for index, (category, rows) in enumerate(grouped.items(), start=1):
        category_total = round(sum(row.amount for row in rows), 2)
        positions: list[dict[str, Any]] = []
        has_named_subcategory = any(row.subcategory for row in rows)

        if has_named_subcategory:
            used_names: set[str] = set()
            for row in rows:
                # "Sonstiges" ("Miscellaneous") stays German: it becomes a
                # real label on the generated Sankey diagram, alongside the
                # user's own German category names -- translating just this
                # one fallback would look inconsistent on the chart itself.
                sub_name = row.subcategory or "Sonstiges"
                original_name = sub_name
                suffix = 2
                while sub_name in used_names:
                    sub_name = f"{original_name} {suffix}"
                    suffix += 1
                used_names.add(sub_name)

                if not row.subcategory:
                    warnings.append(
                        f"Category '{category}': blank subcategory was "
                        "exported as 'Sonstiges'."
                    )
                positions.append({"n": sub_name, "v": row.amount})

        cost_payload.append(
            {
                "n": category,
                "v": category_total,
                "p": percentage(category_total, income_total),
                "ro": 1 if positions else 0,
                "r": f"ref_costs_{index}",
                "po": positions,
            }
        )

    if budget > 0:
        cost_payload.append(
            {
                "n": "Budget",
                "v": budget,
                "p": percentage(budget, income_total),
                "ro": 0,
                "r": f"ref_costs_{len(cost_payload) + 1}",
                "po": [],
            }
        )
    elif budget < 0:
        warnings.append(
            f"Expenses exceed income by {abs(budget):.2f} €. A negative "
            "budget can't be shown in the Finanzfluss diagram as a normal "
            "expense category."
        )

    return income_payload, cost_payload, income_total, expense_total, budget, warnings


def make_url(income_payload: list[dict[str, Any]], cost_payload: list[dict[str, Any]]) -> str:
    query = urlencode({"i": compact_json(income_payload), "c": compact_json(cost_payload)})
    return f"{BASE_URL}?{query}"


def _accept_cookies_and_prepare(page) -> None:
    # These button labels stay German -- they're finanzfluss.de's own real
    # UI text, not ours to translate; matching English labels would just
    # never find a button on the (German) site.
    for label in ("Alle akzeptieren", "Akzeptieren", "Zustimmen", "Einverstanden"):
        try:
            locator = page.get_by_role("button", name=re.compile(label, re.IGNORECASE)).first
            if locator.is_visible(timeout=800):
                locator.click(timeout=2_000)
                break
        except Exception:
            pass

    try:
        calculate = page.get_by_role("button", name=re.compile("Berechnen", re.IGNORECASE)).first
        if calculate.is_visible(timeout=1000):
            calculate.click(timeout=5000)
    except Exception:
        pass

    page.wait_for_timeout(3000)


def download_png_via_menu(url: str, output_path: Path) -> str | None:
    """Use Finanzfluss' real Highcharts menu export, but render it locally.

    Finanzfluss currently sends "Download PNG image" to the public Highcharts
    export server. That server may respond with HTTP 429. We therefore intercept
    the exact POST request produced by the real menu item, extract the export SVG
    and export parameters, abort the remote request, and rasterize that exact SVG
    locally in Chromium.

    This is not a screenshot of the page. The SVG comes from the actual
    Highcharts "Download PNG image" action.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return (
            "Playwright is missing. Install it with 'pip install playwright', "
            "then run 'playwright install chromium'."
        )

    def decode_export_payload(content_type: str, body: bytes) -> dict[str, str]:
        """Decode JSON, urlencoded or multipart Highcharts export POST data."""
        content_type_lower = content_type.lower()

        if "application/json" in content_type_lower:
            decoded = json.loads(body.decode("utf-8"))
            return {
                str(key): (value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))
                for key, value in decoded.items()
            }

        if "application/x-www-form-urlencoded" in content_type_lower:
            parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True)
            return {key: values[-1] if values else "" for key, values in parsed.items()}

        if "multipart/form-data" in content_type_lower:
            from email.parser import BytesParser
            from email.policy import default

            mime_message = BytesParser(policy=default).parsebytes(
                f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8") + body
            )

            result: dict[str, str] = {}
            for part in mime_message.iter_parts():
                name = part.get_param("name", header="content-disposition")
                if not name:
                    continue
                value = part.get_payload(decode=True) or b""
                charset = part.get_content_charset() or "utf-8"
                result[name] = value.decode(charset, errors="replace")
            return result

        # Last-resort attempt: many Highcharts versions use URL encoding.
        try:
            parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True)
            if parsed:
                return {key: values[-1] if values else "" for key, values in parsed.items()}
        except Exception:
            pass

        raise ValueError(f"Unknown export POST format: {content_type or '(no Content-Type)'}")

    browser = None
    context = None

    try:
        with sync_playwright() as playwright:
            # --no-sandbox: this browser only ever visits one fixed,
            # self-generated finanzfluss.de URL, never arbitrary content, so
            # the sandbox buys little here -- and skipping it avoids
            # Chromium's namespace/setuid sandbox setup ever fighting with
            # the systemd unit's own hardening (NoNewPrivileges, restricted
            # namespaces). Ansible always runs `playwright install chromium`
            # during provisioning, so there's no system-browser fallback to
            # fall back to here.
            browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])

            context = browser.new_context(viewport={"width": 1600, "height": 1200}, device_scale_factor=1)

            captured: dict[str, Any] = {}

            def intercept_export(route) -> None:
                request = route.request
                request_url = request.url.lower()

                is_highcharts_export = request.method.upper() == "POST" and (
                    "export.highcharts.com" in request_url
                    or ("highcharts" in request_url and "export" in request_url)
                )

                if not is_highcharts_export:
                    route.continue_()
                    return

                try:
                    body = request.post_data_buffer or b""
                except Exception:
                    post_data = request.post_data or ""
                    body = post_data.encode("utf-8")

                captured["headers"] = dict(request.headers)
                captured["body"] = body

                print(f"Intercepted Highcharts export request: {request.method} {request.url}")

                # Prevent the call to the public server (and therefore the 429).
                route.abort()

            context.route("**/*", intercept_export)

            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=90_000)
            _accept_cookies_and_prepare(page)

            page.locator(".highcharts-container").first.wait_for(state="visible", timeout=20_000)

            menu_button = page.locator(".highcharts-contextbutton").first
            if not menu_button.count():
                menu_button = page.locator(".highcharts-exporting-group").first
            if not menu_button.count():
                return "Highcharts hamburger menu was not found."

            menu_button.click(timeout=5_000)

            items = page.locator(".highcharts-menu-item")
            png_item = None
            menu_texts: list[str] = []

            for index in range(items.count()):
                item = items.nth(index)
                label = (item.text_content() or "").strip()
                menu_texts.append(label)
                if "png" in label.lower():
                    png_item = item
                    break

            if png_item is None:
                return f"PNG menu item not found. Menu entries: {menu_texts}"

            label = (png_item.text_content() or "").strip()
            print(f"Highcharts menu: clicking '{label}' …")
            png_item.click(timeout=5_000)

            # The route handler runs asynchronously relative to the click.
            for _ in range(40):
                if captured.get("body") is not None:
                    break
                page.wait_for_timeout(100)

            if captured.get("body") is None:
                return "PNG menu item was clicked, but no Highcharts export POST was detected."

            headers = captured.get("headers", {})
            content_type = headers.get("content-type", "")
            payload = decode_export_payload(content_type, captured["body"])

            svg = payload.get("svg", "")
            export_type = payload.get("type", "image/png")
            filename = payload.get("filename", "chart")
            raw_scale = payload.get("scale", "2")

            try:
                scale = float(raw_scale)
            except (TypeError, ValueError):
                scale = 2.0

            if export_type and "png" not in export_type.lower():
                return f"Intercepted export wasn't a PNG: type={export_type!r}"

            if not svg or "<svg" not in svg:
                return f"Export request was intercepted but contains no SVG. Fields: {sorted(payload.keys())}"

            print(f"Export data: {len(svg) / 1024:.1f} KiB SVG, scale {scale:g}, filename {filename!r}")
            print("Rendering the intercepted export SVG on a separate browser page …")

            # The real export click may navigate/destroy the Finanzfluss page
            # (for example because Highcharts submits a form to the export server).
            # Render the already-captured SVG in a fresh blank page instead.
            render_page = context.new_page()
            render_page.goto("about:blank")

            # Rasterize the exact SVG from Highcharts locally in Chromium.
            rasterized = render_page.evaluate(
                """async ({ svg, scale }) => {
                    const parser = new DOMParser();
                    const documentSvg = parser.parseFromString(svg, 'image/svg+xml');
                    const root = documentSvg.documentElement;

                    const parseLength = value => {
                        if (!value) return NaN;
                        return Number.parseFloat(String(value).replace('px', ''));
                    };

                    let width = parseLength(root.getAttribute('width'));
                    let height = parseLength(root.getAttribute('height'));

                    const viewBox = (root.getAttribute('viewBox') || '')
                        .trim()
                        .split(/[ ,]+/)
                        .map(Number);

                    if ((!Number.isFinite(width) || width <= 0) && viewBox.length === 4) {
                        width = viewBox[2];
                    }
                    if ((!Number.isFinite(height) || height <= 0) && viewBox.length === 4) {
                        height = viewBox[3];
                    }

                    if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) {
                        return { ok: false, error: 'Could not determine SVG size: ' + width + 'x' + height };
                    }

                    const serialized = new XMLSerializer().serializeToString(documentSvg);
                    const blob = new Blob([serialized], { type: 'image/svg+xml;charset=utf-8' });
                    const blobUrl = URL.createObjectURL(blob);

                    try {
                        const image = new Image();
                        await new Promise((resolve, reject) => {
                            image.onload = resolve;
                            image.onerror = () => reject(new Error('Could not load the Highcharts export SVG'));
                            image.src = blobUrl;
                        });

                        const outputWidth = Math.max(1, Math.round(width * scale));
                        const outputHeight = Math.max(1, Math.round(height * scale));

                        const canvas = document.createElement('canvas');
                        canvas.width = outputWidth;
                        canvas.height = outputHeight;

                        const ctx = canvas.getContext('2d');
                        if (!ctx) {
                            return { ok: false, error: 'No 2D canvas context' };
                        }

                        ctx.drawImage(image, 0, 0, outputWidth, outputHeight);
                        const dataUrl = canvas.toDataURL('image/png');

                        if (!dataUrl.startsWith('data:image/png;base64,')) {
                            return { ok: false, error: 'No PNG data URL was produced' };
                        }

                        return { ok: true, base64: dataUrl.split(',', 2)[1], width: outputWidth, height: outputHeight };
                    } finally {
                        URL.revokeObjectURL(blobUrl);
                    }
                }""",
                {"svg": svg, "scale": scale},
            )

            if not rasterized.get("ok"):
                return (
                    "Local rendering of the real Highcharts export SVG failed: "
                    f"{rasterized.get('error', 'unknown error')}"
                )

            png_data = base64.b64decode(rasterized["base64"])
            if not png_data.startswith(b"\x89PNG\r\n\x1a\n"):
                return "The generated data is not a valid PNG file."

            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(png_data)

            print(
                f"Saved PNG from the real Highcharts export: {output_path} "
                f"({rasterized['width']}x{rasterized['height']}, {len(png_data) / 1024:.1f} KiB)"
            )
            return None

    except Exception as exc:
        if "Executable doesn't exist" in str(exc):
            return "Playwright's browser is missing. Run 'playwright install chromium' once."
        return f"Could not produce the PNG: {str(exc).splitlines()[0]}"

    finally:
        if context is not None:
            try:
                context.close()
            except Exception:
                pass
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass


class NextcloudWebDAV:
    """WebDAV client fixed to the loopback AIO endpoint and one account."""

    def __init__(self, host: str, port: int, http_host: str, username: str, app_password: str) -> None:
        self.base_url = f"http://{host}:{port}"
        self.http_host = http_host
        self.username = username
        self.root = f"{self.base_url}/remote.php/dav/files/{quote(username, safe='')}"
        self.session = requests.Session()
        self.session.auth = (username, app_password)
        self.session.headers.update(
            {"User-Agent": "sankey-export/1", "Host": http_host}
        )

    def _url(self, remote_path: str) -> str:
        parts = [quote(part, safe="") for part in remote_path.strip("/").split("/") if part]
        return self.root + ("/" + "/".join(parts) if parts else "")

    def etag(self, remote_path: str) -> str | None:
        """Cheap PROPFIND for just the ETag -- no content transferred."""
        body = b'<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop><d:getetag/></d:prop></d:propfind>'
        response = self.session.request(
            "PROPFIND",
            self._url(remote_path),
            data=body,
            headers={"Content-Type": "application/xml", "Depth": "0"},
            timeout=30,
        )
        if response.status_code == 404:
            return None
        if response.status_code != 207:
            raise RuntimeError(f"ETag lookup failed ({response.status_code}): {remote_path}")
        root = ET.fromstring(response.content)
        etag_element = root.find(f".//{{{DAV}}}getetag")
        return etag_element.text if etag_element is not None else None

    def ensure_dir(self, remote_dir: str) -> None:
        current = ""
        for part in [p for p in remote_dir.strip("/").split("/") if p]:
            current = f"{current}/{part}"
            response = self.session.request("MKCOL", self._url(current), timeout=30)
            if response.status_code not in {201, 405}:
                raise RuntimeError(f"Could not create Nextcloud folder ({response.status_code}): {current}")

    def download(self, remote_path: str, local_path: Path) -> None:
        response = self.session.get(self._url(remote_path), timeout=120)
        if response.status_code != 200:
            raise RuntimeError(f"Download failed ({response.status_code}): {remote_path}\n{response.text[:300]}")
        local_path.write_bytes(response.content)

    def upload(self, local_path: Path, remote_path: str) -> None:
        remote_dir = remote_path.rsplit("/", 1)[0] if "/" in remote_path.strip("/") else ""
        if remote_dir:
            self.ensure_dir(remote_dir)
        with local_path.open("rb") as handle:
            response = self.session.put(self._url(remote_path), data=handle, timeout=180)
        if response.status_code not in {200, 201, 204}:
            raise RuntimeError(f"Upload failed ({response.status_code}): {remote_path}\n{response.text[:300]}")


def load_cached_etag(state_path: Path) -> str | None:
    try:
        return state_path.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def save_cached_etag(state_path: Path, etag: str) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(etag + "\n", encoding="utf-8")


def main() -> int:
    remote_workbook = f"{REMOTE_DIR}/{WORKBOOK_NAME}"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    webdav = NextcloudWebDAV(
        ENDPOINT_HOST, ENDPOINT_PORT, HTTP_HOST, USERNAME, read_app_password(APP_PASSWORD_FILE)
    )

    etag = webdav.etag(remote_workbook)
    if etag is None:
        print(f"Workbook not found: {remote_workbook}", file=sys.stderr)
        return 1

    if etag == load_cached_etag(STATE_FILE):
        print("Workbook unchanged, nothing to do.")
        return 0

    print(f"Workbook changed (ETag {etag}), downloading {remote_workbook} …")
    temporary_workbook = OUTPUT_DIR / f".{Path(WORKBOOK_NAME).stem}.download{Path(WORKBOOK_NAME).suffix}"
    try:
        webdav.download(remote_workbook, temporary_workbook)
        incomes, costs = read_workbook(temporary_workbook)
    finally:
        temporary_workbook.unlink(missing_ok=True)

    income_payload, cost_payload, income_total, expense_total, budget, warnings = build_payload(incomes, costs)
    link_url = make_url(income_payload, cost_payload)

    png_path = OUTPUT_DIR / "finanzfluss-diagramm.png"
    png_path.unlink(missing_ok=True)

    png_warning = download_png_via_menu(link_url, png_path)
    if png_warning:
        warnings.append(png_warning)

    if png_path.exists():
        remote_png = f"{REMOTE_DIR}/{png_path.name}"
        print(f"Uploading {png_path.name} to Nextcloud …")
        webdav.upload(png_path, remote_png)
        # Only remember this ETag once a fresh PNG has actually been
        # uploaded -- a failed export leaves the cache stale so the next
        # (1-minute-later) tick retries automatically.
        save_cached_etag(STATE_FILE, etag)

    print(f"Income:   {income_total:.2f} €")
    print(f"Expenses: {expense_total:.2f} €")
    print(f"Budget:   {budget:.2f} €")

    if warnings:
        print("Warnings:")
        for warning in warnings:
            print(f"- {warning}")

    return 0 if png_path.exists() else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)

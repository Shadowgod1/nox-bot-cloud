"""Price list import: text lines, CSV (cp1251 / ;), XLSX with Russian headers, photo via a vision model."""

import base64
import io

import openpyxl
import pytest
from httpx import AsyncClient

from iler.knowledge import prices, vision

OWNER = {"email": "owner@salon.kz", "password": "owner-pass-123", "name": "Owner"}


def csrf(c: AsyncClient) -> dict:
    return {"X-ILER-CSRF": c.cookies.get("iler_csrf", "")}


@pytest.fixture
async def owner(client):
    r = await client.post("/api/setup/owner", json=OWNER, headers={"X-ILER-Setup-Token": "test-setup-token"})
    assert r.status_code == 200, r.text
    return client


def _rows(result) -> list[tuple]:
    return [(r.name, r.price, r.duration_min) for r in result.rows]


def _xlsx(rows: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_text_lines_with_prices_durations_and_section_titles():
    text = (
        "Маникюр — 5000\n"
        "1. Наращивание ресниц 2D — от 12 000 тг, 2 ч\n"
        "- Стрижка женская 4500 ₸ (45 мин)\n"
        "Педикюр: 7000-9000\n"
        "НОГТЕВОЙ СЕРВИС\n"
        "Окрашивание — 1 ч 30 мин — от 15000\n"
    )
    result = prices.parse_text(text)
    assert _rows(result) == [
        ("Маникюр", "5 000 ₸", None),
        ("Наращивание ресниц 2D", "от 12 000 ₸", 120),
        ("Стрижка женская", "4 500 ₸", 45),
        ("Педикюр", "7 000–9 000 ₸", None),
        ("Окрашивание", "от 15 000 ₸", 90),
    ]
    assert result.skipped == 1 and "без цены" in result.warnings()[0]


def test_text_copied_from_a_spreadsheet_uses_cells():
    result = prices.parse_text("Маникюр\t5000\t60\nПедикюр\t7000\t90\n")
    assert _rows(result) == [("Маникюр", "5 000 ₸", 60), ("Педикюр", "7 000 ₸", 90)]


def test_csv_cp1251_semicolon_with_russian_headers():
    data = "Услуга;Цена, тг;Длительность, мин\nМаникюр;5000;60\nПедикюр;от 7000;90\nРАЗДЕЛ;;\n".encode("cp1251")
    result = prices.parse_csv(data)
    assert _rows(result) == [("Маникюр", "5 000 ₸", 60), ("Педикюр", "от 7 000 ₸", 90)]
    assert result.skipped == 1


def test_csv_without_header_guesses_columns():
    data = "Маникюр,5000,60 мин\nПедикюр классический,7000,1.5 ч\n".encode()
    assert _rows(prices.parse_csv(data)) == [("Маникюр", "5 000 ₸", 60), ("Педикюр классический", "7 000 ₸", 90)]


def test_xlsx_title_row_then_header_in_hours():
    data = _xlsx([["Прайс салона"], ["Наименование", "Стоимость", "Время (ч)"], ["Маникюр", 5000, 1],
                  ["Брови", "3 500", 0.5]])
    assert _rows(prices.parse_xlsx(data)) == [("Маникюр", "5 000 ₸", 60), ("Брови", "3 500 ₸", 30)]


def test_unsupported_files_are_explained():
    with pytest.raises(prices.PriceImportError, match="xlsx"):
        prices.sniff_kind("old.xls", b"\xd0\xcf\x11\xe0rest")
    with pytest.raises(prices.PriceImportError, match="HEIC"):
        prices.sniff_kind("IMG_1.HEIC", b"....ftypheic")
    assert prices.sniff_kind("p.png", b"\x89PNG\r\n\x1a\nxxxx") == "image"
    assert prices.sniff_kind("p.xlsx", b"PK\x03\x04") == "xlsx"


def test_long_tables_are_cut():
    text = "\n".join(f"Услуга {i} — {1000 + i}" for i in range(prices.MAX_ROWS + 5))
    result = prices.parse_text(text)
    assert len(result.rows) == prices.MAX_ROWS and result.truncated


async def test_api_text_and_files(owner):
    r = await owner.post("/api/knowledge/import-prices", json={"text": "Маникюр — 5000\nПедикюр — от 7000, 90 мин"},
                         headers=csrf(owner))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "text" and body["coins"] is None
    assert body["rows"] == [{"name": "Маникюр", "price": "5 000 ₸", "duration_min": None},
                            {"name": "Педикюр", "price": "от 7 000 ₸", "duration_min": 90}]

    xlsx = _xlsx([["Услуга", "Цена"], ["Стрижка", 4000]])
    r = await owner.post("/api/knowledge/import-prices", headers=csrf(owner),
                         json={"filename": "price.xlsx", "content_base64": base64.b64encode(xlsx).decode()})
    assert r.status_code == 200, r.text
    assert r.json()["source"] == "xlsx" and r.json()["rows"][0]["name"] == "Стрижка"

    r = await owner.post("/api/knowledge/import-prices", headers=csrf(owner),
                         json={"filename": "old.xls", "content_base64": base64.b64encode(b"\xd0\xcf\x11\xe0x").decode()})
    assert r.status_code == 422 and r.json()["error"]["code"] == "price_import_failed"

    r = await owner.post("/api/knowledge/import-prices", json={"text": "  "}, headers=csrf(owner))
    assert r.status_code == 422


async def test_api_requires_csrf_and_saves_nothing(owner, pool):
    r = await owner.post("/api/knowledge/import-prices", json={"text": "Маникюр — 5000"})
    assert r.status_code == 403
    await owner.post("/api/knowledge/import-prices", json={"text": "Маникюр — 5000"}, headers=csrf(owner))
    assert (await owner.get("/api/settings/bot")).json()["services"] == []


async def test_api_photo_goes_through_vision_and_reports_coins(owner, monkeypatch):
    seen = {}

    async def fake_read(data, mime):
        seen.update(size=len(data), mime=mime)
        return "Маникюр классический | от 5000 ₸ | 60 мин\nПокрытие гель-лак | 4000 ₸ | 45 мин", 12.5

    monkeypatch.setattr(vision, "read_price_photo", fake_read)
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 100
    r = await owner.post("/api/knowledge/import-prices", headers=csrf(owner),
                         json={"filename": "price.png", "content_base64": base64.b64encode(png).decode()})
    assert r.status_code == 200, r.text
    body = r.json()
    assert seen == {"size": len(png), "mime": "image/png"}
    assert body["source"] == "image" and body["coins"] == 12.5
    assert body["rows"] == [{"name": "Маникюр классический", "price": "от 5 000 ₸", "duration_min": 60},
                            {"name": "Покрытие гель-лак", "price": "4 000 ₸", "duration_min": 45}]

    async def no_prices(data, mime):
        return vision.NO_PRICES, 3.0

    monkeypatch.setattr(vision, "read_price_photo", no_prices)
    r = await owner.post("/api/knowledge/import-prices", headers=csrf(owner),
                         json={"filename": "cat.jpg", "content_base64": base64.b64encode(b"\xff\xd8\xff" + b"1" * 10).decode()})
    assert r.status_code == 422 and "прайс" in r.json()["error"]["message"]


async def test_photo_without_vision_provider_is_explained(owner, monkeypatch):
    monkeypatch.setattr(vision.router, "available_providers", lambda: ["gemini"])
    r = await owner.get("/api/knowledge/import-prices")
    assert r.status_code == 200
    with pytest.raises(prices.PriceImportError, match="CSV"):
        await vision.read_price_photo(b"\xff\xd8\xff1", "image/jpeg")

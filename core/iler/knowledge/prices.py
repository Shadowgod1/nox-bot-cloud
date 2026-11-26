"""Price list import: CSV, XLSX, pasted text ("Маникюр — 5000") -> rows {name, price, duration_min}.

Nothing is saved here: the admin shows the rows as an editable preview and stores the result in
``bot.services`` (PUT /api/settings/bot).

Tables: the header row is recognised by Russian / Kazakh / English words (услуга, наименование, цена,
стоимость, длительность, время, ...); without a header the columns are guessed from their content (the
widest text column is the name, a numeric one the price, "60 мин" / small numbers the duration). Rows
without a price and a duration (section titles such as "НОГТЕВОЙ СЕРВИС") are skipped and counted.
"""

import csv
import io
import re
from dataclasses import asdict, dataclass
from datetime import datetime, time

MAX_ROWS = 300
MAX_CELLS_ROWS = 5000  # rows read from one sheet / file at most
NAME_MAX = 160
PRICE_MAX = 60


class PriceImportError(ValueError):
    """The input cannot be read; the message is shown to the owner as is (Russian)."""


@dataclass
class PriceRow:
    name: str
    price: str
    duration_min: int | None

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class ParseResult:
    rows: list[PriceRow]
    skipped: int = 0  # rows without a price or a duration (section titles, notes)
    truncated: bool = False

    def warnings(self) -> list[str]:
        out = []
        if self.skipped:
            out.append(f"Пропущено строк без цены: {self.skipped} (обычно это заголовки разделов).")
        if self.truncated:
            out.append(f"Показаны первые {MAX_ROWS} услуг.")
        return out


# --- cells ---------------------------------------------------------------------------------------

_NUM = r"\d{1,3}(?:[   ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?"
_CURRENCY = r"(?:₸|тг\.?|тенге|kzt|т\.(?!\w)|руб\.?|₽)"
_PRICE_RE = re.compile(
    rf"(?P<from>от\s*)?(?P<a>{_NUM})(?:\s*(?:-|–|—|до)\s*(?P<b>{_NUM}))?\s*(?P<cur>{_CURRENCY})?",
    re.IGNORECASE,
)
_HOURS_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:ч\b|ч\.|час(?:а|ов)?\b|h\b|сағ)", re.IGNORECASE)
_MINUTES_RE = re.compile(r"(\d+)\s*(?:мин(?:ут[аы]?)?\.?|м\b|min\b|минут)", re.IGNORECASE)
_HMM_RE = re.compile(r"^\s*(\d{1,2}):([0-5]\d)\s*$")


def _num(text: str) -> float:
    return float(re.sub(r"[   ]", "", text).replace(",", "."))


def _fmt_amount(value: float) -> str:
    return f"{int(round(value)):,}".replace(",", " ")


def norm_price(value: object) -> str:
    """Price as shown to the client: "5 000 ₸", "от 5 000 ₸", "5 000–7 000 ₸"; text without digits kept."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        return f"{_fmt_amount(float(value))} ₸" if value > 0 else ""
    text = " ".join(str(value).split())
    if not text:
        return ""
    m = None
    for m in _PRICE_RE.finditer(text):
        pass
    if m is None:
        return text[:PRICE_MAX]
    out = _fmt_amount(_num(m.group("a")))
    if m.group("b"):
        out += "–" + _fmt_amount(_num(m.group("b")))
    from_ = bool(m.group("from")) or text.lower().startswith("от")
    return (("от " if from_ else "") + out + " ₸")[:PRICE_MAX]


def parse_duration(value: object, *, hours: bool = False) -> int | None:
    """Minutes from 90, "90 мин", "1,5 ч", "1 ч 30 мин", "1:30", a time cell; None when there is none."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, time):
        minutes = value.hour * 60 + value.minute
        return minutes or None
    if isinstance(value, datetime):
        return parse_duration(value.time())
    if isinstance(value, (int, float)):
        minutes = int(round(float(value) * (60 if hours else 1)))
        return minutes if 0 < minutes <= 1440 else None
    text = str(value).strip().lower()
    if not text:
        return None
    m = _HMM_RE.match(text)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2)) or None
    total = 0.0
    h = _HOURS_RE.search(text)
    if h:
        total += _num(h.group(1)) * 60
    mm = _MINUTES_RE.search(text)
    if mm:
        total += int(mm.group(1))
    if not h and not mm:
        if re.fullmatch(r"\d+(?:[.,]\d+)?", text):
            total = _num(text) * (60 if hours else 1)
        else:
            return None
    minutes = int(round(total))
    return minutes if 0 < minutes <= 1440 else None


def _clean_name(text: object) -> str:
    name = " ".join(str(text or "").split())
    name = re.sub(r"^(?:[-•*·–—]+|\d{1,3}[.)])\s*", "", name)
    name = name.strip(" -–—:;|.,\t")
    if name.lower().endswith(" от"):
        name = name[:-3].rstrip(" -–—:;|.,")
    return name[:NAME_MAX]


# --- tables --------------------------------------------------------------------------------------

_PRICE_KEYS = ("цена", "стоимост", "прайс", "сумма", "тариф", "бағасы", "баға", "құны", "price", "cost", "₸", "тенге")
_DUR_KEYS = ("длительн", "продолжит", "время", "мин", "час", "уақыт", "ұзақтығы", "duration", "time")
_NAME_KEYS = ("услуг", "наименован", "название", "процедур", "позици", "работ", "қызмет", "атауы", "service",
              "name", "item")


def _header_kind(cell: object) -> str | None:
    if not isinstance(cell, str):
        return None
    text = cell.strip().lower()
    if not text or len(text) > 40 or re.search(r"\d", text):
        return None
    if any(k in text for k in _PRICE_KEYS):
        return "price"
    if any(k in text for k in _DUR_KEYS):
        return "duration"
    if any(k in text for k in _NAME_KEYS):
        return "name"
    return None


_UNIT = r"(?:₸|тг\.?|тенге|kzt|т\.?|руб\.?|₽|мин\.?|минут[аы]?|м|ч\.?|час(?:а|ов)?|h|min|сағ)"
# "5000", "от 5 000 ₸", "5000-7000 тг", "60 мин", "1 ч 30 мин", "1:30" (no nested quantifiers: no backtracking blowup)
_NUMERIC_CELL = re.compile(
    rf"^\s*(?:от\s*)?[\d.,:–—\- ]*\d[\d.,:–—\- ]*{_UNIT}?(?:\s*\d+\s*{_UNIT})?\s*$", re.IGNORECASE
)


def _is_blank(cell: object) -> bool:
    return cell is None or (isinstance(cell, str) and not cell.strip())


def _looks_numeric(cell: object) -> bool:
    if isinstance(cell, bool):
        return False
    if isinstance(cell, (int, float)):
        return True
    return isinstance(cell, str) and bool(re.search(r"\d", cell)) and bool(_NUMERIC_CELL.match(cell))


def _guess_columns(rows: list[list]) -> dict[str, int]:
    width = max(len(r) for r in rows)
    text_score = [0] * width
    num_values: list[list[float]] = [[] for _ in range(width)]
    dur_hits = [0] * width
    for r in rows:
        for i, cell in enumerate(r):
            if _is_blank(cell):
                continue
            if _looks_numeric(cell):
                if isinstance(cell, str) and (_MINUTES_RE.search(cell) or _HOURS_RE.search(cell) or _HMM_RE.match(cell)):
                    dur_hits[i] += 1
                    continue
                m = _PRICE_RE.search(str(cell)) if isinstance(cell, str) else None
                try:
                    num_values[i].append(float(cell) if not isinstance(cell, str) else _num(m.group("a")) if m else 0)
                except (TypeError, ValueError):
                    pass
            else:
                text_score[i] += len(str(cell))
    cols: dict[str, int] = {}
    if max(text_score, default=0) > 0:
        cols["name"] = text_score.index(max(text_score))
    numeric = [i for i in range(width) if num_values[i] and i != cols.get("name")]
    if numeric:
        def median(i: int) -> float:
            v = sorted(num_values[i])
            return v[len(v) // 2]
        price = max(numeric, key=median)
        cols["price"] = price
        rest = [i for i in numeric if i != price and median(i) <= 600]
        if rest:
            cols["duration"] = rest[0]
    dur = [i for i in range(width) if dur_hits[i] and i not in cols.values()]
    if dur and "duration" not in cols:
        cols["duration"] = max(dur, key=lambda i: dur_hits[i])
    return cols


def parse_table(rows: list[list]) -> ParseResult:
    rows = [list(r) for r in rows[:MAX_CELLS_ROWS] if any(not _is_blank(c) for c in r)]
    if not rows:
        return ParseResult(rows=[])
    cols: dict[str, int] = {}
    hours = False
    start = 0
    for idx, row in enumerate(rows[:5]):
        kinds = {}
        for i, cell in enumerate(row):
            k = _header_kind(cell)
            if k and k not in kinds:
                kinds[k] = i
        filled = sum(not _is_blank(c) for c in row)
        if filled >= 2 and (len(kinds) >= 2 or "price" in kinds):
            cols = kinds
            start = idx + 1
            if "duration" in kinds:
                head = str(row[kinds["duration"]]).lower()
                hours = bool(re.search(r"час|\bч\b|сағ|hour|\bh\b", head)) and "мин" not in head
            break
    body = rows[start:]
    if not body:
        return ParseResult(rows=[])
    guessed = _guess_columns(body)
    if "name" not in cols:
        name_col = guessed.get("name")
        if name_col is not None and name_col not in cols.values():
            cols["name"] = name_col
        else:
            free = [i for i in range(max(len(r) for r in body)) if i not in cols.values()]
            if free:
                cols["name"] = free[0]
    if not start:  # no header: take the guessed price / duration columns
        for k in ("price", "duration"):
            if k in guessed and guessed[k] not in cols.values():
                cols[k] = guessed[k]
    if "name" not in cols:
        raise PriceImportError("Не нашли колонку с названиями услуг. Нужны колонки «Услуга» и «Цена».")

    def cell(row: list, key: str):
        i = cols.get(key)
        return row[i] if i is not None and i < len(row) else None

    out = ParseResult(rows=[])
    for row in body:
        name = _clean_name(cell(row, "name"))
        if not name:
            continue
        raw_price = cell(row, "price")
        price = norm_price(raw_price)
        duration = parse_duration(cell(row, "duration"), hours=hours)
        if not price and duration is None:
            out.skipped += 1
            continue
        if len(out.rows) >= MAX_ROWS:
            out.truncated = True
            break
        out.rows.append(PriceRow(name=name, price=price, duration_min=duration))
    return out


# --- text ----------------------------------------------------------------------------------------

def parse_line(line: str) -> PriceRow | None:
    """"Маникюр — 5000", "Наращивание ресниц 2D — от 12 000 тг, 2 ч", "1. Стрижка 4500 ₸ (45 мин)"."""
    text = " ".join(line.split())
    if not text:
        return None
    duration = None
    h = _HOURS_RE.search(text)
    mm = _MINUTES_RE.search(text)
    if h or mm:
        duration = parse_duration(" ".join(x.group(0) for x in (h, mm) if x))
        for x in sorted((x for x in (h, mm) if x), key=lambda x: -x.start()):
            text = text[: x.start()] + text[x.end():]
    text = re.sub(r"\(\s*\)", "", text)
    body = re.sub(r"^(?:[-•*·–—]+|\d{1,3}[.)])\s*", "", text.strip())
    m = None
    for m in _PRICE_RE.finditer(body):
        pass
    if m is None:
        name = _clean_name(body)
        return PriceRow(name=name, price="", duration_min=duration) if name and duration else None
    name = _clean_name(body[: m.start()] + body[m.end():] if not body[: m.start()].strip() else body[: m.start()])
    if not name:
        return None
    return PriceRow(name=name, price=norm_price(body[m.start(): m.end()]), duration_min=duration)


def parse_text(text: str) -> ParseResult:
    lines = [ln for ln in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n") if ln.strip()]
    if not lines:
        return ParseResult(rows=[])
    for sep in ("\t", "|", ";"):  # copied from a spreadsheet / a table: cells
        if sum(sep in ln for ln in lines) >= max(1, len(lines) * 0.6):
            return parse_table([[c.strip() for c in ln.strip().strip("|").split(sep)] for ln in lines])
    out = ParseResult(rows=[])
    for ln in lines[:MAX_CELLS_ROWS]:
        row = parse_line(ln)
        if row is None:
            out.skipped += 1
            continue
        if len(out.rows) >= MAX_ROWS:
            out.truncated = True
            break
        out.rows.append(row)
    return out


# --- files ---------------------------------------------------------------------------------------

def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp1251"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise PriceImportError("Не удалось прочитать файл: сохраните его в кодировке UTF-8.")


def parse_csv(data: bytes) -> ParseResult:
    text = _decode(data)
    sample = text[:4096]
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=";,\t|").delimiter
    except csv.Error:
        first = sample.split("\n", 1)[0]
        delimiter = ";" if ";" in first else "\t" if "\t" in first else ","
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows = []
    for i, r in enumerate(reader):
        if i >= MAX_CELLS_ROWS:
            break
        rows.append([c.strip() for c in r])
    return parse_table(rows)


def parse_xlsx(data: bytes) -> ParseResult:
    try:
        import openpyxl
    except ImportError as exc:  # pragma: no cover - dependency of core
        raise PriceImportError("Чтение XLSX недоступно на этом сервере: загрузите CSV или вставьте текст.") from exc
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 - zip / xml errors of a broken or foreign file
        raise PriceImportError("Не удалось открыть файл Excel. Сохраните его как .xlsx или .csv.") from exc
    result = ParseResult(rows=[])
    try:
        for ws in wb.worksheets:
            rows = []
            for i, r in enumerate(ws.iter_rows(values_only=True)):
                if i >= MAX_CELLS_ROWS:
                    break
                rows.append(list(r))
            part = parse_table(rows) if rows else ParseResult(rows=[])
            result.skipped += part.skipped
            for row in part.rows:
                if len(result.rows) >= MAX_ROWS:
                    result.truncated = True
                    break
                result.rows.append(row)
            result.truncated = result.truncated or part.truncated
    finally:
        wb.close()
    return result


def sniff_kind(filename: str, data: bytes) -> str:
    """'xlsx' | 'csv' | 'image' (with mime in image_mime); raises PriceImportError for unsupported files."""
    name = (filename or "").lower()
    if data.startswith(b"\xd0\xcf\x11\xe0") or name.endswith(".xls"):
        raise PriceImportError("Старый формат Excel (.xls) не поддерживается: сохраните файл как .xlsx или .csv.")
    if data.startswith(b"PK"):
        return "xlsx"
    if image_mime(data):
        return "image"
    if name.endswith((".heic", ".heif")):
        raise PriceImportError("Формат HEIC не поддерживается: сделайте снимок экрана или сохраните фото как JPG.")
    if name.endswith((".pdf", ".doc", ".docx")):
        raise PriceImportError("PDF и Word не поддерживаются: загрузите CSV, XLSX, фото прайса или вставьте текст.")
    return "csv"


def image_mime(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None

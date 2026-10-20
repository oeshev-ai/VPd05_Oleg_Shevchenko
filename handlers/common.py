"""Общие тексты, разбор сумм и перевод страны в код валюты."""

import re

from pydantic import ValidationError

from api_client import (
    MSG_INVALID_CURRENCY,
    MSG_SERVICE_UNAVAILABLE,
    ExchangeServiceError,
    get_exchange_rate,
)
from config import get_settings

NO_ACTIVE_TRIP = (
    "У вас пока нет активного путешествия. "
    "Создайте его через кнопку 'Новое путешествие'."
)
NOT_A_NUMBER = "Пожалуйста, введите корректное число (например, 150 или 150.50)."
NEGATIVE_BALANCE = "⚠️ Внимание! Эта операция уведёт баланс в минус. Вы уверены?"

_CODE = re.compile(r"^[A-Za-z]{3}$")
_COUNTRY_TO_CURRENCY: dict[str, str] = {}


def _norm(text: str) -> str:
    """Приводит название страны к ключу словаря."""
    value = text.strip().lower().replace("ё", "е").replace("-", " ")
    return " ".join(value.split())


def _register(code: str, *names: str) -> None:
    """Связывает названия страны с трёхбуквенным кодом валюты."""
    for name in names:
        _COUNTRY_TO_CURRENCY[_norm(name)] = code


_register("RUB", "россия", "рф", "российская федерация", "рубль")
_register("CNY", "китай", "кнр", "юань", "china")
_register("USD", "сша", "соединенные штаты", "америка", "usa", "usd")
_register("EUR", "евро", "германия", "франция", "италия", "испания", "португалия", "нидерланды", "бельгия", "австрия", "греция", "финляндия", "ирландия", "хорватия", "литва", "латвия", "эстония", "словакия", "словения", "черногория", "germany", "france", "italy", "spain")
_register("JPY", "япония", "йена", "иена", "japan")
_register("GBP", "великобритания", "англия", "британия", "фунт", "uk")
_register("THB", "таиланд", "тайланд", "бат", "thailand")
_register("TRY", "турция", "лира", "turkey")
_register("AED", "оаэ", "эмираты", "дубай", "uae")
_register("KZT", "казахстан", "тенге", "kazakhstan")
_register("GEL", "грузия", "лари", "georgia")
_register("AMD", "армения", "драм", "armenia")
_register("UZS", "узбекистан", "сум", "uzbekistan")
_register("KGS", "киргизия", "кыргызстан", "киргизстан", "сом")
_register("TJS", "таджикистан", "сомони")
_register("BYN", "беларусь", "белоруссия")
_register("UAH", "украина", "гривна")
_register("VND", "вьетнам", "донг", "vietnam")
_register("KRW", "корея", "южная корея", "вона")
_register("INR", "индия", "india")
_register("IDR", "индонезия")
_register("MYR", "малайзия")
_register("SGD", "сингапур")
_register("HKD", "гонконг")
_register("CHF", "швейцария")
_register("CZK", "чехия")
_register("PLN", "польша")
_register("HUF", "венгрия")
_register("EGP", "египет", "egypt")
_register("ILS", "израиль")
_register("CAD", "канада")
_register("AUD", "австралия")
_register("BRL", "бразилия")
_register("MXN", "мексика")
_register("ARS", "аргентина")
_register("ZAR", "юар", "южная африка")
_register("SEK", "швеция")
_register("NOK", "норвегия")
_register("DKK", "дания")
_register("RSD", "сербия")
_register("BGN", "болгария")
_register("RON", "румыния")
_register("MDL", "молдова", "молдавия")
_register("AZN", "азербайджан")
_register("QAR", "катар")
_register("SAR", "саудовская аравия")
_register("EGP", "египет")
_register("MVR", "мальдивы")
_register("LKR", "шри ланка")
_register("NPR", "непал")
_register("MNT", "монголия")
_register("KHR", "камбоджа")
_register("LAK", "лаос")
_register("MMK", "мьянма", "бирма")
_register("PHP", "филиппины")
_register("PKR", "пакистан")
_register("BDT", "бангладеш")
_register("CUP", "куба")
_register("COP", "колумбия")
_register("CLP", "чили")
_register("PEN", "перу")
_register("UYU", "уругвай")
_register("BOB", "боливия")
_register("NZD", "новая зеландия")
_register("ISK", "исландия")


def resolve_currency(text: str) -> str | None:
    """Возвращает код валюты по коду или названию страны.

    Неизвестный текст не подменяется: вызывающий код покажет ошибку курса.
    """
    raw = text.strip().rstrip(".")
    if _CODE.fullmatch(raw):
        return raw.upper()
    return _COUNTRY_TO_CURRENCY.get(_norm(raw))


def parse_positive_number(text: str) -> float | None:
    """Разбирает сумму вида 150, 150.50 или 150,50. Ноль и минус не подходят."""
    raw = text.strip().replace(" ", "").replace("\u00a0", "").replace(",", ".")
    if not re.fullmatch(r"\d+(\.\d+)?", raw):
        return None
    value = float(raw)
    if value <= 0 or value > 10_000_000_000:
        return None
    return value


def amount_token(value: float) -> str:
    """Короткая запись суммы для callback: 100 или 100.50."""
    quantized = round(value, 2)
    text = f"{quantized:.2f}"
    if text.endswith(".00"):
        return text[:-3]
    return text


def format_amount(value: float) -> str:
    """Деньги с пробелом между тысячами: 30720 → «30 720», 781.25 остаётся дробным."""
    rounded = round(float(value), 2)
    sign = "-" if rounded < 0 else ""
    rounded = abs(rounded)
    if abs(rounded - round(rounded)) < 1e-9:
        body = f"{int(round(rounded)):,}"
    else:
        body = f"{rounded:,.2f}"
    return sign + body.replace(",", " ")


def format_rate(value: float) -> str:
    """Курс без хвостовых нулей: 12.80 → «12.8»."""
    for digits in (2, 4, 6):
        rounded = round(value, digits)
        if abs(rounded) >= 10 ** (-digits):
            return f"{rounded:.{digits}f}".rstrip("0").rstrip(".")
    return f"{value:.6f}".rstrip("0").rstrip(".")


def format_balance_line(target_amount: float, target_code: str, home_amount: float, home_code: str) -> str:
    """Строка остатка в двух валютах."""
    return (
        f"Остаток: {format_amount(target_amount)} {target_code}"
        f" = {format_amount(home_amount)} {home_code}"
    )


def split_expense(amount_target: float, exchange_rate: float) -> tuple[float, float]:
    """Пара сумм расхода: валюта назначения и её эквивалент в домашней валюте."""
    target = round(amount_target, 2)
    home = round(target * exchange_rate, 2)
    return target, home


def opening_by_rate(home_amount: float, exchange_rate: float) -> tuple[float, float]:
    """Стартовые остатки по уже согласованному курсу «1 целевая = X домашней»."""
    home = round(home_amount, 2)
    target = round(home / exchange_rate, 2)
    return home, target


def overdraws(home_balance: float, target_balance: float, amount_home: float, amount_target: float) -> bool:
    """True, если после списания любой из остатков станет отрицательным."""
    return (
        round(target_balance - amount_target, 2) < 0
        or round(home_balance - amount_home, 2) < 0
    )


def manual_rate_prompt(home: str, target: str) -> str:
    """Просьба ввести курс в ту же сторону, в которой бот его показывает."""
    return (
        f"Введите курс числом: сколько {home} стоит 1 {target}. "
        "Например, 12.8."
    )


def rate_question(target: str, home: str, rate: float) -> str:
    """Текст подтверждения курса."""
    return (
        f"Текущий курс: 1 {target} = {format_rate(rate)} {home}. "
        "Подходит ли вам этот курс?"
    )


def read_api_key() -> str:
    """Ключ access_key из настроек. Ошибка валидации звучит как ошибка конфигурации."""
    try:
        return get_settings().api_key.get_secret_value()
    except ValidationError as exc:
        from api_client import InvalidAccessKeyError, MSG_INVALID_ACCESS_KEY

        raise InvalidAccessKeyError(MSG_INVALID_ACCESS_KEY) from exc


async def quote_home_per_target(home: str, target: str) -> float:
    """Сколько домашней валюты стоит 1 единица валюты назначения.

    Запрос к API идёт как в сценарии: from=домашняя, to=назначение, amount=1.
    Показанный курс — величина, обратная ответу.
    """
    if home == target:
        return 1.0
    conversion = await get_exchange_rate(home, target, 1, read_api_key())
    if conversion.result <= 0:
        raise ExchangeServiceError(MSG_SERVICE_UNAVAILABLE)
    return 1 / conversion.result


def currency_error_text() -> str:
    """Текст, если страну или код разобрать не удалось."""
    return MSG_INVALID_CURRENCY

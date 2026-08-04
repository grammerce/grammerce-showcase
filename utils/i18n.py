"""
Утилиты для мультиязычного контента.

Схема хранения:
  product.name_i18n = {"ru": "Молоко", "uz": "Sut", "en": "Milk"}
  product.description_i18n = {...}
  shop.available_languages = ["ru", "uz"]
  shop.default_language = "ru"
  bot_user.language = "uz"

Fallback-цепочка: запрошенный язык → default_language магазина → "ru"
"""

from typing import Any

SUPPORTED_LANGUAGES = ("ru", "uz", "en")


def get_text(
    i18n_dict: dict[str, str] | None,
    lang: str,
    fallback_lang: str = "ru",
    default: str = "",
) -> str:
    """
    Получить текст из i18n-словаря с fallback-цепочкой.

    Args:
        i18n_dict: {"ru": "...", "uz": "...", "en": "..."}
        lang: запрошенный язык
        fallback_lang: язык-запасной (обычно default_language магазина)
        default: значение, если ничего не найдено
    """
    if not i18n_dict:
        return default

    # 1) Запрошенный язык
    text = i18n_dict.get(lang)
    if text:
        return text

    # 2) Запасной язык магазина
    if fallback_lang and fallback_lang != lang:
        text = i18n_dict.get(fallback_lang)
        if text:
            return text

    # 3) Русский как последний резерв
    text = i18n_dict.get("ru")
    if text:
        return text

    # 4) Базовое значение поля (например, оригинальное name до i18n)
    if default:
        return default

    # 5) Любое непустое значение как крайний резерв
    for v in i18n_dict.values():
        if v:
            return v

    return ""


def localize_product(product_dict: dict[str, Any], lang: str, fallback: str = "ru") -> dict[str, Any]:
    """
    Добавить локализованные name/description в словарь товара.
    Исходные name_i18n/description_i18n остаются в ответе (для кабинета).
    """
    result = dict(product_dict)
    name_i18n = result.get("name_i18n") or {}
    desc_i18n = result.get("description_i18n") or {}

    if name_i18n:
        result["name"] = get_text(name_i18n, lang, fallback, result.get("name", ""))
    if desc_i18n:
        result["description"] = get_text(desc_i18n, lang, fallback, result.get("description") or "")

    return result


def localize_category(category_dict: dict[str, Any], lang: str, fallback: str = "ru") -> dict[str, Any]:
    """Аналогично localize_product, но для категорий."""
    result = dict(category_dict)
    name_i18n = result.get("name_i18n") or {}
    desc_i18n = result.get("description_i18n") or {}

    if name_i18n:
        result["name"] = get_text(name_i18n, lang, fallback, result.get("name", ""))
    if desc_i18n:
        result["description"] = get_text(desc_i18n, lang, fallback, result.get("description") or "")

    return result


def detect_language(
    requested: str | None,
    available: list,
    default: str = "ru",
) -> str:
    """
    Определить язык запроса с учётом доступных языков магазина.

    Args:
        requested: язык из параметра ?lang= или из bot_user.language
        available: shop.available_languages
        default: shop.default_language
    """
    if requested and requested in available:
        return requested
    if default in available:
        return default
    if available:
        return available[0]
    return "ru"

import json
import logging
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from integrations.accounting.base import AccountingProvider
from models import Order, OrderItem

log = logging.getLogger(__name__)

class AzmaLiveProvider(AccountingProvider):
    """
    Провайдер для реальной интеграции с API AZMA.
    """
    
    def __init__(self, base_url: str = "https://api.azma.uz/v1"):
        self.base_url = base_url.rstrip("/")
        
    async def _get_auth_headers(self, shop_id: int, session: AsyncSession) -> dict[str, str] | None:
        """
        Получает API ключи AZMA из настроек магазина.
        В будущем эти ключи должны храниться в ShopSettings.
        """
        # TODO: Реализовать реальное получение ключей из ShopSettings
        # svc = ShopSettingsService(session)
        # settings = await svc.get_settings(shop_id)
        # api_key = settings.get("azma_api_key")
        
        # Заглушка, пока ключей нет
        api_key = f"dummy_key_for_shop_{shop_id}"
        
        if not api_key:
            log.warning(f"[AZMA Live] В настройках магазина {shop_id} не найден AZMA API Key")
            return None
            
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

    async def sync_order(self, order: Order, items: list[OrderItem]) -> dict[str, Any]:
        """
        Отправка реального запроса к API AZMA для генерации фискального чека.
        Формирует JSON из позиций заказа (OrderItem).
        """
        log.info(f"[AZMA Live] Начинаем синхронизацию заказа #{order.id} (UUID: {order.order_uuid})")
        
        # Получаем сессию БД для извлечения настроек магазина (ключей)
        # В реальном приложении лучше передавать настройки или сессию
        # Здесь мы упростили для примера, предполагая, что нам передадут сессию или мы ее создадим
        from database import async_session_factory
        
        async with async_session_factory() as db_session:
            headers = await self._get_auth_headers(order.shop_id, db_session)
            if not headers:
                return {"ok": False, "error": "AZMA API key not configured"}

        # Формируем тело запроса согласно предполагаемой документации AZMA
        payload = {
            "external_id": str(order.order_uuid),
            "order_number": str(order.id),
            "total_amount": float(order.total_amount),
            "currency": "UZS",
            "customer": {
                "phone": order.meta.get("customer_phone", "") if order.meta else "",
                "name": order.meta.get("customer_name", "") if order.meta else ""
            },
            "items": []
        }
        
        for item in items:
            payload["items"].append({
                "product_id": item.product_id,
                "name": item.title,
                "quantity": item.qty,
                "price": float(item.price),
                "amount": float(item.price * item.qty)
            })
            
        # Учет скидки (если AZMA поддерживает глобальную скидку на чек, либо нужно размазать по товарам)
        if order.discount and order.discount > 0:
            payload["discount"] = float(order.discount)

        url = f"{self.base_url}/receipts/create"
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                log.debug(f"[AZMA Live] Запрос к {url}: {json.dumps(payload, ensure_ascii=False)}")
                
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                
                data = response.json()
                
                # Парсим ответ от AZMA (предполагаем наличие receipt_id и url)
                # Пример: {"success": true, "data": {"receipt_id": "123", "fiscal_url": "https://soliq.uz/..."}}
                if data.get("success"):
                    receipt_data = data.get("data", {})
                    external_id = str(receipt_data.get("receipt_id", ""))
                    fiscal_url = receipt_data.get("fiscal_url", "")
                    
                    log.info(f"[AZMA Live] Заказ #{order.id} успешно синхронизирован. URL: {fiscal_url}")
                    
                    return {
                        "ok": True,
                        "external_id": external_id,
                        "fiscal_url": fiscal_url
                    }
                else:
                    error_msg = data.get("error", "Unknown error from AZMA")
                    log.error(f"[AZMA Live] Ошибка API AZMA: {error_msg}")
                    return {"ok": False, "error": error_msg}
                    
        except httpx.HTTPStatusError as e:
            log.error(f"[AZMA Live] HTTP ошибка {e.response.status_code}: {e.response.text}")
            return {"ok": False, "error": f"HTTP {e.response.status_code}"}
        except httpx.RequestError as e:
            log.error(f"[AZMA Live] Сетевая ошибка: {e}")
            return {"ok": False, "error": str(e)}
        except Exception as e:
            log.error(f"[AZMA Live] Внутренняя ошибка: {e}")
            return {"ok": False, "error": "Internal server error"}

    async def send_report(self, shop_id: int, period: str) -> dict[str, Any]:
        """
        Отправка Z-отчета или сводной информации в AZMA.
        """
        log.info(f"[AZMA Live] Отправка сводного отчета для shop_id={shop_id}, период={period}")
        
        from database import async_session_factory
        async with async_session_factory() as db_session:
            headers = await self._get_auth_headers(shop_id, db_session)
            if not headers:
                return {"ok": False, "error": "AZMA API key not configured"}

        url = f"{self.base_url}/reports/send"
        payload = {"period": period}
        
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                
                data = response.json()
                if data.get("success"):
                    return {"ok": True, "message": "Очет успешно отправлен"}
                else:
                    return {"ok": False, "error": data.get("error", "API Error")}
                    
        except Exception as e:
            log.error(f"[AZMA Live] Сводный отчет, ошибка: {e}")
            return {"ok": False, "error": str(e)}

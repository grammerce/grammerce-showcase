"""
Адаптер МойСклад (Этап 4).
Реализация в integrations/moysklad_adapter.py.
"""
from __future__ import annotations

from .base import PosClientData, PosConnector, PosOrderResult, PosOrderStatus, PosProduct


class MoySkladAdapter(PosConnector):
    """
    МойСклад API — https://dev.moysklad.ru/doc/api/remap/1.2/
    Bearer-token авторизация.
    """

    BASE_URL = "https://api.moysklad.ru/api/remap/1.2"

    def __init__(self, api_token: str, organization_id: str | None = None):
        self.api_token = api_token
        self.organization_id = organization_id
        self._headers = {"Authorization": f"Bearer {api_token}"}

    async def test_connection(self) -> bool:
        import httpx
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.BASE_URL}/entity/organization",
                headers=self._headers,
                timeout=10,
            )
            return resp.status_code == 200

    async def sync_catalog(self) -> list[PosProduct]:
        """
        GET /entity/assortment?filter=type=product
        Пагинация: limit=1000, offset=0, 1000, ...
        """
        import httpx
        products: list[PosProduct] = []
        offset = 0
        limit = 1000

        async with httpx.AsyncClient() as client:
            while True:
                resp = await client.get(
                    f"{self.BASE_URL}/entity/assortment",
                    headers=self._headers,
                    params={"filter": "type=product", "limit": limit, "offset": offset},
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()
                rows = data.get("rows", [])
                if not rows:
                    break

                for item in rows:
                    # МойСклад хранит цены в копейках → делим на 100
                    sale_prices = item.get("salePrices", [])
                    price_kopeck = sale_prices[0]["value"] if sale_prices else 0
                    price_uzs = int(price_kopeck / 100)

                    cat_meta = item.get("productFolder", {}).get("name")

                    products.append(PosProduct(
                        external_id=item["id"],
                        name=item["name"],
                        price=price_uzs,
                        in_stock=not item.get("archived", False),
                        category_name=cat_meta,
                        description=item.get("description"),
                        sku=item.get("article"),
                    ))

                if len(rows) < limit:
                    break
                offset += limit

        return products

    async def push_order(
        self,
        order_id: int,
        items: list[dict],
        total_amount: int,
        discount_amount: int,
        customer_name: str | None,
        customer_phone: str | None,
        payment_method: str,
        delivery_type: str | None = None,
    ) -> PosOrderResult:
        """
        POST /entity/customerorder
        Находит или создаёт counterparty по номеру телефона.
        """
        import httpx

        async with httpx.AsyncClient() as client:
            # Получить организацию
            org_resp = await client.get(
                f"{self.BASE_URL}/entity/organization",
                headers=self._headers,
                timeout=10,
            )
            org_resp.raise_for_status()
            org_data = org_resp.json()
            org_meta = org_data["rows"][0]["meta"]

            # Найти или создать контрагента
            agent_meta = await self._get_or_create_counterparty(client, customer_name, customer_phone)

            # Маппинг позиций (только товары с external_id)
            positions = []
            for it in items:
                if not it.get("external_id"):
                    continue
                positions.append({
                    "quantity": it["quantity"],
                    "price": it["price"] * 100,  # UZS → копейки
                    "assortment": {
                        "meta": {
                            "href": f"{self.BASE_URL}/entity/product/{it['external_id']}",
                            "type": "product",
                            "mediaType": "application/json",
                        }
                    },
                })

            payload = {
                "organization": {"meta": org_meta},
                "agent": {"meta": agent_meta},
                "description": f"Telegram заказ #{order_id}",
                "positions": positions,
            }

            resp = await client.post(
                f"{self.BASE_URL}/entity/customerorder",
                headers={**self._headers, "Content-Type": "application/json"},
                json=payload,
                timeout=15,
            )
            if resp.status_code in (200, 201):
                return PosOrderResult(success=True, external_order_id=resp.json()["id"])
            return PosOrderResult(success=False, error=f"HTTP {resp.status_code}: {resp.text[:200]}")

    async def _get_or_create_counterparty(self, client, name: str | None, phone: str | None) -> dict:
        """Найти или создать контрагента в МойСклад."""

        if phone:
            resp = await client.get(
                f"{self.BASE_URL}/entity/counterparty",
                headers=self._headers,
                params={"filter": f"phone={phone}"},
                timeout=10,
            )
            if resp.status_code == 200:
                rows = resp.json().get("rows", [])
                if rows:
                    return rows[0]["meta"]

        # Создаём нового
        payload: dict = {"name": name or "Telegram покупатель"}
        if phone:
            payload["phone"] = phone

        resp = await client.post(
            f"{self.BASE_URL}/entity/counterparty",
            headers={**self._headers, "Content-Type": "application/json"},
            json=payload,
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()["meta"]

    async def get_order_status(self, external_order_id: str) -> PosOrderStatus:
        import httpx

        STATUS_MAP = {
            "New": "new",
            "InProcess": "in_progress",
            "Assembled": "ready",
            "Delivered": "completed",
            "Closed": "completed",
            "Cancelled": "cancelled",
        }

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.BASE_URL}/entity/customerorder/{external_order_id}",
                headers=self._headers,
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            state_name = data.get("state", {}).get("name", "Unknown")
            return PosOrderStatus(
                external_order_id=external_order_id,
                status=STATUS_MAP.get(state_name, "new"),
                original_status=state_name,
            )

    async def get_client_data(
        self,
        phone: str | None = None,
        external_id: str | None = None,
    ) -> PosClientData | None:
        import httpx

        async with httpx.AsyncClient() as client:
            if phone:
                resp = await client.get(
                    f"{self.BASE_URL}/entity/counterparty",
                    headers=self._headers,
                    params={"filter": f"phone={phone}"},
                    timeout=10,
                )
                if resp.status_code != 200:
                    return None
                rows = resp.json().get("rows", [])
                if not rows:
                    return None
                cp = rows[0]
                cp_id = cp["id"]
            elif external_id:
                cp_id = external_id
            else:
                return None

            # Получить заказы контрагента
            orders_resp = await client.get(
                f"{self.BASE_URL}/entity/customerorder",
                headers=self._headers,
                params={"filter": f"agent=https://api.moysklad.ru/api/remap/1.2/entity/counterparty/{cp_id}"},
                timeout=15,
            )
            if orders_resp.status_code != 200:
                return None

            orders = orders_resp.json().get("rows", [])
            orders_count = len(orders)
            ltv = sum(int(o.get("sum", 0) / 100) for o in orders)
            avg_check = int(ltv / orders_count) if orders_count > 0 else 0
            last_date = orders[0].get("updated") if orders else None

            return PosClientData(
                external_id=cp_id,
                orders_count=orders_count,
                ltv=ltv,
                avg_check=avg_check,
                last_order_date=last_date,
            )

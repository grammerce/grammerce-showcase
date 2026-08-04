"""
InvoiceService — генерация PDF-счётов на оплату и управление жизненным циклом счёта.

Содержит:
- create_invoice()     — создать запись Invoice в БД + PDF
- generate_pdf()       — сгенерировать PDF с реквизитами (reportlab + кириллица)
- mark_as_paid()       — отметить счёт оплаченным, продлить подписку
- mark_as_overdue()    — отметить просроченным
- get_next_invoice_num()  — последовательная нумерация INV-ГГГГ-XXXXXX
"""
from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from config.billing import (
    INVOICE_DUE_DAYS,
    INVOICES_DIR,
    VAT_RATE,
    get_seller_details,
)
from models import Invoice, Plan, Shop, Subscription, User

log = logging.getLogger(__name__)

# ─── Шрифты для reportlab (кириллица) ────────────────────────────────────────
_FONT_CANDIDATES = [
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
     "DejaVuSans", "DejaVuSans-Bold"),
    ("C:/Windows/Fonts/arial.ttf",
     "C:/Windows/Fonts/arialbd.ttf",
     "Arial", "Arial-Bold"),
]
_FONT_NAME = "Helvetica"
_FONT_BOLD = "Helvetica-Bold"
_FONTS_REGISTERED = False


def _register_fonts() -> None:
    global _FONTS_REGISTERED, _FONT_NAME, _FONT_BOLD
    if _FONTS_REGISTERED:
        return
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        for reg_path, bold_path, name, bold_name in _FONT_CANDIDATES:
            if os.path.exists(reg_path) and os.path.exists(bold_path):
                pdfmetrics.registerFont(TTFont(name, reg_path))
                pdfmetrics.registerFont(TTFont(bold_name, bold_path))
                _FONT_NAME = name
                _FONT_BOLD = bold_name
                log.info("PDF fonts registered: %s", name)
                break
        else:
            log.warning("No Cyrillic fonts found, using Helvetica")
    except Exception as e:
        log.warning("Font registration error: %s", e)
    finally:
        _FONTS_REGISTERED = True


# ─── Утилиты ──────────────────────────────────────────────────────────────────

def _fmt_uzs(amount: int) -> str:
    """1234567 → '1 234 567 сум'"""
    return f"{amount:,}".replace(",", " ") + " сум"


def _now() -> datetime:
    return datetime.now(UTC)


# ─── InvoiceService ───────────────────────────────────────────────────────────

class InvoiceService:

    def __init__(self, db: Session):
        self.db = db

    # ── Нумерация ─────────────────────────────────────────────────────────────

    def get_next_invoice_num(self) -> str:
        """Генерирует следующий номер: INV-2026-000042"""
        year = datetime.now(UTC).year
        # Ищем максимальный номер за этот год, чтобы избежать коллизий при удалении счетов
        max_num = self.db.execute(
            select(func.max(Invoice.invoice_number)).where(
                Invoice.invoice_number.like(f"INV-{year}-%")
            )
        ).scalar()
        
        if max_num:
            try:
                last_seq = int(max_num.split("-")[-1])
            except ValueError:
                last_seq = 0
            return f"INV-{year}-{last_seq + 1:06d}"
        
        return f"INV-{year}-000001"

    # ── Создание счёта ────────────────────────────────────────────────────────

    def create_invoice(
        self,
        subscription_id: int,
        *,
        notify: bool = False,
    ) -> Invoice:
        """
        1. Загружает subscription + shop + plan
        2. Генерирует invoice_number
        3. Рассчитывает суммы (amount + НДС 12%)
        4. Копирует реквизиты
        5. Генерирует PDF
        6. Сохраняет Invoice
        """
        sub = self.db.get(Subscription, subscription_id)
        if not sub:
            raise ValueError(f"Subscription {subscription_id} not found")

        shop = self.db.get(Shop, sub.shop_id)
        plan = self.db.get(Plan, sub.plan_id)

        if not shop or not plan:
            raise ValueError("Shop or Plan not found")

        # Рассчитываем суммы
        amount = (
            plan.price_monthly
            if sub.billing_period == "monthly"
            else plan.price_yearly
        )
        vat_amount  = int(amount * VAT_RATE)
        total_amount = amount + vat_amount

        # Период следующий от current_period_end
        period_start = sub.current_period_end or _now()
        if sub.billing_period == "monthly":
            period_end = period_start + timedelta(days=30)
        else:
            period_end = period_start + timedelta(days=365)

        # Описание
        period_label = period_start.strftime("%B %Y") if period_start else ""
        description = f"Подписка {plan.name} — {period_label}"

        # Реквизиты
        seller = get_seller_details()
        buyer  = _get_buyer_details(shop)

        invoice_number = self.get_next_invoice_num()
        now = _now()
        due_date = now + timedelta(days=INVOICE_DUE_DAYS)

        invoice = Invoice(
            subscription_id=subscription_id,
            shop_id=shop.id,
            invoice_number=invoice_number,
            invoice_type="subscription",
            amount=amount,
            vat_amount=vat_amount,
            total_amount=total_amount,
            description=description,
            period_start=period_start,
            period_end=period_end,
            seller_details=seller,
            buyer_details=buyer,
            status="draft",
            issued_at=now,
            due_date=due_date,
        )
        self.db.add(invoice)
        self.db.flush()   # получаем invoice.id

        # Генерируем PDF
        try:
            pdf_path = self.generate_pdf(invoice)
            invoice.pdf_path = pdf_path
        except Exception as e:
            log.error("PDF generation failed for invoice %s: %s", invoice_number, e)

        self.db.commit()
        self.db.refresh(invoice)
        log.info("Invoice created: %s (shop=%s)", invoice_number, shop.id)
        return invoice

    # ── Создание счёта за Setup Fee ─────────────────────────────────────────────

    def create_setup_fee_invoice(self, subscription_id: int, discount_amount: int = 0) -> Invoice:
        """
        Создаёт счёт на разовый взнос за подключение (Setup Fee).
        amount берётся из plan.setup_fee_amount (хранится в БД).
        discount_amount (UZS) — скидка по реферальной ссылке: вычитается из базы,
        НДС и итог считаются от нетто-суммы (PDF остаётся консистентным).
        """
        sub = self.db.get(Subscription, subscription_id)
        if not sub:
            raise ValueError(f"Subscription {subscription_id} not found")

        shop = self.db.get(Shop, sub.shop_id)
        plan = self.db.get(Plan, sub.plan_id)

        if not shop or not plan:
            raise ValueError("Shop or Plan not found")

        base = plan.setup_fee_amount
        discount = max(0, min(int(discount_amount or 0), base))
        amount = base - discount
        vat_amount = int(amount * VAT_RATE)
        total_amount = amount + vat_amount

        seller = get_seller_details()
        buyer = _get_buyer_details(shop)

        invoice_number = self.get_next_invoice_num()
        now = _now()
        due_date = now + timedelta(days=INVOICE_DUE_DAYS)

        invoice = Invoice(
            subscription_id=subscription_id,
            shop_id=shop.id,
            invoice_number=invoice_number,
            invoice_type="setup_fee",
            amount=amount,
            discount_amount=discount,
            vat_amount=vat_amount,
            total_amount=total_amount,
            description=f"Разовый взнос за подключение — тариф {plan.name}",
            period_start=None,
            period_end=None,
            seller_details=seller,
            buyer_details=buyer,
            status="draft",
            issued_at=now,
            due_date=due_date,
        )
        self.db.add(invoice)
        self.db.flush()

        try:
            pdf_path = self.generate_pdf(invoice)
            invoice.pdf_path = pdf_path
        except Exception as e:
            log.error("PDF generation failed for setup_fee invoice %s: %s", invoice_number, e)

        self.db.commit()
        self.db.refresh(invoice)
        log.info("Setup fee invoice created: %s (shop=%s)", invoice_number, shop.id)
        return invoice

    # ── PDF-генерация ─────────────────────────────────────────────────────────

    def generate_pdf(self, invoice: Invoice) -> str:
        """
        Генерирует PDF счёт на оплату.
        Возвращает путь к файлу (str).
        """
        _register_fonts()

        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER, TA_RIGHT
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )

        # Директория и путь
        out_dir = Path(INVOICES_DIR)
        out_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = str(out_dir / f"{invoice.invoice_number}.pdf")

        doc = SimpleDocTemplate(
            pdf_path,
            pagesize=A4,
            rightMargin=20 * mm,
            leftMargin=20 * mm,
            topMargin=15 * mm,
            bottomMargin=15 * mm,
        )

        # Стили
        getSampleStyleSheet()
        font    = _FONT_NAME
        font_b  = _FONT_BOLD

        title_style = ParagraphStyle(
            "title", fontName=font_b, fontSize=14,
            alignment=TA_CENTER, spaceAfter=4 * mm,
        )
        subtitle_style = ParagraphStyle(
            "subtitle", fontName=font_b, fontSize=11,
            alignment=TA_CENTER, spaceAfter=2 * mm,
        )
        normal = ParagraphStyle(
            "normal_cyr", fontName=font, fontSize=9, leading=13,
        )
        ParagraphStyle(
            "right_cyr", fontName=font, fontSize=9, alignment=TA_RIGHT,
        )

        seller = invoice.seller_details or {}
        buyer  = invoice.buyer_details  or {}
        issued_str  = (invoice.issued_at.strftime("%d.%m.%Y")
                       if invoice.issued_at else "—")
        due_str     = (invoice.due_date.strftime("%d.%m.%Y")
                       if invoice.due_date else "—")
        p_start_str = (invoice.period_start.strftime("%d.%m.%Y")
                       if invoice.period_start else "—")
        p_end_str   = (invoice.period_end.strftime("%d.%m.%Y")
                       if invoice.period_end else "—")

        story = []

        # ── Заголовок ─────────────────────────────────────────────────────────
        story.append(Paragraph("СЧЁТ НА ОПЛАТУ", title_style))
        story.append(Paragraph(
            f"№ {invoice.invoice_number} от {issued_str}", subtitle_style
        ))
        story.append(Spacer(1, 6 * mm))

        # ── Реквизиты продавца и покупателя ──────────────────────────────────
        def _party_block(label: str, d: dict) -> list:
            lines = [
                f"<b>{label}:</b>",
                d.get("company_name", "—"),
                f"ИНН: {d.get('inn', '—')}",
                f"Р/с: {d.get('account', '—')}",
                f"Банк: {d.get('bank_name', '—')}, МФО: {d.get('bank_mfo', '—')}",
                f"Адрес: {d.get('address', '—')}",
            ]
            if d.get("director"):
                lines.append(f"Директор: {d['director']}")
            return [Paragraph(line, normal) for line in lines]

        parties_data = [[
            _party_block("Поставщик", seller),
            _party_block("Покупатель", buyer),
        ]]
        parties_table = Table(parties_data, colWidths=[85 * mm, 85 * mm])
        parties_table.setStyle(TableStyle([
            ("VALIGN",    (0, 0), (-1, -1), "TOP"),
            ("BOX",       (0, 0), (0, 0), 0.5, colors.grey),
            ("BOX",       (1, 0), (1, 0), 0.5, colors.grey),
            ("PADDING",   (0, 0), (-1, -1), 4),
        ]))
        story.append(parties_table)
        story.append(Spacer(1, 6 * mm))

        # ── Таблица позиций ───────────────────────────────────────────────────
        header = ["#", "Наименование", "Кол", "Цена, сум", "Сумма, сум"]
        row = [
            "1",
            f"{invoice.description or 'Услуга'}\n{p_start_str} – {p_end_str}",
            "1",
            f"{invoice.amount:,}".replace(",", " "),
            f"{invoice.amount:,}".replace(",", " "),
        ]
        items_data = [header, row]
        items_table = Table(
            items_data,
            colWidths=[8 * mm, 95 * mm, 10 * mm, 28 * mm, 29 * mm],
        )
        items_table.setStyle(TableStyle([
            ("BACKGROUND",  (0, 0), (-1, 0), colors.lightgrey),
            ("FONTNAME",    (0, 0), (-1, 0), font_b),
            ("FONTNAME",    (0, 1), (-1, -1), font),
            ("FONTSIZE",    (0, 0), (-1, -1), 9),
            ("GRID",        (0, 0), (-1, -1), 0.5, colors.grey),
            ("ALIGN",       (2, 0), (-1, -1), "CENTER"),
            ("ALIGN",       (3, 0), (-1, -1), "RIGHT"),
            ("ALIGN",       (4, 0), (-1, -1), "RIGHT"),
            ("VALIGN",      (0, 0), (-1, -1), "MIDDLE"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.Color(0.96, 0.96, 0.96)]),
            ("PADDING",     (0, 0), (-1, -1), 4),
        ]))
        story.append(items_table)
        story.append(Spacer(1, 4 * mm))

        # ── Итоги ─────────────────────────────────────────────────────────────
        vat_pct = int(VAT_RATE * 100)
        totals_data = []
        if VAT_RATE > 0:
            totals_data.append(["", "Итого без НДС:", _fmt_uzs(invoice.amount)])
            totals_data.append(["", f"НДС ({vat_pct}%):", _fmt_uzs(invoice.vat_amount)])
        totals_data.append(["", "ИТОГО К ОПЛАТЕ:", _fmt_uzs(invoice.total_amount)])
        totals_table = Table(totals_data, colWidths=[80 * mm, 45 * mm, 45 * mm])
        totals_style = [
            ("FONTNAME",  (0, -1), (-1, -1), font_b),
            ("FONTSIZE",  (0, 0),  (-1, -1), 9),
            ("ALIGN",     (1, 0),  (-1, -1), "RIGHT"),
            ("TOPPADDING", (0, -1), (-1, -1), 4),
            ("LINEABOVE", (1, -1), (-1, -1), 0.5, colors.grey),
        ]
        if len(totals_data) > 1:
            totals_style.insert(0, ("FONTNAME", (0, 0), (-1, -2), font))
        totals_table.setStyle(TableStyle(totals_style))
        story.append(totals_table)
        story.append(Spacer(1, 6 * mm))

        # ── Срок оплаты ───────────────────────────────────────────────────────
        story.append(Paragraph(f"Срок оплаты: до <b>{due_str}</b>", normal))
        story.append(Spacer(1, 8 * mm))

        # ── Подпись ───────────────────────────────────────────────────────────
        director = seller.get("director", "_______________")
        story.append(Paragraph(
            f"Директор _____________ / {director} /", normal
        ))

        doc.build(story)
        log.info("PDF generated: %s", pdf_path)
        return pdf_path

    # ── Отметить оплаченным ───────────────────────────────────────────────────

    def mark_as_paid(
        self,
        invoice_id: int,
        paid_at: datetime | None = None,
    ) -> Invoice:
        """
        1. invoice.status = 'paid'
        2. subscription.status = 'active', продлевает период
        3. Уведомление владельцу (логируется)
        """
        invoice = self.db.get(Invoice, invoice_id)
        if not invoice:
            raise ValueError(f"Invoice {invoice_id} not found")

        paid_ts = paid_at or _now()
        invoice.status   = "paid"
        invoice.paid_at  = paid_ts

        sub = self.db.get(Subscription, invoice.subscription_id)
        if sub:
            sub.status                = "active"
            sub.last_paid_at          = paid_ts
            sub.grace_started_at      = None
            sub.suspended_at          = None
            sub.last_invoice_id       = invoice.id
            sub.current_period_start  = invoice.period_start or _now()
            sub.current_period_end    = invoice.period_end or (
                _now() + timedelta(days=30)
            )

        # Если это Setup Fee — отметить user.setup_fee_paid
        if getattr(invoice, "invoice_type", None) == "setup_fee" and sub:
            shop = self.db.get(Shop, sub.shop_id)
            if shop:
                user = self.db.query(User).filter_by(id=shop.owner_id).first()
                if user:
                    user.setup_fee_paid = True
                    user.setup_fee_paid_at = paid_ts
                    log.info("Setup fee paid for user %s (shop %s)", user.id, shop.id)

        self.db.commit()
        self.db.refresh(invoice)

        log.info(
            "Invoice %s marked as paid. Subscription %s → active until %s",
            invoice.invoice_number,
            sub.id if sub else "?",
            sub.current_period_end if sub else "?",
        )

        # Автоотправка ЭСФ через Didox (fire-and-forget в фоне)
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(_send_esf_for_invoice(invoice.id, invoice.shop_id))
        except RuntimeError:
            log.warning("No event loop for ESF auto-send, invoice %s", invoice.invoice_number)

        return invoice

    # ── Отметить просроченным ────────────────────────────────────────────────

    def mark_as_overdue(self, invoice_id: int) -> Invoice:
        """invoice.status = 'overdue'"""
        invoice = self.db.get(Invoice, invoice_id)
        if not invoice:
            raise ValueError(f"Invoice {invoice_id} not found")
        invoice.status = "overdue"
        self.db.commit()
        self.db.refresh(invoice)
        return invoice

    # ── Отметить отправленным ─────────────────────────────────────────────────

    def mark_as_sent(self, invoice_id: int) -> Invoice:
        invoice = self.db.get(Invoice, invoice_id)
        if not invoice:
            raise ValueError(f"Invoice {invoice_id} not found")
        invoice.status  = "sent"
        invoice.sent_at = _now()
        self.db.commit()
        self.db.refresh(invoice)
        return invoice


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _get_buyer_details(shop: Shop) -> dict:
    """Формирует dict реквизитов покупателя из полей Shop."""
    return {
        "company_name": shop.company_name or shop.name,
        "inn":          shop.company_inn          or "",
        "account":      shop.company_account      or "",
        "bank_name":    shop.company_bank_name    or "",
        "bank_mfo":     shop.company_bank_mfo     or "",
        "director":     shop.company_director     or "",
        "address":      shop.company_address      or "",
    }


# ─── Автоотправка ЭСФ ───────────────────────────────────────────────────────

async def _send_esf_for_invoice(invoice_id: int, shop_id: int) -> None:
    """
    Фоновая задача: отправить ЭСФ через DidoxService после оплаты.
    Вызывается как fire-and-forget task из mark_as_paid.
    """
    from database import async_session_factory
    from services.didox_service import DidoxError, DidoxService

    try:
        async with async_session_factory() as session:
            invoice = await session.get(Invoice, invoice_id)
            shop = await session.get(Shop, shop_id)

            if not invoice or not shop:
                log.error("ESF: invoice %s or shop %s not found", invoice_id, shop_id)
                return

            if invoice.esf_sent:
                log.info("ESF: already sent for invoice %s", invoice.invoice_number)
                return

            # Проверяем что у покупателя есть ИНН
            buyer_inn = getattr(shop, "company_inn", "") or ""
            if not buyer_inn:
                log.warning("ESF: no buyer INN for shop %s, skipping", shop_id)
                return

            svc = DidoxService()
            seller_data = svc.build_seller_data()
            buyer_data = svc.build_buyer_data(shop)
            items = svc.build_invoice_items(invoice)
            contract_info = svc.build_contract_info(
                user_id=shop.owner_id if hasattr(shop, "owner_id") else shop_id,
            )

            doc_id = await svc.create_and_send_invoice(
                seller_data, buyer_data, items, contract_info,
            )

            invoice.didox_document_id = doc_id
            invoice.esf_sent = True
            invoice.esf_sent_at = _now()
            await session.commit()

            log.info("ESF sent: invoice=%s, didox_doc=%s", invoice.invoice_number, doc_id)

    except DidoxError as e:
        log.error("ESF failed for invoice %s: %s", invoice_id, e)
        # esf_sent остаётся False — cron подберёт
    except Exception as e:
        log.error("ESF unexpected error for invoice %s: %s", invoice_id, e)

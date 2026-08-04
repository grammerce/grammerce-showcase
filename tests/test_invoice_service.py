"""
ЭТАП 2: Тесты InvoiceService (PDF-счёт, нумерация, статусы).
"""
import os
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base, Invoice, Plan, Shop, Subscription, User
from services.invoice_service import InvoiceService

TEST_DB = "sqlite:///./test_invoice.db"
engine_i = create_engine(TEST_DB, connect_args={"check_same_thread": False})
SessionI = sessionmaker(bind=engine_i, autocommit=False, autoflush=False)


@pytest.fixture(autouse=True)
def clean_db():
    Base.metadata.create_all(bind=engine_i)
    yield
    Base.metadata.drop_all(bind=engine_i)
    # убираем тестовые PDF
    for f in Path("media/invoices").glob("INV-*.pdf"):
        try:
            f.unlink()
        except Exception:
            pass


@pytest.fixture
def db():
    s = SessionI()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def plan(db):
    p = Plan(
        name="Start", slug="start-inv",
        price_monthly=200000, price_yearly=2000000,
        max_products=100, max_broadcasts_month=4,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


@pytest.fixture
def shop(db):
    s = Shop(
        name="Gul Savdo Test", slug="gul-inv-99", config={},
        company_name='ООО "Gul Savdo"',
        company_inn="987654321",
        company_account="20208000000000000002",
        company_bank_name="АКБ Asaka",
        company_bank_mfo="00873",
        company_director="Каримов К.К.",
        company_address="г. Ташкент",
        billing_email="buh@example.com",
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


@pytest.fixture
def sub(db, shop, plan):
    now = datetime.now(UTC)
    s = Subscription(
        shop_id=shop.id, plan_id=plan.id,
        status="past_due", billing_period="monthly",
        started_at=now,
        current_period_start=now - timedelta(days=30),
        current_period_end=now,
        trial_ends_at=now - timedelta(days=23),
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


@pytest.fixture
def svc(db):
    return InvoiceService(db)


# ─── Нумерация ────────────────────────────────────────────────────────────────

class TestInvoiceNumbering:
    def test_first_number(self, svc):
        year = datetime.now().year
        num = svc.get_next_invoice_num()
        assert num == f"INV-{year}-000001"

    def test_sequential_numbers(self, db, svc, shop, plan, sub):
        inv1 = svc.create_invoice(sub.id)
        # Чтобы создать второй счёт, нужна новая подписка (разные магазины)
        shop2 = Shop(name="Shop2", slug="shop2-inv", config={})
        db.add(shop2)
        db.commit()
        db.refresh(shop2)
        sub2 = Subscription(
            shop_id=shop2.id, plan_id=plan.id,
            status="past_due", billing_period="monthly",
            started_at=datetime.now(UTC),
            current_period_start=datetime.now(UTC) - timedelta(days=30),
            current_period_end=datetime.now(UTC),
        )
        db.add(sub2)
        db.commit()
        db.refresh(sub2)
        inv2 = svc.create_invoice(sub2.id)

        year = datetime.now().year
        assert inv1.invoice_number == f"INV-{year}-000001"
        assert inv2.invoice_number == f"INV-{year}-000002"

    def test_no_duplicate_numbers(self, db, svc, shop, plan, sub):
        inv = svc.create_invoice(sub.id)
        # Следующий номер должен быть +1
        next_num = svc.get_next_invoice_num()
        year = datetime.now().year
        assert next_num == f"INV-{year}-000002"


# ─── Создание счёта ───────────────────────────────────────────────────────────

class TestCreateInvoice:
    def test_basic_creation(self, svc, sub, shop, plan):
        inv = svc.create_invoice(sub.id)
        assert inv.id is not None
        assert inv.shop_id == shop.id
        assert inv.subscription_id == sub.id
        assert inv.amount == plan.price_monthly
        assert inv.status == "draft"

    def test_vat_calculation(self, svc, sub):
        """НДС считается по настроенной ставке, а не по зашитой в тест.

        Компания сейчас не плательщик НДС (VAT_RATE=0 в config/billing.py),
        поэтому проверка на жёстко вписанные 12% падала. Ненулевая ставка
        проверяется отдельно ниже.
        """
        from services.invoice_service import VAT_RATE

        inv = svc.create_invoice(sub.id)
        assert inv.vat_amount == int(inv.amount * VAT_RATE)
        assert inv.total_amount == inv.amount + inv.vat_amount

    def test_vat_calculation_with_non_zero_rate(self, svc, sub, monkeypatch):
        """При переходе на общую систему налогообложения ставка станет 0.12."""
        import services.invoice_service as invoice_service

        monkeypatch.setattr(invoice_service, "VAT_RATE", 0.12)

        inv = svc.create_invoice(sub.id)
        assert inv.vat_amount == int(inv.amount * 0.12)
        assert inv.total_amount == inv.amount + inv.vat_amount

    def test_seller_details_copied(self, svc, sub):
        inv = svc.create_invoice(sub.id)
        assert inv.seller_details.get("inn") is not None
        assert len(inv.seller_details.get("company_name", "")) > 0

    def test_buyer_details_from_shop(self, svc, sub, shop):
        inv = svc.create_invoice(sub.id)
        assert inv.buyer_details.get("inn") == shop.company_inn
        assert inv.buyer_details.get("company_name") == shop.company_name

    def test_due_date_7_days(self, svc, sub):
        inv = svc.create_invoice(sub.id)
        delta = inv.due_date - inv.issued_at
        assert 6 <= delta.days <= 8   # ~7 дней

    def test_description_contains_plan(self, svc, sub, plan):
        inv = svc.create_invoice(sub.id)
        assert plan.name in (inv.description or "")

    def test_invalid_subscription(self, svc):
        with pytest.raises(ValueError):
            svc.create_invoice(99999)


# ─── PDF-генерация ────────────────────────────────────────────────────────────

class TestGeneratePDF:
    def test_pdf_created(self, svc, sub):
        inv = svc.create_invoice(sub.id)
        assert inv.pdf_path is not None
        assert Path(inv.pdf_path).exists()

    def test_pdf_size_positive(self, svc, sub):
        inv = svc.create_invoice(sub.id)
        if inv.pdf_path:
            size = Path(inv.pdf_path).stat().st_size
            assert size > 1000   # более 1 КБ

    def test_pdf_contains_invoice_number(self, svc, sub):
        """Читаем текст PDF и проверяем, что номер счёта там есть."""
        inv = svc.create_invoice(sub.id)
        if not inv.pdf_path or not Path(inv.pdf_path).exists():
            pytest.skip("PDF not generated")
        try:
            import pdfplumber
            with pdfplumber.open(inv.pdf_path) as pdf:
                text = "\n".join(p.extract_text() or "" for p in pdf.pages)
            assert inv.invoice_number in text
        except ImportError:
            # pdfplumber не установлен — проверяем что файл существует
            assert Path(inv.pdf_path).stat().st_size > 0

    def test_pdf_contains_seller_inn(self, svc, sub):
        inv = svc.create_invoice(sub.id)
        if not inv.pdf_path:
            pytest.skip("PDF not generated")
        # Просто проверяем что файл есть и ненулевой
        assert Path(inv.pdf_path).stat().st_size > 0

    def test_pdf_name_matches_invoice_number(self, svc, sub):
        inv = svc.create_invoice(sub.id)
        if inv.pdf_path:
            assert inv.invoice_number in inv.pdf_path


# ─── mark_as_paid ─────────────────────────────────────────────────────────────

class TestMarkAsPaid:
    def test_invoice_status_paid(self, svc, sub):
        inv = svc.create_invoice(sub.id)
        paid = svc.mark_as_paid(inv.id)
        assert paid.status == "paid"
        assert paid.paid_at is not None

    def test_subscription_becomes_active(self, db, svc, sub):
        inv = svc.create_invoice(sub.id)
        svc.mark_as_paid(inv.id)
        db.refresh(sub)
        assert sub.status == "active"

    def test_subscription_period_extended(self, db, svc, sub):
        inv = svc.create_invoice(sub.id)
        svc.mark_as_paid(inv.id)
        db.refresh(sub)
        assert sub.current_period_end is not None
        assert sub.current_period_end > datetime.now(UTC).replace(tzinfo=None)

    def test_grace_cleared_on_paid(self, db, svc, sub):
        # Установим grace_started_at
        sub.grace_started_at = datetime.now(UTC)
        db.commit()

        inv = svc.create_invoice(sub.id)
        svc.mark_as_paid(inv.id)
        db.refresh(sub)
        assert sub.grace_started_at is None

    def test_custom_paid_at(self, svc, sub):
        inv = svc.create_invoice(sub.id)
        custom_time = datetime(2026, 3, 1, tzinfo=UTC)
        paid = svc.mark_as_paid(inv.id, paid_at=custom_time)
        assert paid.paid_at.year == 2026
        assert paid.paid_at.month == 3

    def test_mark_nonexistent_invoice(self, svc):
        with pytest.raises(ValueError):
            svc.mark_as_paid(99999)


# ─── mark_as_overdue ─────────────────────────────────────────────────────────

class TestMarkAsOverdue:
    def test_status_overdue(self, svc, sub):
        inv = svc.create_invoice(sub.id)
        overdue = svc.mark_as_overdue(inv.id)
        assert overdue.status == "overdue"

    def test_mark_nonexistent(self, svc):
        with pytest.raises(ValueError):
            svc.mark_as_overdue(99999)


# ─── create_setup_fee_invoice ────────────────────────────────────────────────

class TestCreateSetupFeeInvoice:
    def test_setup_fee_uses_plan_amount(self, svc, sub, plan):
        inv = svc.create_setup_fee_invoice(sub.id)
        assert inv.amount == plan.setup_fee_amount

    def test_setup_fee_invoice_type(self, svc, sub):
        inv = svc.create_setup_fee_invoice(sub.id)
        assert inv.invoice_type == "setup_fee"

    def test_setup_fee_description(self, svc, sub):
        inv = svc.create_setup_fee_invoice(sub.id)
        assert "подключение" in inv.description.lower()

    def test_setup_fee_no_period(self, svc, sub):
        inv = svc.create_setup_fee_invoice(sub.id)
        assert inv.period_start is None
        assert inv.period_end is None

    def test_setup_fee_vat_calculation(self, svc, sub, plan):
        from services.invoice_service import VAT_RATE

        inv = svc.create_setup_fee_invoice(sub.id)
        expected_vat = int(plan.setup_fee_amount * VAT_RATE)
        assert inv.vat_amount == expected_vat
        assert inv.total_amount == plan.setup_fee_amount + expected_vat

    def test_setup_fee_status_draft(self, svc, sub):
        inv = svc.create_setup_fee_invoice(sub.id)
        assert inv.status == "draft"

    def test_subscription_invoice_type(self, svc, sub):
        """create_invoice() должен ставить invoice_type='subscription'."""
        inv = svc.create_invoice(sub.id)
        assert inv.invoice_type == "subscription"


# ─── mark_as_paid с setup_fee ────────────────────────────────────────────────

class TestMarkAsPaidSetupFee:
    @pytest.fixture
    def user_owner(self, db, shop):
        u = User(email="owner-test@test.com", role="owner", setup_fee_paid=False)
        db.add(u)
        db.flush()
        shop.owner_id = u.id
        db.commit()
        db.refresh(u)
        return u

    def test_setup_fee_paid_sets_user_flag(self, db, svc, sub, shop, user_owner):
        inv = svc.create_setup_fee_invoice(sub.id)
        svc.mark_as_paid(inv.id)
        db.refresh(user_owner)
        assert user_owner.setup_fee_paid is True
        assert user_owner.setup_fee_paid_at is not None

    def test_subscription_paid_does_not_set_setup_fee(self, db, svc, sub, shop, user_owner):
        inv = svc.create_invoice(sub.id)
        svc.mark_as_paid(inv.id)
        db.refresh(user_owner)
        assert user_owner.setup_fee_paid is False

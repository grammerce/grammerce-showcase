"""
Tests for database models
"""
from datetime import datetime

import pytest

from models import Category, Customer, Order, OrderItem, Product, Shop


class TestShopModel:
    """Tests for Shop model"""

    def test_create_shop(self, db_session):
        """Should create shop with valid data"""
        shop = Shop(
            id=100,
            name="Test Shop",
            # bot_token уникален на всю таблицу, а фикстура db_session уже
            # создала магазин с "test_token_123" — берём собственный токен.
            bot_token="test_token_shop_100",
            owner_tg_id=123456789,
            config={"type": "fashion", "currency": "UZS", "min_order": 50000}
        )
        db_session.add(shop)
        db_session.commit()

        retrieved = db_session.query(Shop).filter(Shop.id == 100).first()
        assert retrieved is not None
        assert retrieved.name == "Test Shop"
        assert retrieved.bot_token == "test_token_shop_100"
        assert retrieved.config["type"] == "fashion"

    def test_shop_config_json(self, db_session):
        """Shop config should store JSON data"""
        shop = Shop(
            id=101,
            name="JSON Test Shop",
            bot_token="token_456",
            owner_tg_id=987654321,
            config={
                "type": "food",
                "currency": "UZS",
                "min_order": 30000,
                "delivery_fee": 15000,
                "working_hours": {"start": "09:00", "end": "22:00"}
            }
        )
        db_session.add(shop)
        db_session.commit()

        retrieved = db_session.query(Shop).filter(Shop.id == 101).first()
        assert retrieved.config["type"] == "food"
        assert retrieved.config["working_hours"]["start"] == "09:00"
        assert retrieved.config["delivery_fee"] == 15000


class TestCategoryModel:
    """Tests for Category model"""

    def test_create_category(self, db_session):
        """Should create category linked to shop"""
        category = Category(
            id=100,
            shop_id=1,  # From fixture
            name="Test Category",
            description="Test category description"
        )
        db_session.add(category)
        db_session.commit()

        retrieved = db_session.query(Category).filter(Category.id == 100).first()
        assert retrieved is not None
        assert retrieved.name == "Test Category"
        assert retrieved.shop_id == 1

    def test_category_shop_relationship(self, db_session):
        """Category should belong to a shop"""
        category = db_session.query(Category).filter(Category.shop_id == 1).first()
        assert category is not None
        assert category.shop is not None
        assert category.shop.id == 1


class TestProductModel:
    """Tests for Product model"""

    def test_create_product(self, db_session):
        """Should create product with all fields"""
        product = Product(
            id=100,
            shop_id=1,
            category_id=1,
            name="Test Product",
            description="Test product description",
            price=150000,
            image_url="/img/test.png",
            stock=10,
            sold=0,
            is_active=True
        )
        db_session.add(product)
        db_session.commit()

        retrieved = db_session.query(Product).filter(Product.id == 100).first()
        assert retrieved is not None
        assert retrieved.name == "Test Product"
        assert retrieved.price == 150000
        assert retrieved.stock == 10
        assert retrieved.is_active is True

    def test_product_default_values(self, db_session):
        """Product should have sensible defaults"""
        product = Product(
            id=101,
            shop_id=1,
            category_id=1,
            name="Default Product",
            price=100000
        )
        db_session.add(product)
        db_session.commit()

        retrieved = db_session.query(Product).filter(Product.id == 101).first()
        assert retrieved.stock == 0  # Default
        assert retrieved.sold == 0  # Default
        assert retrieved.is_active is True  # Default

    def test_product_variants_json(self, db_session):
        """Product variants should store JSON array"""
        product = Product(
            id=102,
            shop_id=1,
            category_id=1,
            name="Product with Variants",
            price=200000,
            variants=[
                {"size": "S", "color": "Red", "stock": 5},
                {"size": "M", "color": "Blue", "stock": 3},
                {"size": "L", "color": "Green", "stock": 2}
            ]
        )
        db_session.add(product)
        db_session.commit()

        retrieved = db_session.query(Product).filter(Product.id == 102).first()
        assert retrieved.variants is not None
        assert len(retrieved.variants) == 3
        assert retrieved.variants[0]["size"] == "S"
        assert retrieved.variants[1]["color"] == "Blue"

    def test_product_shop_isolation(self, db_session):
        """Products should be isolated by shop_id"""
        from models import Shop

        # Create second shop
        shop2 = Shop(
            id=200,
            name="Shop 2",
            bot_token="token_200",
            owner_tg_id=200000000,
            config={"type": "tech"}
        )
        db_session.add(shop2)

        category2 = Category(id=200, shop_id=200, name="Cat 2")
        db_session.add(category2)

        # Products for different shops
        product1 = Product(id=200, shop_id=1, category_id=1, name="Shop1 Product", price=100000)
        product2 = Product(id=201, shop_id=200, category_id=200, name="Shop2 Product", price=150000)
        db_session.add_all([product1, product2])
        db_session.commit()

        # Query products for shop 1
        shop1_products = db_session.query(Product).filter(Product.shop_id == 1).all()
        assert all(p.shop_id == 1 for p in shop1_products)

        # Query products for shop 2
        shop2_products = db_session.query(Product).filter(Product.shop_id == 200).all()
        assert len(shop2_products) == 1
        assert shop2_products[0].name == "Shop2 Product"

class TestCustomerModel:
    """Customer — покупатель магазина.

    Прошлая версия этих тестов создавала Customer(name=..., location={...}).
    Ни того, ни другого поля у модели нет: имя хранится как first_name/last_name,
    а координаты живут в BotUser/TelegramProfile — это разные сущности.
    Тесты падали на TypeError ещё на конструкторе.
    """

    def test_create_customer(self, db_session):
        customer = Customer(
            id=100,
            telegram_id=111222333,
            shop_id=1,
            first_name="Test",
            last_name="Customer",
            username="test_customer",
            phone="+998901234567",
        )
        db_session.add(customer)
        db_session.commit()

        retrieved = db_session.query(Customer).filter(Customer.telegram_id == 111222333).first()
        assert retrieved is not None
        assert retrieved.first_name == "Test"
        assert retrieved.last_name == "Customer"
        assert retrieved.phone == "+998901234567"

    def test_bonus_balance_defaults_to_zero(self, db_session):
        customer = Customer(id=104, telegram_id=555000555, shop_id=1, first_name="Bonus")
        db_session.add(customer)
        db_session.commit()

        retrieved = db_session.query(Customer).filter(Customer.id == 104).first()
        assert retrieved.bonus_balance == 0

    def test_same_telegram_id_in_different_shops(self, db_session):
        """Покупатель опознаётся парой telegram_id + shop_id.

        Один и тот же человек в двух магазинах — две независимые записи.
        """
        shop2 = Shop(
            id=300,
            name="Shop 3",
            bot_token="token_300",
            owner_tg_id=300000000,
            config={"type": "beauty"},
        )
        db_session.add(shop2)
        db_session.commit()

        db_session.add_all([
            Customer(id=102, telegram_id=999888777, shop_id=1, first_name="Customer Shop 1"),
            Customer(id=103, telegram_id=999888777, shop_id=300, first_name="Customer Shop 2"),
        ])
        db_session.commit()

        customers = db_session.query(Customer).filter(Customer.telegram_id == 999888777).all()
        assert len(customers) == 2
        assert {c.shop_id for c in customers} == {1, 300}


class TestOrderModel:
    """Order + OrderItem.

    Прошлая версия тестов создавала Order(items=[...JSON...], total=...,
    delivery_address=..., phone=...). Такой схемы нет: позиции лежат в отдельной
    таблице order_items, сумма называется total_amount, а адрес и телефон —
    в meta. Именно это расхождение и было описано в tests/README.md как
    «Model Schema Mismatch», но исправлено не было.
    """

    def _make_customer(self, db_session, customer_id: int, telegram_id: int) -> Customer:
        customer = Customer(
            id=customer_id,
            telegram_id=telegram_id,
            shop_id=1,
            first_name="Order",
            phone="+998901234567",
        )
        db_session.add(customer)
        db_session.commit()
        return customer

    def test_create_order_with_items(self, db_session):
        self._make_customer(db_session, 200, 123000123)

        order = Order(
            id=100,
            customer_id=200,
            shop_id=1,
            total_amount=350000,
            status="pending",
            meta={"delivery_address": "Test Street 123, Tashkent", "phone": "+998901234567"},
        )
        db_session.add(order)
        db_session.commit()

        db_session.add_all([
            OrderItem(order_id=100, product_id=1, quantity=2, price_at_moment=100000),
            OrderItem(order_id=100, product_id=2, quantity=1, price_at_moment=150000),
        ])
        db_session.commit()

        retrieved = db_session.query(Order).filter(Order.id == 100).first()
        assert retrieved is not None
        assert retrieved.total_amount == 350000
        assert retrieved.status == "pending"
        assert len(retrieved.items) == 2
        assert retrieved.meta["delivery_address"] == "Test Street 123, Tashkent"

    def test_price_snapshot_survives_product_price_change(self, db_session):
        """Ключевой инвариант: цена фиксируется в order_items при оформлении.

        Позиция заказа хранит price_at_moment — снимок, а не ссылку на товар.
        Изменение цены товара задним числом не должно менять историю заказов.
        """
        self._make_customer(db_session, 201, 456000456)

        order = Order(id=101, customer_id=201, shop_id=1, total_amount=85000, status="pending")
        db_session.add(order)
        db_session.commit()

        db_session.add(OrderItem(order_id=101, product_id=1, quantity=1, price_at_moment=85000))
        db_session.commit()

        product = db_session.query(Product).filter(Product.id == 1).first()
        product.price = 120000
        db_session.commit()

        retrieved = db_session.query(Order).filter(Order.id == 101).first()
        assert retrieved.items[0].price_at_moment == 85000
        assert retrieved.total_amount == 85000

    def test_order_uuid_is_generated(self, db_session):
        """order_uuid проставляется сам — по нему заказ ищется во внешних системах."""
        self._make_customer(db_session, 204, 111000111)

        order = Order(id=103, customer_id=204, shop_id=1, total_amount=1000)
        db_session.add(order)
        db_session.commit()

        retrieved = db_session.query(Order).filter(Order.id == 103).first()
        assert retrieved.order_uuid
        assert len(retrieved.order_uuid) == 36

    def test_order_timestamps(self, db_session):
        self._make_customer(db_session, 202, 789000789)

        order = Order(id=102, customer_id=202, shop_id=1, total_amount=100000, status="pending")
        db_session.add(order)
        db_session.commit()

        retrieved = db_session.query(Order).filter(Order.id == 102).first()
        assert retrieved.created_at is not None
        assert isinstance(retrieved.created_at, datetime)

    def test_order_status_values(self, db_session):
        self._make_customer(db_session, 203, 321000321)

        valid_statuses = ["pending", "confirmed", "preparing", "delivering", "completed", "cancelled"]

        for idx, status in enumerate(valid_statuses):
            order = Order(
                id=200 + idx,
                customer_id=203,
                shop_id=1,
                total_amount=100000,
                status=status,
            )
            db_session.add(order)
            db_session.commit()

            retrieved = db_session.query(Order).filter(Order.id == 200 + idx).first()
            assert retrieved.status == status

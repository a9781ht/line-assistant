from __future__ import annotations

import uuid
from functools import lru_cache
from importlib.resources import files
from typing import Any, cast

import yaml
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from line_assistant.core.errors import DomainError, NotFoundError
from line_assistant.db.models import Category, Direction, Ledger, PaymentKind, PaymentMethod


@lru_cache
def load_ledger_seed() -> dict[str, Any]:
    """讀取內建的帳本資料 ledger.yaml，使用 @lru_cache，避免每建立一個帳本都重複讀取 YAML 檔。"""
    seed_text = files("line_assistant.seeds").joinpath("ledger.yaml").read_text(encoding="utf-8")
    return cast(dict[str, Any], yaml.safe_load(seed_text))


# 管理記帳分類。
class CategoryService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def seed_fixed_categories(self, ledger: Ledger) -> None:
        """建立固定記帳分類樹，若已存在則不再重複建立。"""
        existing = await self.session.scalar(
            select(Category.id).where(Category.ledger_id == ledger.id).limit(1)
        )
        if existing is not None:
            return

        categories = cast(list[dict[str, Any]], load_ledger_seed()["categories"])
        for root_order, root_data in enumerate(categories):
            direction = Direction(cast(str, root_data["direction"]))
            root = Category(
                ledger_id=ledger.id,
                parent_id=None,
                direction=direction,
                depth=1,
                system_key=cast(str, root_data["key"]),
                name=cast(str, root_data["name"]),
                sort_order=root_order,
                is_system=True,
            )
            self.session.add(root)
            await self.session.flush()
            children = cast(list[dict[str, str]], root_data["children"])
            for child_order, child_data in enumerate(children):
                self.session.add(
                    Category(
                        ledger_id=ledger.id,
                        parent_id=root.id,
                        direction=direction,
                        depth=2,
                        system_key=child_data["key"],
                        name=child_data["name"],
                        sort_order=child_order,
                        is_system=True,
                    )
                )
        await self.session.flush()

    async def get(self, ledger_id: uuid.UUID, category_id: uuid.UUID) -> Category:
        """依 UUID 取得某帳本的記帳分類。"""
        category = await self.session.scalar(
            select(Category).where(
                Category.id == category_id,
                Category.ledger_id == ledger_id,
            )
        )
        if category is None:
            raise NotFoundError("找不到指定記帳分類")
        return category

    async def get_by_system_key(self, ledger_id: uuid.UUID, system_key: str) -> Category:
        """依固定鍵取得記帳分類。"""
        category = await self.session.scalar(
            select(Category).where(
                Category.ledger_id == ledger_id,
                Category.system_key == system_key,
            )
        )
        if category is None:
            raise NotFoundError(f"找不到系統記帳分類：{system_key}")
        return category

    async def list_children(
        self,
        ledger_id: uuid.UUID,
        parent_id: uuid.UUID | None,
        *,
        direction: Direction | None = None,
        include_inactive: bool = False,
    ) -> list[Category]:
        """取得某記帳分類底下的記帳子分類。"""
        query: Select[tuple[Category]] = select(Category).where(
            Category.ledger_id == ledger_id,
            Category.parent_id == parent_id,
        )
        if direction is not None:
            query = query.where(Category.direction == direction)
        if not include_inactive:
            query = query.where(Category.is_active.is_(True))
        query = query.order_by(Category.sort_order, Category.name)
        return list((await self.session.scalars(query)).all())

    async def has_active_children(self, category: Category) -> bool:
        child = await self.session.scalar(
            select(Category.id)
            .where(Category.parent_id == category.id, Category.is_active.is_(True))
            .limit(1)
        )
        return child is not None

    async def list_custom(
        self,
        ledger_id: uuid.UUID,
        *,
        page: int = 0,
        page_size: int = 8,
    ) -> tuple[list[Category], bool]:
        query = (
            select(Category)
            .where(
                Category.ledger_id == ledger_id,
                Category.is_system.is_(False),
                Category.is_active.is_(True),
            )
            .order_by(Category.depth, Category.name)
            .offset(max(page, 0) * page_size)
            .limit(page_size + 1)
        )
        rows = list((await self.session.scalars(query)).all())
        return rows[:page_size], len(rows) > page_size

    async def add_children(self, parent: Category, names: list[str]) -> list[Category]:
        """在自訂記帳分類底下新增下一層記帳子分類。"""
        if parent.depth < 2:
            raise DomainError("只能在固定第二層記帳分類或其記帳子分類下新增項目")
        if not parent.is_active:
            raise DomainError("不能在已停用的記帳分類下新增項目")

        existing = {
            item.name.casefold(): item
            for item in await self.list_children(
                parent.ledger_id, parent.id, include_inactive=True
            )
        }
        next_order = max((item.sort_order for item in existing.values()), default=-1) + 1
        result: list[Category] = []
        for name in names:
            old = existing.get(name.casefold())
            if old is not None:
                old.is_active = True
                old.version += 1
                result.append(old)
                continue
            category = Category(
                ledger_id=parent.ledger_id,
                parent_id=parent.id,
                direction=parent.direction,
                depth=parent.depth + 1,
                name=name,
                sort_order=next_order,
                is_system=False,
            )
            next_order += 1
            self.session.add(category)
            result.append(category)
        await self.session.flush()
        return result

    async def rename(self, category: Category, name: str) -> None:
        """重新命名自訂記帳分類。"""
        if category.is_system:
            raise DomainError("固定記帳分類不能改名")
        duplicate = await self.session.scalar(
            select(Category.id).where(
                Category.ledger_id == category.ledger_id,
                Category.parent_id == category.parent_id,
                Category.name == name,
                Category.id != category.id,
            )
        )
        if duplicate is not None:
            raise DomainError("同一層已經有相同名稱")
        category.name = name
        category.version += 1

    async def disable(self, category: Category) -> None:
        """停用自訂記帳分類，不做硬刪除。"""
        if category.is_system:
            raise DomainError("固定記帳分類不能停用")
        if await self.has_active_children(category):
            raise DomainError("請先停用這個記帳分類底下的記帳子分類")
        category.is_active = False
        category.version += 1

    async def path(self, category: Category) -> tuple[list[str], list[str]]:
        """取得某記帳分類的完整路徑，包含父記帳分類名稱與系統鍵。"""
        names: list[str] = []
        system_keys: list[str] = []
        current: Category | None = category
        visited: set[uuid.UUID] = set()
        while current is not None:
            if current.id in visited:
                raise DomainError("記帳分類樹含有循環")
            visited.add(current.id)
            names.append(current.name)
            if current.system_key:
                system_keys.append(current.system_key)
            if current.parent_id is None:
                break
            current = await self.session.get(Category, current.parent_id)
        names.reverse()
        system_keys.reverse()
        return names, system_keys


# 管理付款方式。
class PaymentMethodService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_for_user(
        self,
        user_id: uuid.UUID,
        kind: PaymentKind,
        *,
        include_inactive: bool = False,
    ) -> list[PaymentMethod]:
        """取得用戶指定付款方式下的付款工具清單。"""
        query = select(PaymentMethod).where(
            PaymentMethod.owner_user_id == user_id,
            PaymentMethod.kind == kind,
        )
        if not include_inactive:
            query = query.where(PaymentMethod.is_active.is_(True))
        query = query.order_by(PaymentMethod.sort_order, PaymentMethod.name)
        return list((await self.session.scalars(query)).all())

    async def get_for_user(
        self, user_id: uuid.UUID, method_id: uuid.UUID
    ) -> PaymentMethod:
        """取得用戶的付款工具，若不存在則拋出 NotFoundError。"""
        method = await self.session.scalar(
            select(PaymentMethod).where(
                PaymentMethod.id == method_id,
                PaymentMethod.owner_user_id == user_id,
            )
        )
        if method is None:
            raise NotFoundError("找不到指定付款工具")
        return method

    async def list_all_for_user(
        self,
        user_id: uuid.UUID,
        *,
        page: int = 0,
        page_size: int = 8,
    ) -> tuple[list[PaymentMethod], bool]:
        """取得用戶所有付款工具，包含現金與信用卡付款方式。"""
        query = (
            select(PaymentMethod)
            .where(
                PaymentMethod.owner_user_id == user_id,
                PaymentMethod.is_active.is_(True),
            )
            .order_by(PaymentMethod.kind, PaymentMethod.sort_order, PaymentMethod.name)
            .offset(max(page, 0) * page_size)
            .limit(page_size + 1)
        )
        rows = list((await self.session.scalars(query)).all())
        return rows[:page_size], len(rows) > page_size

    async def add_many(
        self, user_id: uuid.UUID, kind: PaymentKind, names: list[str]
    ) -> list[PaymentMethod]:
        existing = {
            item.name.casefold(): item
            for item in await self.list_for_user(user_id, kind, include_inactive=True)
        }
        next_order = max((item.sort_order for item in existing.values()), default=-1) + 1
        result: list[PaymentMethod] = []
        for name in names:
            old = existing.get(name.casefold())
            if old is not None:
                old.is_active = True
                old.version += 1
                result.append(old)
                continue
            method = PaymentMethod(
                owner_user_id=user_id,
                kind=kind,
                name=name,
                sort_order=next_order,
            )
            next_order += 1
            self.session.add(method)
            result.append(method)
        await self.session.flush()
        return result

    async def rename(self, method: PaymentMethod, name: str) -> None:
        duplicate = await self.session.scalar(
            select(PaymentMethod.id).where(
                PaymentMethod.owner_user_id == method.owner_user_id,
                PaymentMethod.kind == method.kind,
                PaymentMethod.name == name,
                PaymentMethod.id != method.id,
            )
        )
        if duplicate is not None:
            raise DomainError("這個付款方式下已經有相同名稱的付款工具")
        method.name = name
        method.version += 1

    def disable(self, method: PaymentMethod) -> None:
        method.is_active = False
        method.version += 1


def payment_kind_label(kind: PaymentKind) -> str:
    return "現金" if kind is PaymentKind.CASH else "信用卡"

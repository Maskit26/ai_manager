from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ai_seller_core.persistence.models import (
    CatalogImport,
    CatalogImportError,
    CatalogSource,
    Product,
)


class CatalogImportFinalizationError(ValueError):
    pass


async def finalize_catalog_import(
    session: AsyncSession,
    *,
    project_id: str,
    import_id: str,
    products: Sequence[Mapping[str, Any]],
) -> None:
    if session.in_transaction():
        raise RuntimeError(
            "finalize_catalog_import requires a session without an active transaction"
        )

    product_rows: list[dict[str, Any]] = []
    seen_external_ids: set[str] = set()
    for product in products:
        external_id = product.get("external_id")
        name = product.get("name")
        if not isinstance(external_id, str) or not external_id.strip():
            raise ValueError("Each imported product must have a non-empty external_id")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Each imported product must have a non-empty name")
        if external_id in seen_external_ids:
            raise ValueError(f"Duplicate external_id in import: {external_id}")
        seen_external_ids.add(external_id)
        product_rows.append(dict(product))

    finalization_error: str | None = None
    async with session.begin():
        import_record = await session.scalar(
            select(CatalogImport)
            .where(
                CatalogImport.project_id == project_id,
                CatalogImport.id == import_id,
            )
            .with_for_update()
        )
        if import_record is None:
            raise LookupError(f"Catalog import {import_id} was not found in project {project_id}")
        if import_record.status != "processing":
            raise CatalogImportFinalizationError(
                f"Catalog import {import_id} is not processing"
            )

        source = await session.scalar(
            select(CatalogSource)
            .where(
                CatalogSource.project_id == project_id,
                CatalogSource.id == import_record.source_id,
            )
            .with_for_update()
        )
        if source is None:
            raise LookupError(
                f"Catalog source {import_record.source_id} was not found in project {project_id}"
            )

        error_counts = await session.execute(
            select(CatalogImportError.category, func.count())
            .where(
                CatalogImportError.project_id == project_id,
                CatalogImportError.import_id == import_id,
            )
            .group_by(CatalogImportError.category)
        )
        counts = dict(error_counts.all())
        has_critical_errors = counts.get("completeness-critical", 0) > 0
        non_critical_errors = counts.get("non-critical", 0)
        complete_pass = (
            import_record.source_read_complete
            and not import_record.structural_error
            and import_record.processed_rows == import_record.total_rows
            and import_record.deactivation_safe
            and not has_critical_errors
        )
        if not complete_pass:
            import_record.status = "failed"
            import_record.completed_at = datetime.now(UTC)
            finalization_error = (
                "Catalog import is incomplete or contains errors that make deactivation unsafe"
            )
        else:
            if product_rows:
                for row in product_rows:
                    row.update(
                        project_id=project_id,
                        source_id=source.id,
                        last_seen_import_id=import_id,
                        status="active",
                    )

                statement = insert(Product).values(product_rows)
                statement = statement.on_conflict_do_update(
                    constraint="uq_products_source_external",
                    set_={
                        "sku": statement.excluded.sku,
                        "name": statement.excluded.name,
                        "category": statement.excluded.category,
                        "attributes": statement.excluded.attributes,
                        "price": statement.excluded.price,
                        "stock": statement.excluded.stock,
                        "currency": statement.excluded.currency,
                        "status": "active",
                        "last_seen_import_id": import_id,
                        "version": Product.version + 1,
                        "updated_at": func.now(),
                    },
                )
                await session.execute(statement)

            absent_products = update(Product).where(
                Product.project_id == project_id,
                Product.source_id == source.id,
            )
            if seen_external_ids:
                absent_products = absent_products.where(
                    Product.external_id.not_in(seen_external_ids)
                )
            await session.execute(absent_products.values(status="inactive", updated_at=func.now()))

            await session.execute(
                update(CatalogSource)
                .where(
                    CatalogSource.project_id == project_id,
                    CatalogSource.id != source.id,
                )
                .values(is_authoritative=False, status="inactive")
            )
            source.is_authoritative = True
            source.status = "active"
            import_record.status = (
                "completed_with_errors" if non_critical_errors else "completed"
            )
            import_record.completed_at = datetime.now(UTC)

    if finalization_error is not None:
        raise CatalogImportFinalizationError(finalization_error)

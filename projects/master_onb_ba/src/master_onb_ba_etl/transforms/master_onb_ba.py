"""Auto-generated @transform stubs for domain 'master_onb_ba'.

Each function below is a DRAFT starting point from the Alteryx-generated PySpark.
Review carefully before use — in particular:
  * Column names may need updating to match the bronze/silver table's actual schema.
  * dropDuplicates with 'any' semantics may select a different row than Alteryx did.
"""

from pyspark.sql import DataFrame
import pyspark.sql.functions as F  # noqa: F401

from datalab.framework.registry import transform


@transform("master_onb_ba.gold_xlsx_ba_master_selected")
# CONFIDENCE: medium — TODO review before production use
# WHY transform: [Unique] Unique keeps 'any' matching row (no order key, decisions[].mode=any) — dropDuplicates, NOT cdc (cdc would pick a different, order-dependent survivor)
def gold_xlsx_ba_master_selected(df: DataFrame, **kwargs) -> DataFrame:
    """Transform for master_onb_ba.gold_xlsx_ba_master_selected.

    Generated from Alteryx tool chain — review all column names,
    types, and deduplication semantics before use.
    """
    # TODO: review and adapt the generated code below
    # --- [Unique] ---
    # [9] Unique
    xlsx_ba_master_unique = bronze_xlsx_ba_master.dropDuplicates(
        [
            'Business Area', 'Description', 'Company code', 'Unit', 'Business Name', 'Field_13_4',
            'Field_13_5', 'Field_13_6',
        ]
    )
    # --- [Formula] ---
    # [5] Formula
    xlsx_ba_master_with_syncdate = xlsx_ba_master_unique.withColumn(
        'SYNCDATE', F.date_format(F.current_timestamp(), 'yyyy-MM-dd HH:mm:ss'),
    )
    # --- [Select] ---
    # [2] Select
    xlsx_ba_master_selected_have = {c.lower() for c in xlsx_ba_master_with_syncdate.columns}
    xlsx_ba_master_selected = xlsx_ba_master_with_syncdate.select(
        F.col('SYNCDATE').cast('timestamp').alias('SYNCDATE'),
        *([F.col('Business Area').alias('BUSINESS_AREA')] if 'business area' in xlsx_ba_master_selected_have else []),
        *([F.col('Description').alias('DESCRIPTION')] if 'description' in xlsx_ba_master_selected_have else []),
        *([F.col('Company code').alias('COMPANY_CODE')] if 'company code' in xlsx_ba_master_selected_have else []),
        *([F.col('Unit').alias('UNIT')] if 'unit' in xlsx_ba_master_selected_have else []),
        *([F.col('Business Name').alias('PROJECT')] if 'business name' in xlsx_ba_master_selected_have else []),
    )
    # TODO: replace variable names above with 'df' and return result
    return df  # FIXME: replace with the actual transform result


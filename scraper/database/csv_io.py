"""CSV import/export composition."""
from __future__ import annotations

from scraper.database.constants import (
    SCHEMA_VERSION,
    DUPLICATE_STRATEGIES,
    DEFAULT_DEDUPE_STRATEGIES,
    _VOLATILE_URL_PARAMS,
    _MERGE_SEP,
    _MERGE_UNION_FIELDS,
    DEFAULT_DB_PATH,
    _OFFENDER_INSERT_COLUMNS,
    _OFFENDER_INSERT_SQL,
    _record_to_insert_tuple,
    _utc_now_iso,
    _escape_like,
)

from scraper.database.csv_infer_csv_jurisdiction import InferCsvJurisdictionCsvMixin
from scraper.database.csv_tag_record_source import TagRecordSourceCsvMixin
from scraper.database.csv_import_records import ImportRecordsCsvMixin
from scraper.database.csv_build_name_merge_index import BuildNameMergeIndexCsvMixin
from scraper.database.csv_find_merge_target import FindMergeTargetCsvMixin
from scraper.database.csv_merge_source_into_existing import MergeSourceIntoExistingCsvMixin
from scraper.database.csv_import_csv import ImportCsvCsvMixin
from scraper.database.csv_import_csv_directory import ImportCsvDirectoryCsvMixin
from scraper.database.csv_backfill_sources import BackfillSourcesCsvMixin
from scraper.database.csv_export_to_csv import ExportToCsvCsvMixin
from scraper.database.csv_normalize_record import NormalizeRecordCsvMixin
from scraper.database.csv_repair_fl_sor import RepairFlSorCsvMixin


class CsvMixin(
    InferCsvJurisdictionCsvMixin,
    TagRecordSourceCsvMixin,
    ImportRecordsCsvMixin,
    BuildNameMergeIndexCsvMixin,
    FindMergeTargetCsvMixin,
    MergeSourceIntoExistingCsvMixin,
    ImportCsvCsvMixin,
    ImportCsvDirectoryCsvMixin,
    BackfillSourcesCsvMixin,
    ExportToCsvCsvMixin,
    NormalizeRecordCsvMixin,
    RepairFlSorCsvMixin,
):
    """CSV import/export with multi-source merge."""

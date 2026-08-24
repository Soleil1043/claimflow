"""医疗类工具装配：import 即注册到默认注册中心。"""

from tools.medical.diagnosis_matcher import (
    DiagnosisMatcherInput,
    DiagnosisMatcherOutput,
    DiagnosisMatcherTool,
)
from tools.medical.ocr_extract import (
    OcrExtractInput,
    OcrExtractOutput,
    OcrExtractTool,
)
from tools.medical.record_query import (
    RecordQueryInput,
    RecordQueryOutput,
    RecordQueryTool,
)
from tools.registry import get_default_registry

get_default_registry().register(RecordQueryTool())
get_default_registry().register(DiagnosisMatcherTool())
get_default_registry().register(OcrExtractTool())

__all__ = [
    "RecordQueryTool",
    "RecordQueryInput",
    "RecordQueryOutput",
    "DiagnosisMatcherTool",
    "DiagnosisMatcherInput",
    "DiagnosisMatcherOutput",
    "OcrExtractTool",
    "OcrExtractInput",
    "OcrExtractOutput",
]

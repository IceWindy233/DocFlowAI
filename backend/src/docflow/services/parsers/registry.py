from __future__ import annotations

from docflow.services.parsers.base import ParseContext, ParsedContent, ParserError
from docflow.services.parsers.native import (
    DoclingEnricher,
    DocxParser,
    ImageParser,
    LegacyOfficeParser,
    PdfParser,
    SpreadsheetParser,
)


class ParserRegistry:
    def __init__(self) -> None:
        self.parsers = [
            PdfParser(),
            DocxParser(),
            SpreadsheetParser(),
            ImageParser(),
            LegacyOfficeParser(),
        ]
        self.docling = DoclingEnricher()

    def get(self, suffix: str):
        suffix = suffix.lower()
        for parser in self.parsers:
            if suffix in parser.supported_extensions:
                return parser
        raise ParserError(f"未注册解析器：{suffix}")

    def parse(self, context: ParseContext) -> ParsedContent:
        parser = self.get(context.source_path.suffix)
        result = parser.parse(context)
        if context.source_path.suffix.lower() in {".pdf", ".docx"}:
            markdown, warning = self.docling.enrich(context.source_path)
            if markdown:
                result.metadata["docling_markdown"] = markdown
                result.parser_version = f"docling+{result.parser_version}"
            elif warning:
                result.warnings.append(warning)
        return result

from docflow.services.expansion_sample import allocate_quotas


def test_expansion_sample_quotas_are_deterministic_and_total_target() -> None:
    quotas = allocate_quotas(200)
    assert sum(quotas.values()) == 200
    assert quotas == {
        "PDF_NATIVE": 30,
        "PDF_SCANNED": 30,
        "PDF_COMPLEX": 15,
        "DOC": 40,
        "DOCX": 30,
        "WPS": 20,
        "SPREADSHEET": 15,
        "IMAGE": 1,
        "DUPLICATE_PAIR": 8,
        "SKIPPED_TEMP": 5,
        "UNSUPPORTED": 6,
    }


def test_expansion_sample_quota_allocation_handles_other_sizes() -> None:
    assert sum(allocate_quotas(17).values()) == 17

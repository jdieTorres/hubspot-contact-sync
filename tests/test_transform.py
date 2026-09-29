from hubspot_sync.transform import normalize_phone, transform_rows


def test_normalizes_and_maps_fields():
    r = transform_rows([{"email": "  ANA@Example.COM ", "first_name": "ANA MARÍA", "last_name": "pérez",
                         "phone": "+57 (300) 123-4567", "language": "Español"}])
    assert r.valid == [{"email": "ana@example.com", "firstname": "Ana María", "lastname": "Pérez",
                        "phone": "+573001234567", "hs_language": "es"}]


def test_rejects_missing_and_invalid_emails():
    r = transform_rows([{"email": ""}, {"email": "not-an-email"}, {"email": "ok@x.co"}])
    assert [x["error"] for x in r.rejected] == ["missing email", "invalid email format"]
    assert [x["row"] for x in r.rejected] == [2, 3]
    assert len(r.valid) == 1


def test_merges_duplicates_without_erasing_data():
    r = transform_rows([
        {"email": "a@x.co", "first_name": "Ana", "phone": "3001112222"},
        {"email": "A@X.CO", "first_name": "", "city": "Bogotá"},
    ])
    assert r.duplicates_merged == 1
    assert r.valid == [{"email": "a@x.co", "firstname": "Ana", "phone": "3001112222", "city": "Bogotá"}]


def test_blank_phone_stays_blank():
    assert normalize_phone("   ") == ""

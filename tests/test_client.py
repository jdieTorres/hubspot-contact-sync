import pytest
import responses

from hubspot_sync.client import BASE_URL, HubSpotClient, HubSpotError

UPSERT = f"{BASE_URL}/crm/v3/objects/contacts/batch/upsert"
LIST = f"{BASE_URL}/crm/v3/objects/contacts"


@pytest.fixture
def sleeps():
    return []


@pytest.fixture
def client(sleeps):
    return HubSpotClient("test-token", max_retries=3, sleep=sleeps.append)


def test_requires_token():
    with pytest.raises(ValueError):
        HubSpotClient("")


@responses.activate
def test_sends_bearer_token(client):
    responses.post(UPSERT, json={"status": "COMPLETE", "results": []})
    client.batch_upsert_contacts([{"email": "a@b.co"}])
    assert responses.calls[0].request.headers["Authorization"] == "Bearer test-token"


@responses.activate
def test_upsert_payload_uses_email_as_id(client):
    responses.post(UPSERT, json={"results": []})
    client.batch_upsert_contacts([{"email": "a@b.co", "firstname": "Ana"}])
    body = responses.calls[0].request.body
    assert b'"idProperty": "email"' in body and b'"id": "a@b.co"' in body


def test_rejects_batches_over_100(client):
    with pytest.raises(ValueError):
        client.batch_upsert_contacts([{"email": f"u{i}@x.co"} for i in range(101)])


@responses.activate
def test_retries_429_honoring_retry_after(client, sleeps):
    responses.post(UPSERT, status=429, headers={"Retry-After": "2"}, json={"message": "rate limit"})
    responses.post(UPSERT, json={"results": [{"id": "1", "new": True}]})
    data = client.batch_upsert_contacts([{"email": "a@b.co"}])
    assert data["results"][0]["new"] is True
    assert sleeps == [2.0]


@responses.activate
def test_exponential_backoff_on_5xx(client, sleeps):
    for _ in range(3):
        responses.post(UPSERT, status=503)
    responses.post(UPSERT, json={"results": []})
    client.batch_upsert_contacts([{"email": "a@b.co"}])
    assert len(sleeps) == 3
    assert 1 <= sleeps[0] < 1.6 and 2 <= sleeps[1] < 2.6 and 4 <= sleeps[2] < 4.6


@responses.activate
def test_gives_up_after_max_retries(client, sleeps):
    for _ in range(4):
        responses.post(UPSERT, status=429, json={"message": "rate limit"})
    with pytest.raises(HubSpotError) as exc:
        client.batch_upsert_contacts([{"email": "a@b.co"}])
    assert exc.value.status == 429
    assert len(sleeps) == 3


@responses.activate
def test_does_not_retry_400(client, sleeps):
    responses.post(UPSERT, status=400, json={"message": "Property values were not valid"})
    with pytest.raises(HubSpotError) as exc:
        client.batch_upsert_contacts([{"email": "a@b.co"}])
    assert exc.value.status == 400 and "not valid" in str(exc.value)
    assert sleeps == []


@responses.activate
def test_pagination_follows_after_cursor(client):
    responses.get(LIST, json={"results": [{"id": "1"}, {"id": "2"}], "paging": {"next": {"after": "3"}}})
    responses.get(LIST, json={"results": [{"id": "3"}]})
    ids = [c["id"] for c in client.iter_contacts(["email"])]
    assert ids == ["1", "2", "3"]
    assert "after=3" in responses.calls[1].request.url

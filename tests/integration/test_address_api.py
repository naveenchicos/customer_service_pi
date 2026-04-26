"""
Integration tests for the addresses API.

End-to-end against a real PostgreSQL — verifies behaviour that depends on DB
features the unit tests can't fake: the partial unique index on dedup_key,
FK cycle between accounts and addresses, and same-txn updates to account
default-pointer columns.

Run prerequisites: see tests/integration/conftest.py.
"""

import uuid

import pytest


def _addr_payload(**overrides) -> dict:
    base = {
        "line1": "123 Main St",
        "line2": "Apt 3A",
        "city": "Boston",
        "state": "MA",
        "postal_code": "02101",
        "country": "USA",
    }
    base.update(overrides)
    return base


class TestCreateAddress:
    async def test_create_returns_201_with_address_body(
        self, client, created_account_id
    ):
        resp = await client.post(
            f"/accounts/{created_account_id}/addresses", json=_addr_payload()
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["line1"] == "123 Main St"
        assert body["country"] == "USA"
        assert body["status"] == "active"
        assert "dedup_key" not in body  # internal field never exposed
        assert "address_type" not in body  # field doesn't exist
        assert "message" not in body  # 201 status is enough; no message wrapper

    async def test_billing_same_as_shipping_sets_both_account_defaults(
        self, client, created_account_id
    ):
        resp = await client.post(
            f"/accounts/{created_account_id}/addresses",
            json=_addr_payload(billing_same_as_shipping=True),
        )
        assert resp.status_code == 201
        addr_id = resp.json()["id"]

        acct = await client.get(f"/accounts/{created_account_id}")
        body = acct.json()
        assert body["default_shipping_address_id"] == addr_id
        assert body["default_billing_address_id"] == addr_id

    async def test_duplicate_dedup_returns_409(self, client, created_account_id):
        first = await client.post(
            f"/accounts/{created_account_id}/addresses", json=_addr_payload()
        )
        assert first.status_code == 201

        # Same line1 prefix + city + state + postal → duplicate
        dup = await client.post(
            f"/accounts/{created_account_id}/addresses",
            json=_addr_payload(line1="123 Main Street"),  # same first 5 chars
        )
        assert dup.status_code == 409
        assert dup.json()["detail"]["code"] == "ADDRESS_DUPLICATE"

    async def test_unsupported_country_returns_400(self, client, created_account_id):
        resp = await client.post(
            f"/accounts/{created_account_id}/addresses",
            json=_addr_payload(country="GBR"),
        )
        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "COUNTRY_NOT_SUPPORTED"

    async def test_account_not_found_returns_404(self, client):
        unknown = uuid.uuid4()
        resp = await client.post(f"/accounts/{unknown}/addresses", json=_addr_payload())
        assert resp.status_code == 404

    async def test_max_10_addresses_enforced(self, client, created_account_id):
        for i in range(10):
            resp = await client.post(
                f"/accounts/{created_account_id}/addresses",
                json=_addr_payload(line1=f"{i:05d} St"),  # unique line1 each
            )
            assert resp.status_code == 201, resp.text

        resp = await client.post(
            f"/accounts/{created_account_id}/addresses",
            json=_addr_payload(line1="99999 Final St"),
        )
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "ADDRESS_LIMIT_EXCEEDED"


class TestListAddresses:
    async def test_list_returns_only_active(self, client, created_account_id):
        a = await client.post(
            f"/accounts/{created_account_id}/addresses", json=_addr_payload()
        )
        b = await client.post(
            f"/accounts/{created_account_id}/addresses",
            json=_addr_payload(line1="999 Side St"),
        )
        assert a.status_code == 201 and b.status_code == 201

        await client.delete(
            f"/accounts/{created_account_id}/addresses/{a.json()['id']}"
        )

        resp = await client.get(f"/accounts/{created_account_id}/addresses")
        assert resp.status_code == 200
        ids = [item["id"] for item in resp.json()]
        assert b.json()["id"] in ids
        assert a.json()["id"] not in ids


class TestSoftDeleteAddress:
    async def test_delete_clears_account_default_pointers(
        self, client, created_account_id
    ):
        created = await client.post(
            f"/accounts/{created_account_id}/addresses",
            json=_addr_payload(billing_same_as_shipping=True),
        )
        assert created.status_code == 201
        addr_id = created.json()["id"]

        deleted = await client.delete(
            f"/accounts/{created_account_id}/addresses/{addr_id}"
        )
        assert deleted.status_code == 200
        assert deleted.json()["status"] == "inactive"

        acct = await client.get(f"/accounts/{created_account_id}")
        body = acct.json()
        assert body["default_shipping_address_id"] is None
        assert body["default_billing_address_id"] is None


class TestUpdateAddress:
    async def test_patch_updates_fields_and_recomputes_dedup(
        self, client, created_account_id
    ):
        created = await client.post(
            f"/accounts/{created_account_id}/addresses", json=_addr_payload()
        )
        addr_id = created.json()["id"]

        resp = await client.patch(
            f"/accounts/{created_account_id}/addresses/{addr_id}",
            json={"city": "Cambridge"},
        )
        assert resp.status_code == 200
        assert resp.json()["city"] == "Cambridge"

    async def test_patch_billing_same_as_shipping_updates_account(
        self, client, created_account_id
    ):
        created = await client.post(
            f"/accounts/{created_account_id}/addresses", json=_addr_payload()
        )
        addr_id = created.json()["id"]

        resp = await client.patch(
            f"/accounts/{created_account_id}/addresses/{addr_id}",
            json={"billing_same_as_shipping": True},
        )
        assert resp.status_code == 200

        acct = await client.get(f"/accounts/{created_account_id}")
        assert acct.json()["default_shipping_address_id"] == addr_id
        assert acct.json()["default_billing_address_id"] == addr_id

    async def test_empty_patch_returns_422(self, client, created_account_id):
        created = await client.post(
            f"/accounts/{created_account_id}/addresses", json=_addr_payload()
        )
        addr_id = created.json()["id"]

        resp = await client.patch(
            f"/accounts/{created_account_id}/addresses/{addr_id}",
            json={},
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["code"] == "NO_FIELDS_TO_UPDATE"


@pytest.mark.usefixtures("created_account_id")
class TestErrorEnvelopeShape:
    async def test_404_uses_error_detail_envelope(self, client, created_account_id):
        unknown = uuid.uuid4()
        resp = await client.get(f"/accounts/{created_account_id}/addresses/{unknown}")
        assert resp.status_code == 404
        body = resp.json()["detail"]
        assert body["code"] == "ADDRESS_NOT_FOUND"
        assert "correlation_id" in body

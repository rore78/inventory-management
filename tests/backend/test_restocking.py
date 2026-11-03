"""
Tests for restocking API endpoints.
"""
from datetime import datetime, timedelta

import pytest

import main


@pytest.fixture(autouse=True)
def clear_restock_orders():
    """Submitted orders live in a module-level list, so reset it around every test."""
    main.restock_orders.clear()
    yield
    main.restock_orders.clear()


TREND_RANK = {"increasing": 0, "stable": 1, "decreasing": 2}


class TestRestockCandidates:
    """Test suite for the restock candidates endpoint."""

    def test_get_restock_candidates(self, client):
        """Test getting restock candidates."""
        response = client.get("/api/restock/candidates")
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, list)
        assert len(data) > 0

        first = data[0]
        for field in ["item_sku", "item_name", "trend", "quantity",
                      "unit_cost", "line_cost", "lead_time_days"]:
            assert field in first

    def test_restock_candidates_ranking(self, client):
        """Test that candidates are ranked by trend, then by demand gap descending."""
        data = client.get("/api/restock/candidates").json()

        keys = [(TREND_RANK[c["trend"]], -c["quantity"]) for c in data]
        assert keys == sorted(keys)
        assert data[0]["trend"] == "increasing"

    def test_restock_candidates_exclude_non_positive_gap(self, client):
        """Test that items whose forecast does not exceed current demand are excluded."""
        forecasts = client.get("/api/demand").json()
        candidates = client.get("/api/restock/candidates").json()
        candidate_skus = {c["item_sku"] for c in candidates}

        for f in forecasts:
            gap = f["forecasted_demand"] - f["current_demand"]
            if gap <= 0:
                assert f["item_sku"] not in candidate_skus
            else:
                assert f["item_sku"] in candidate_skus

        # MTR-304 has a decreasing forecast in the mock data
        assert "MTR-304" not in candidate_skus

    def test_restock_candidates_line_cost_calculation(self, client):
        """Test that quantity and line cost come from the forecast data."""
        forecasts = {f["item_sku"]: f for f in client.get("/api/demand").json()}
        candidates = client.get("/api/restock/candidates").json()

        for c in candidates:
            f = forecasts[c["item_sku"]]
            assert c["quantity"] == f["forecasted_demand"] - f["current_demand"]
            assert c["unit_cost"] == f["unit_cost"]
            assert c["lead_time_days"] == f["lead_time_days"]
            assert abs(c["line_cost"] - c["quantity"] * c["unit_cost"]) < 0.01

    def test_demand_forecasts_have_cost_and_lead_time(self, client):
        """Test that every demand forecast includes unit cost and lead time."""
        data = client.get("/api/demand").json()
        for f in data:
            assert isinstance(f["unit_cost"], (int, float))
            assert f["unit_cost"] > 0
            assert isinstance(f["lead_time_days"], int)
            assert f["lead_time_days"] > 0


class TestRestockOrders:
    """Test suite for submitting and listing restocking orders."""

    def test_get_restock_orders_empty(self, client):
        """Test that no orders exist before any are submitted."""
        response = client.get("/api/restock-orders")
        assert response.status_code == 200
        assert response.json() == []

    def test_create_restock_order(self, client):
        """Test submitting a restocking order within budget."""
        candidates = client.get("/api/restock/candidates").json()[:2]
        payload = {
            "budget": 1_000_000,
            "items": [{"item_sku": c["item_sku"], "quantity": c["quantity"]} for c in candidates],
        }

        response = client.post("/api/restock-orders", json=payload)
        assert response.status_code == 201

        order = response.json()
        assert order["order_number"] == "RST-0001"
        assert order["status"] == "Submitted"
        assert len(order["items"]) == 2

        expected_total = sum(c["line_cost"] for c in candidates)
        assert abs(order["total_value"] - expected_total) < 0.01

        # The order arrives when its slowest item does
        assert order["lead_time_days"] == max(c["lead_time_days"] for c in candidates)
        submitted = datetime.fromisoformat(order["submitted_date"])
        expected = datetime.fromisoformat(order["expected_delivery"])
        assert expected - submitted == timedelta(days=order["lead_time_days"])

    def test_created_order_appears_in_list(self, client):
        """Test that submitted orders are listed newest first."""
        candidate = client.get("/api/restock/candidates").json()[0]
        item = {"item_sku": candidate["item_sku"], "quantity": 1}

        client.post("/api/restock-orders", json={"budget": 1_000_000, "items": [item]})
        client.post("/api/restock-orders", json={"budget": 1_000_000, "items": [item]})

        data = client.get("/api/restock-orders").json()
        assert [o["order_number"] for o in data] == ["RST-0002", "RST-0001"]

    def test_create_restock_order_uses_server_prices(self, client):
        """Test that line costs are computed from server data, not the request."""
        forecast = client.get("/api/demand").json()[0]
        payload = {
            "budget": 1_000_000,
            "items": [{"item_sku": forecast["item_sku"], "quantity": 10, "unit_cost": 0.01}],
        }

        order = client.post("/api/restock-orders", json=payload).json()
        assert order["items"][0]["unit_cost"] == forecast["unit_cost"]
        assert abs(order["total_value"] - 10 * forecast["unit_cost"]) < 0.01

    def test_create_restock_order_over_budget(self, client):
        """Test that an order exceeding the budget is rejected."""
        candidate = client.get("/api/restock/candidates").json()[0]
        payload = {
            "budget": candidate["line_cost"] - 1,
            "items": [{"item_sku": candidate["item_sku"], "quantity": candidate["quantity"]}],
        }

        response = client.post("/api/restock-orders", json=payload)
        assert response.status_code == 400
        assert "budget" in response.json()["detail"].lower()
        assert client.get("/api/restock-orders").json() == []

    def test_create_restock_order_empty_items(self, client):
        """Test that an order without items is rejected."""
        response = client.post("/api/restock-orders", json={"budget": 1000, "items": []})
        assert response.status_code == 400

    def test_create_restock_order_unknown_sku(self, client):
        """Test that an unknown SKU is rejected."""
        payload = {"budget": 1000, "items": [{"item_sku": "NOPE-999", "quantity": 1}]}
        response = client.post("/api/restock-orders", json=payload)
        assert response.status_code == 400
        assert "NOPE-999" in response.json()["detail"]

    @pytest.mark.parametrize("quantity", [0, -5])
    def test_create_restock_order_non_positive_quantity(self, client, quantity):
        """Test that zero or negative quantities are rejected."""
        sku = client.get("/api/demand").json()[0]["item_sku"]
        payload = {"budget": 1000, "items": [{"item_sku": sku, "quantity": quantity}]}
        response = client.post("/api/restock-orders", json=payload)
        assert response.status_code == 400

    def test_create_restock_order_missing_budget(self, client):
        """Test that a request without a budget fails validation."""
        response = client.post("/api/restock-orders", json={"items": []})
        assert response.status_code == 422

# Sample data — written to DynamoDB by the setup cell below.
# Also imported locally during seeding; not bundled into Lambda zips at runtime.

CUSTOMERS = {
    "CUST-001": {"customer_id": "CUST-001", "name": "Jane Smith",  "email": "jane@example.com",  "phone": "+15550001001"},
    "CUST-002": {"customer_id": "CUST-002", "name": "Bob Jones",   "email": "bob@example.com",   "phone": "+15550001002"},
    "CUST-003": {"customer_id": "CUST-003", "name": "Alice Chen",  "email": "alice@example.com", "phone": "+15550001003"},
    "CUST-004": {"customer_id": "CUST-004", "name": "Dave Kim",    "email": "dave@example.com",  "phone": "+15550001004"},
}

ORDERS = {
    "ORD-1001": {
        "order_id": "ORD-1001", "customer_id": "CUST-001", "status": "PROCESSING",
        "items": [{"sku": "WIDGET-42", "qty": 2, "price": 29.99},
                  {"sku": "GADGET-7",  "qty": 1, "price": 0.00}],
        "total": 59.98,
        "shipping_address": {"street": "123 Main St", "city": "Seattle",       "state": "WA", "zip": "98101"},
        "created_at": "2026-03-15T10:00:00Z", "payment_id": "PAY-001",
    },
    "ORD-1002": {
        "order_id": "ORD-1002", "customer_id": "CUST-002", "status": "SHIPPED",
        "items": [{"sku": "GADGET-7", "qty": 1, "price": 99.00}],
        "total": 99.00,
        "shipping_address": {"street": "456 Oak Ave", "city": "Portland",      "state": "OR", "zip": "97201"},
        "created_at": "2026-03-14T09:30:00Z", "payment_id": "PAY-002",
    },
    "ORD-1003": {
        "order_id": "ORD-1003", "customer_id": "CUST-003", "status": "DELIVERED",
        "items": [{"sku": "DOOHICKEY-9", "qty": 3, "price": 49.99}],
        "total": 149.97,
        "shipping_address": {"street": "789 Pine Rd", "city": "San Francisco", "state": "CA", "zip": "94101"},
        "created_at": "2026-03-10T14:00:00Z", "payment_id": "PAY-003",
    },
    "ORD-1004": {
        "order_id": "ORD-1004", "customer_id": "CUST-004", "status": "PENDING",
        "items": [{"sku": "WIDGET-42", "qty": 1, "price": 29.99}],
        "total": 29.99,
        "shipping_address": {"street": "321 Elm St",  "city": "Austin",        "state": "TX", "zip": "78701"},
        "created_at": "2026-03-18T08:00:00Z", "payment_id": "PAY-004",
    },
    "ORD-1005": {
        "order_id": "ORD-1005", "customer_id": "CUST-001", "status": "CANCELLED",
        "items": [{"sku": "GADGET-7", "qty": 1, "price": 49.99}],
        "total": 49.99,
        "shipping_address": {"street": "123 Main St", "city": "Seattle",       "state": "WA", "zip": "98101"},
        "created_at": "2026-03-12T11:00:00Z", "payment_id": "PAY-005",
    },
}

PAYMENTS = {
    "PAY-001": {"payment_id": "PAY-001", "order_id": "ORD-1001", "amount": 59.98,  "status": "CAPTURED",  "gateway": "stripe"},
    "PAY-002": {"payment_id": "PAY-002", "order_id": "ORD-1002", "amount": 99.00,  "status": "CAPTURED",  "gateway": "stripe"},
    "PAY-003": {"payment_id": "PAY-003", "order_id": "ORD-1003", "amount": 149.97, "status": "CAPTURED",  "gateway": "braintree"},
    "PAY-004": {"payment_id": "PAY-004", "order_id": "ORD-1004", "amount": 29.99,  "status": "PENDING",   "gateway": "stripe"},
    "PAY-005": {"payment_id": "PAY-005", "order_id": "ORD-1005", "amount": 49.99,  "status": "REFUNDED",  "gateway": "stripe"},
}

INVENTORY = {
    "WIDGET-42":   {"sku": "WIDGET-42",   "name": "Widget Pro 42",     "stock": 150, "reserved": 3, "warehouse": "WH-WEST"},
    "GADGET-7":    {"sku": "GADGET-7",    "name": "Gadget Series 7",   "stock": 45,  "reserved": 1, "warehouse": "WH-EAST"},
    "GIZMO-3":     {"sku": "GIZMO-3",     "name": "Gizmo v3",          "stock": 0,   "reserved": 0, "warehouse": "WH-WEST"},
    "DOOHICKEY-9": {"sku": "DOOHICKEY-9", "name": "Doohickey Mark IX", "stock": 200, "reserved": 3, "warehouse": "WH-CENTRAL"},
}

SHIPMENTS = {
    "SHIP-001": {
        "shipment_id": "SHIP-001", "order_id": "ORD-1002", "carrier": "UPS",
        "tracking_number": "1Z999AA10123456784", "status": "IN_TRANSIT",
        "estimated_delivery": "2026-03-20", "last_update": "2026-03-17T18:00:00Z",
    },
    "SHIP-002": {
        "shipment_id": "SHIP-002", "order_id": "ORD-1003", "carrier": "FedEx",
        "tracking_number": "7489023480237", "status": "DELIVERED",
        "estimated_delivery": "2026-03-13", "last_update": "2026-03-13T14:22:00Z",
    },
}

EMAIL_TEMPLATES = {
    "order_confirmation": {
        "template_id": "order_confirmation",
        "subject": "Order {order_id} Confirmed",
        "body":    "Dear {customer_name}, your order {order_id} has been confirmed. Total: ${total}. Thank you!",
    },
    "order_shipped": {
        "template_id": "order_shipped",
        "subject": "Order {order_id} Has Shipped",
        "body":    "Dear {customer_name}, your order {order_id} is on its way! Tracking: {tracking_number}.",
    },
    "refund_issued": {
        "template_id": "refund_issued",
        "subject": "Refund Issued for Order {order_id}",
        "body":    "Dear {customer_name}, a refund of ${amount} has been issued for order {order_id}.",
    },
}
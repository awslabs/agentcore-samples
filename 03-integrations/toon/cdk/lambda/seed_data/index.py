"""
Custom Resource Lambda for seeding mock customer data into DynamoDB.
"""

import boto3
import random
import uuid
from datetime import datetime, timedelta
from decimal import Decimal


# Sample data pools
FIRST_NAMES = [
    "James",
    "Mary",
    "Robert",
    "Patricia",
    "John",
    "Jennifer",
    "Michael",
    "Linda",
    "David",
    "Elizabeth",
    "William",
    "Barbara",
    "Richard",
    "Susan",
    "Joseph",
    "Jessica",
    "Thomas",
    "Sarah",
    "Christopher",
    "Karen",
    "Charles",
    "Lisa",
    "Daniel",
    "Nancy",
    "Matthew",
    "Betty",
    "Anthony",
    "Margaret",
    "Mark",
    "Sandra",
    "Donald",
    "Ashley",
    "Steven",
    "Kimberly",
    "Paul",
    "Emily",
    "Andrew",
    "Donna",
    "Joshua",
    "Michelle",
    "Kenneth",
    "Dorothy",
    "Kevin",
    "Carol",
    "Brian",
    "Amanda",
    "George",
    "Melissa",
    "Timothy",
    "Deborah",
    "Ronald",
    "Stephanie",
    "Edward",
    "Rebecca",
    "Jason",
    "Sharon",
    "Jeffrey",
    "Laura",
    "Ryan",
    "Cynthia",
    "Jacob",
    "Kathleen",
    "Gary",
    "Amy",
]

LAST_NAMES = [
    "Smith",
    "Johnson",
    "Williams",
    "Brown",
    "Jones",
    "Garcia",
    "Miller",
    "Davis",
    "Rodriguez",
    "Martinez",
    "Hernandez",
    "Lopez",
    "Gonzalez",
    "Wilson",
    "Anderson",
    "Thomas",
    "Taylor",
    "Moore",
    "Jackson",
    "Martin",
    "Lee",
    "Perez",
    "Thompson",
    "White",
    "Harris",
    "Sanchez",
    "Clark",
    "Ramirez",
    "Lewis",
    "Robinson",
    "Walker",
    "Young",
    "Allen",
    "King",
    "Wright",
    "Scott",
    "Torres",
    "Nguyen",
    "Hill",
    "Flores",
    "Green",
    "Adams",
    "Nelson",
    "Baker",
    "Hall",
    "Rivera",
    "Campbell",
    "Mitchell",
    "Carter",
    "Roberts",
    "Gomez",
    "Phillips",
    "Evans",
    "Turner",
    "Diaz",
    "Parker",
]

STREET_NAMES = [
    "Main Street",
    "Oak Avenue",
    "Maple Drive",
    "Cedar Lane",
    "Pine Road",
    "Elm Street",
    "Washington Boulevard",
    "Park Avenue",
    "Lake Drive",
    "River Road",
    "Highland Avenue",
    "Sunset Boulevard",
    "Forest Lane",
    "Mountain View Drive",
    "Valley Road",
    "Meadow Lane",
    "Spring Street",
    "Harbor Drive",
    "Ocean Avenue",
    "Beach Boulevard",
    "Garden Way",
    "Hillside Drive",
    "Orchard Lane",
    "Willow Street",
]

CITIES = [
    ("New York", "NY", "10001", "Northeast"),
    ("Los Angeles", "CA", "90001", "West"),
    ("Chicago", "IL", "60601", "Midwest"),
    ("Houston", "TX", "77001", "South"),
    ("Phoenix", "AZ", "85001", "Southwest"),
    ("Philadelphia", "PA", "19101", "Northeast"),
    ("San Antonio", "TX", "78201", "South"),
    ("San Diego", "CA", "92101", "West"),
    ("Dallas", "TX", "75201", "South"),
    ("San Jose", "CA", "95101", "West"),
    ("Austin", "TX", "78701", "South"),
    ("Jacksonville", "FL", "32099", "Southeast"),
    ("Fort Worth", "TX", "76101", "South"),
    ("Columbus", "OH", "43085", "Midwest"),
    ("Charlotte", "NC", "28201", "Southeast"),
    ("San Francisco", "CA", "94102", "West"),
    ("Indianapolis", "IN", "46201", "Midwest"),
    ("Seattle", "WA", "98101", "Northwest"),
    ("Denver", "CO", "80201", "Mountain"),
    ("Boston", "MA", "02101", "Northeast"),
    ("Nashville", "TN", "37201", "South"),
    ("Portland", "OR", "97201", "Northwest"),
    ("Las Vegas", "NV", "89101", "Southwest"),
    ("Atlanta", "GA", "30301", "Southeast"),
    ("Miami", "FL", "33101", "Southeast"),
]

SUBSCRIPTION_TIERS = ["Free", "Basic", "Standard", "Premium", "Enterprise"]
ACCOUNT_STATUSES = [
    "Active",
    "Inactive",
    "Suspended",
    "Pending Verification",
    "Churned",
]
PAYMENT_METHODS = [
    "Visa ending in 4242",
    "Mastercard ending in 5555",
    "American Express ending in 3782",
    "Discover ending in 6011",
    "PayPal",
    "Apple Pay",
    "Google Pay",
    "Bank Transfer",
    "Debit Card ending in 1234",
    "Corporate Card ending in 9999",
]
REFERRAL_SOURCES = [
    "Google Search",
    "Facebook Ads",
    "Instagram",
    "Twitter/X",
    "LinkedIn",
    "Friend Referral",
    "Email Campaign",
    "Trade Show",
    "Podcast Sponsorship",
    "YouTube Advertisement",
    "TikTok",
    "Reddit",
    "Affiliate Partner",
    "Industry Blog",
    "Direct Traffic",
    "Bing Search",
    "Press Coverage",
    "Conference Presentation",
    "Webinar",
    "Word of Mouth",
]
GENDERS = ["Male", "Female", "Non-binary", "Prefer not to say"]

CUSTOMER_NOTES_TEMPLATES = [
    "Loyal customer since {year}. Prefers email communication for order updates.",
    "High-value customer with consistent monthly purchases. Enrolled in VIP program.",
    "Requested callback regarding enterprise pricing options. Follow up by end of week.",
    "Customer expressed interest in bulk ordering. Send promotional materials.",
    "Previous support ticket resolved satisfactorily. Customer satisfaction rating: 5/5.",
    "Participated in beta testing program for new features. Provided valuable feedback.",
    "Referred 3 new customers this quarter. Eligible for referral bonus credit.",
    "Prefers phone support over chat. Best contact time: weekday afternoons.",
    "Downgraded from Premium to Standard tier. Consider retention offer.",
    "Recently upgraded subscription. Schedule onboarding call within 48 hours.",
]


def generate_phone_number():
    area_code = random.randint(200, 999)
    exchange = random.randint(200, 999)
    subscriber = random.randint(1000, 9999)
    return f"+1-{area_code}-{exchange}-{subscriber}"


def generate_date_of_birth():
    today = datetime.now()
    age = random.randint(18, 80)
    birth_year = today.year - age
    birth_month = random.randint(1, 12)
    birth_day = random.randint(1, 28)
    return f"{birth_year}-{birth_month:02d}-{birth_day:02d}"


def generate_created_at():
    days_ago = random.randint(1, 1825)
    created_date = datetime.now() - timedelta(days=days_ago)
    return created_date.strftime("%Y-%m-%dT%H:%M:%SZ")


def generate_last_login():
    days_ago = random.randint(0, 90)
    hours_ago = random.randint(0, 23)
    login_date = datetime.now() - timedelta(days=days_ago, hours=hours_ago)
    return login_date.strftime("%Y-%m-%dT%H:%M:%SZ")


def generate_monthly_spend(tier):
    spend_ranges = {
        "Free": (0, 50),
        "Basic": (25, 150),
        "Standard": (100, 500),
        "Premium": (400, 2000),
        "Enterprise": (1500, 15000),
    }
    min_spend, max_spend = spend_ranges.get(tier, (0, 100))
    return Decimal(str(round(random.uniform(min_spend, max_spend), 2)))


def generate_customer_record():
    customer_id = str(uuid.uuid4())
    first_name = random.choice(FIRST_NAMES)
    last_name = random.choice(LAST_NAMES)
    domain = (
        "gmail.com"
        if random.random() > 0.3
        else random.choice(
            ["yahoo.com", "outlook.com", "hotmail.com", "icloud.com", "protonmail.com"]
        )
    )
    email = f"{first_name.lower()}.{last_name.lower()}{random.randint(1, 999)}@{domain}"

    city_data = random.choice(CITIES)
    city, state, zip_code, region = city_data

    subscription_tier = random.choice(SUBSCRIPTION_TIERS)
    created_at = generate_created_at()
    created_year = created_at[:4]

    return {
        "customer_id": customer_id,
        "email": email,
        "first_name": first_name,
        "last_name": last_name,
        "full_name": f"{first_name} {last_name}",
        "phone_number": generate_phone_number(),
        "date_of_birth": generate_date_of_birth(),
        "gender": random.choice(GENDERS),
        "address_street": f"{random.randint(100, 9999)} {random.choice(STREET_NAMES)}",
        "address_city": city,
        "address_state": state,
        "address_zip_code": zip_code,
        "address_country": "United States",
        "region": region,
        "subscription_tier": subscription_tier,
        "monthly_spend": generate_monthly_spend(subscription_tier),
        "total_lifetime_value": Decimal(str(round(random.uniform(100, 50000), 2))),
        "account_status": random.choice(ACCOUNT_STATUSES),
        "created_at": created_at,
        "last_login_at": generate_last_login(),
        "total_orders": random.randint(0, 250),
        "loyalty_points": random.randint(0, 50000),
        "average_order_value": Decimal(str(round(random.uniform(25, 500), 2))),
        "support_tickets_opened": random.randint(0, 15),
        "preferred_payment_method": random.choice(PAYMENT_METHODS),
        "marketing_opt_in": random.choice([True, False]),
        "sms_notifications_enabled": random.choice([True, False]),
        "referral_source": random.choice(REFERRAL_SOURCES),
        "customer_notes": random.choice(CUSTOMER_NOTES_TEMPLATES).format(
            year=created_year
        ),
        "tags": random.sample(
            [
                "vip",
                "new",
                "at-risk",
                "high-value",
                "enterprise",
                "seasonal",
                "referrer",
                "beta-tester",
                "early-adopter",
                "loyalty-member",
            ],
            k=random.randint(1, 4),
        ),
        "last_modified_by": random.choice(
            ["system", "admin", "support-agent", "automated-process"]
        ),
    }


def lambda_handler(event, context):
    """Custom Resource handler for seeding DynamoDB."""
    import os

    request_type = event["RequestType"]
    properties = event["ResourceProperties"]

    table_name = properties.get("TableName") or os.environ.get(
        "TABLE_NAME", "Customers"
    )
    num_records = int(properties.get("NumRecords", 120))

    if request_type == "Create":
        return seed_data(table_name, num_records)
    elif request_type == "Update":
        # On update, we could re-seed or skip - skipping to avoid duplicates
        print("Update requested - skipping to avoid duplicate data")
        return {"PhysicalResourceId": f"seed-{table_name}"}
    elif request_type == "Delete":
        # Optionally clear data on delete - skipping for safety
        print("Delete requested - data will remain in table")
        return {"PhysicalResourceId": f"seed-{table_name}"}

    return {"PhysicalResourceId": f"seed-{table_name}"}


def seed_data(table_name, num_records):
    """Seed the DynamoDB table with mock data."""
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(table_name)

    print(f"Seeding {num_records} records into '{table_name}'...")

    successful = 0
    failed = 0

    with table.batch_writer() as batch:
        for i in range(num_records):
            try:
                customer = generate_customer_record()
                batch.put_item(Item=customer)
                successful += 1
            except Exception as e:
                failed += 1
                print(f"Error inserting record {i + 1}: {str(e)}")

    print(f"Seeding complete: {successful} successful, {failed} failed")

    return {
        "PhysicalResourceId": f"seed-{table_name}",
        "Data": {
            "RecordsInserted": str(successful),
            "RecordsFailed": str(failed),
        },
    }

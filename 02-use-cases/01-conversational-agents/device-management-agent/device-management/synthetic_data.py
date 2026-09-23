"""
Device Management System - Synthetic Data Generator

This module generates realistic synthetic test data for the Device Management
System, populating DynamoDB tables with devices, users, WiFi networks, device
settings, and user activities. It ensures minimum data requirements are met
for comprehensive testing and demonstration.

The generator creates:
- 25+ IoT devices with realistic configurations
- 25+ device settings per device (600+ total settings)
- 25+ WiFi networks across devices
- 25+ user accounts with varied roles
- 625+ user activity records (25 per user)

Key Features:
    - Realistic device models (TransPort routers, IX/EX series)
    - Comprehensive device settings (cellular, network, system, security)
    - Varied WiFi configurations (security types, channels, signal strength)
    - Diverse user roles (admin, operator, viewer, technician, etc.)
    - Rich activity history (login, config changes, firmware updates)
    - Timestamp generation for historical data
    - Unique email addresses to prevent conflicts
    - ISO format datetime strings for DynamoDB compatibility

Data Generation Details:

    Devices (25+):
    - Device IDs: DG-100001 to DG-100025
    - Models: TransPort WR31/WR44, IX20/IX15, EX15/EX12, WR54/WR64
    - Firmware versions: 5.x.x format
    - Connection statuses: Connected, Disconnected, Dormant, Maintenance, Updating
    - IP addresses: 192.168.x.x range
    - MAC addresses: 00:40:9D:xx:xx:xx format
    - Last connected: Within past 72 hours

    Device Settings (25 per device):
    - Cellular: APN, auth type, SIM PIN, band selection, roaming
    - Network: Hostname, IP mode, DNS, gateway, MTU, IPv6
    - System: Contact, location, description, timezone, NTP, log level
    - Firewall: Enabled status, default policy
    - WiFi: Channel, country, regulatory domain
    - Security: SSH, HTTPS, password complexity

    WiFi Networks (25+ total):
    - Network IDs: wifi_1, wifi_2, etc.
    - SSIDs: Device-Net-{device_id}-{number}
    - Security types: WPA2-PSK, WPA3-PSK, Open, WPA-PSK, WEP, Enterprise
    - Channels: 1, 6, 11 (2.4GHz), 36-161 (5GHz)
    - Signal strength: -90 to -30 dBm
    - Enabled status: Mostly enabled (80% probability)

    Users (25+):
    - User IDs: USR100001 to USR100025
    - Usernames: firstname.lastname{number}
    - Emails: Unique across multiple domains
    - Roles: admin, operator, viewer, device_admin, support, manager, etc.
    - Created dates: 30-365 days ago
    - Last login: 1-240 hours ago

    User Activities (625+ total, 25 per user):
    - Activity types: 30 different types including:
      * Authentication: login, logout, password_change
      * Device operations: config_change, firmware_update, reboot
      * User management: user_added, user_removed, permission_changed
      * System operations: backup, restore, report_generated
      * Network: VPN connected/disconnected, remote_access
      * Security: certificate operations, security_scan
    - Timestamps: Distributed over past 30 days
    - IP addresses: Varied source IPs
    - Descriptions: Context-specific details

Environment Variables:
    AWS_REGION: AWS region for DynamoDB (defaults to us-west-2)

DynamoDB Tables Populated:
    - Devices: Device inventory and status
    - DeviceSettings: Configuration settings per device
    - WifiNetworks: WiFi network configurations
    - Users: User accounts and profiles
    - UserActivities: Activity logs and audit trail

Usage:
    Run directly to generate data:
    >>> python synthetic_data.py

    Import and use programmatically:
    >>> from synthetic_data import generate_synthetic_data
    >>> generate_synthetic_data()

Output:
    Synthetic data generated successfully!
    - 25 devices created
    - 25 users created
    - 25 WiFi networks created
    - 625 device settings created
    - 625 user activities created

Data Characteristics:
    - All timestamps in ISO 8601 format
    - Decimal types for numeric values (DynamoDB compatibility)
    - Realistic value distributions (not purely random)
    - Referential integrity (activities reference real users/devices)
    - Varied data to test different query patterns

Notes:
    - Requires DynamoDB tables to be initialized first
    - Calls init_db() from dynamodb_models.py automatically
    - Uses boto3 for DynamoDB operations
    - Generates deterministic data (same run produces similar patterns)
    - Safe to run multiple times (creates new records)
    - Minimum 25 items per entity type guaranteed
    - Email uniqueness enforced with retry logic
"""

import datetime
import random
import uuid
from decimal import Decimal
import boto3

# Configure DynamoDB connection
# Always use AWS DynamoDB in us-west-2
aws_region = "us-west-2"

# Initialize DynamoDB resource
dynamodb = boto3.resource("dynamodb", region_name=aws_region)

# Define table names
DEVICES_TABLE = "Devices"
DEVICE_SETTINGS_TABLE = "DeviceSettings"
WIFI_NETWORKS_TABLE = "WifiNetworks"
USERS_TABLE = "Users"
USER_ACTIVITIES_TABLE = "UserActivities"


# Helper function to convert datetime to ISO format string
def datetime_to_iso(dt):
    if isinstance(dt, datetime.datetime):
        return dt.isoformat()
    return dt


# Device CRUD operations
def create_device(device_data):
    """Create a new device"""
    table = dynamodb.Table(DEVICES_TABLE)

    # Ensure device_id exists
    if "device_id" not in device_data:
        device_data["device_id"] = f"DG-{uuid.uuid4().hex[:8].upper()}"

    # Convert datetime objects to ISO format strings
    if "last_connected" in device_data and device_data["last_connected"]:
        device_data["last_connected"] = datetime_to_iso(device_data["last_connected"])

    table.put_item(Item=device_data)
    return device_data


# Device Settings CRUD operations
def create_device_setting(device_id, setting_key, setting_value, last_updated=None):
    """Create or update a device setting"""
    table = dynamodb.Table(DEVICE_SETTINGS_TABLE)

    if not last_updated:
        last_updated = datetime.datetime.utcnow()

    item = {
        "device_id": device_id,
        "setting_key": setting_key,
        "setting_value": setting_value,
        "last_updated": datetime_to_iso(last_updated),
    }

    table.put_item(Item=item)
    return item


# WiFi Network CRUD operations
def create_wifi_network(network_data):
    """Create a new WiFi network"""
    table = dynamodb.Table(WIFI_NETWORKS_TABLE)

    # Ensure required fields exist
    if "device_id" not in network_data:
        raise ValueError("device_id is required")

    if "network_id" not in network_data:
        network_data["network_id"] = f"wifi_{uuid.uuid4().hex[:8]}"

    # Convert datetime objects to ISO format strings
    if "last_updated" in network_data and network_data["last_updated"]:
        network_data["last_updated"] = datetime_to_iso(network_data["last_updated"])
    else:
        network_data["last_updated"] = datetime_to_iso(datetime.datetime.utcnow())

    # Convert float to Decimal for DynamoDB
    if "signal_strength" in network_data and network_data["signal_strength"] is not None:
        network_data["signal_strength"] = Decimal(str(network_data["signal_strength"]))

    table.put_item(Item=network_data)
    return network_data


# User CRUD operations
def create_user(user_data):
    """Create a new user"""
    table = dynamodb.Table(USERS_TABLE)

    # Ensure user_id exists
    if "user_id" not in user_data:
        user_data["user_id"] = f"USR{uuid.uuid4().hex[:8].upper()}"

    # Convert datetime objects to ISO format strings
    if "created_at" in user_data and user_data["created_at"]:
        user_data["created_at"] = datetime_to_iso(user_data["created_at"])
    else:
        user_data["created_at"] = datetime_to_iso(datetime.datetime.utcnow())

    if "last_login" in user_data and user_data["last_login"]:
        user_data["last_login"] = datetime_to_iso(user_data["last_login"])

    table.put_item(Item=user_data)
    return user_data


# User Activity CRUD operations
def create_user_activity(user_id, activity_type, description=None, ip_address=None, timestamp=None):
    """Create a new user activity"""
    table = dynamodb.Table(USER_ACTIVITIES_TABLE)

    if not timestamp:
        timestamp = datetime.datetime.utcnow()

    timestamp_str = datetime_to_iso(timestamp)

    item = {
        "user_id": user_id,
        "timestamp": timestamp_str,
        "activity_type": activity_type,
        "description": description,
        "ip_address": ip_address,
    }

    table.put_item(Item=item)
    return item


def generate_synthetic_data():
    """Generate synthetic data for testing with DynamoDB"""
    # Import and initialize database
    from dynamodb_models import init_db

    init_db()

    # Generate devices (at least 25)
    devices = []
    for i in range(1, 26):
        device_id = f"DG-{100000 + i}"
        device_data = {
            "device_id": device_id,
            "name": f"Device Router {i}",
            "model": random.choice(
                [
                    "TransPort WR31",
                    "TransPort WR44",
                    "IX20",
                    "EX15",
                    "IX15",
                    "EX12",
                    "WR54",
                    "WR64",
                ]
            ),
            "firmware_version": f"5.{random.randint(1, 9)}.{random.randint(1, 20)}",
            "connection_status": random.choice(["Connected", "Disconnected", "Dormant", "Maintenance", "Updating"]),
            "ip_address": f"192.168.{random.randint(1, 5)}.{random.randint(2, 254)}",
            "mac_address": f"00:40:9D:{random.randint(10, 99)}:{random.randint(10, 99)}:{random.randint(10, 99)}",
            "last_connected": datetime.datetime.now() - datetime.timedelta(hours=random.randint(0, 72)),
        }
        device = create_device(device_data)
        devices.append(device)

    # Generate device settings (multiple per device)
    setting_keys = [
        "cellular.apn",
        "cellular.auth_type",
        "cellular.sim_pin",
        "network.hostname",
        "network.ip_mode",
        "network.dns_primary",
        "system.contact",
        "system.location",
        "system.description",
        "firewall.enabled",
        "firewall.default_policy",
        "wifi.channel",
        "wifi.country",
        "wifi.regulatory_domain",
        "system.timezone",
        "system.ntp_server",
        "system.log_level",
        "network.ipv6_enabled",
        "network.mtu",
        "network.gateway",
        "security.ssh_enabled",
        "security.https_enabled",
        "security.password_complexity",
        "cellular.band_selection",
        "cellular.roaming_allowed",
    ]

    device_settings_count = 0
    for device in devices:
        # Ensure each device has at least one setting for each key
        for key in setting_keys:
            if "cellular.apn" in key:
                value = random.choice(
                    [
                        "internet",
                        "broadband",
                        "iot.secure",
                        "m2m.provider.com",
                        "wireless.data",
                    ]
                )
            elif "auth_type" in key:
                value = random.choice(["none", "pap", "chap", "auto"])
            elif "sim_pin" in key:
                value = str(random.randint(1000, 9999))
            elif "hostname" in key:
                value = f"device-router-{device['device_id'].lower()}"
            elif "ip_mode" in key:
                value = random.choice(["dhcp", "static", "auto"])
            elif "dns" in key:
                value = random.choice(
                    [
                        "8.8.8.8",
                        "8.8.4.4",
                        "1.1.1.1",
                        "9.9.9.9",
                        f"192.168.{random.randint(1, 5)}.1",
                    ]
                )
            elif "contact" in key:
                value = f"admin-{random.randint(1, 10)}@example.com"
            elif "location" in key:
                value = random.choice(
                    [
                        "Server Room",
                        "Office",
                        "Remote Site",
                        "Branch Office",
                        "Warehouse",
                        "Data Center",
                        "Manufacturing Floor",
                        "Retail Store",
                        "Distribution Center",
                        "Field Site",
                    ]
                )
            elif "description" in key:
                value = f"Device Router for {random.choice(['Primary', 'Backup', 'IoT', 'Monitoring', 'Security', 'Guest', 'VPN', 'Emergency'])} connectivity"
            elif "firewall.enabled" in key:
                value = random.choice(["true", "false"])
            elif "default_policy" in key:
                value = random.choice(["accept", "drop", "reject"])
            elif "wifi.channel" in key:
                value = str(random.choice([1, 6, 11, 36, 40, 44, 48, 149, 153, 157, 161]))
            elif "wifi.country" in key:
                value = random.choice(["US", "CA", "GB", "DE", "FR", "JP", "AU", "NZ", "SG", "IN"])
            elif "regulatory_domain" in key:
                value = random.choice(["FCC", "ETSI", "TELEC", "KCC", "SRRC"])
            elif "timezone" in key:
                value = random.choice(
                    [
                        "UTC",
                        "America/New_York",
                        "Europe/London",
                        "Asia/Tokyo",
                        "Australia/Sydney",
                        "Pacific/Auckland",
                    ]
                )
            elif "ntp_server" in key:
                value = random.choice(
                    [
                        "pool.ntp.org",
                        "time.google.com",
                        "time.windows.com",
                        "time.apple.com",
                        "ntp.ubuntu.com",
                    ]
                )
            elif "log_level" in key:
                value = random.choice(["debug", "info", "warning", "error", "critical"])
            elif "ipv6_enabled" in key:
                value = random.choice(["true", "false"])
            elif "mtu" in key:
                value = str(random.choice([1500, 1492, 1480, 1450, 1400]))
            elif "gateway" in key:
                value = f"192.168.{random.randint(1, 5)}.1"
            elif "ssh_enabled" in key:
                value = random.choice(["true", "false"])
            elif "https_enabled" in key:
                value = random.choice(["true", "false"])
            elif "password_complexity" in key:
                value = random.choice(["low", "medium", "high", "extreme"])
            elif "band_selection" in key:
                value = random.choice(["auto", "4g_only", "5g_only", "4g_preferred", "5g_preferred"])
            elif "roaming_allowed" in key:
                value = random.choice(["true", "false"])
            else:
                value = f"value-{random.randint(1, 100)}"

            last_updated = datetime.datetime.now() - datetime.timedelta(days=random.randint(0, 30))
            create_device_setting(device["device_id"], key, value, last_updated)
            device_settings_count += 1

    # Generate WiFi networks (at least 25 total)
    wifi_count = 0
    for device in devices:
        # Ensure we have at least 25 WiFi networks total
        networks_per_device = max(1, 25 // len(devices))
        if wifi_count < 25 and device["device_id"] == devices[-1]["device_id"]:
            # Add remaining networks to the last device to ensure we have at least 25
            networks_per_device = max(networks_per_device, 25 - wifi_count)

        for i in range(1, networks_per_device + 1):
            network_data = {
                "device_id": device["device_id"],
                "network_id": f"wifi_{i}",
                "ssid": f"Device-Net-{device['device_id']}-{i}",
                "security_type": random.choice(["wpa2-psk", "wpa3-psk", "open", "wpa-psk", "wep", "enterprise"]),
                "enabled": random.choice([True, True, False]),  # More likely to be enabled
                "channel": random.choice([1, 6, 11, 36, 40, 44, 48, 149, 153, 157, 161]),
                "signal_strength": random.uniform(-90.0, -30.0),
                "last_updated": datetime.datetime.now() - datetime.timedelta(days=random.randint(0, 30)),
            }
            create_wifi_network(network_data)
            wifi_count += 1

    # Generate users (at least 25)
    users = []
    roles = [
        "admin",
        "operator",
        "viewer",
        "device_admin",
        "support",
        "manager",
        "auditor",
        "guest",
        "technician",
        "developer",
    ]
    domains = [
        "example.com",
        "company.net",
        "device-test.org",
        "enterprise.co",
        "tech-corp.io",
    ]

    first_names = [
        "John",
        "Jane",
        "Robert",
        "Lisa",
        "Michael",
        "Sarah",
        "David",
        "Emma",
        "Chris",
        "Olivia",
        "William",
        "Sophia",
        "James",
        "Ava",
        "Benjamin",
        "Mia",
        "Daniel",
        "Charlotte",
        "Matthew",
        "Amelia",
        "Joseph",
        "Harper",
        "Andrew",
        "Evelyn",
        "Joshua",
        "Abigail",
        "Nicholas",
        "Emily",
        "Ryan",
        "Elizabeth",
    ]

    last_names = [
        "Smith",
        "Johnson",
        "Williams",
        "Brown",
        "Jones",
        "Miller",
        "Davis",
        "Garcia",
        "Wilson",
        "Taylor",
        "Anderson",
        "Thomas",
        "Jackson",
        "White",
        "Harris",
        "Martin",
        "Thompson",
        "Moore",
        "Young",
        "Allen",
        "King",
        "Wright",
        "Scott",
        "Green",
        "Baker",
        "Adams",
        "Nelson",
        "Hill",
        "Ramirez",
        "Campbell",
    ]

    # Create a set to track used email addresses
    used_emails = set()

    for i in range(1, 26):
        # Keep generating until we get a unique email
        while True:
            first_name = random.choice(first_names)
            last_name = random.choice(last_names)
            domain = random.choice(domains)
            email = f"{first_name.lower()}.{last_name.lower()}{i}@{domain}"

            if email not in used_emails:
                used_emails.add(email)
                break

        user_data = {
            "user_id": f"USR{100000 + i}",
            "username": f"{first_name.lower()}.{last_name.lower()}{i}",
            "email": email,
            "first_name": first_name,
            "last_name": last_name,
            "role": random.choice(roles),
            "created_at": datetime.datetime.now() - datetime.timedelta(days=random.randint(30, 365)),
            "last_login": datetime.datetime.now() - datetime.timedelta(hours=random.randint(1, 240)),
        }
        user = create_user(user_data)
        users.append(user)

    # Generate user activities (at least 25 per user)
    activity_types = [
        "login",
        "logout",
        "device_config_change",
        "firmware_update",
        "user_added",
        "user_removed",
        "report_generated",
        "alert_acknowledged",
        "device_reboot",
        "password_change",
        "vpn_connected",
        "vpn_disconnected",
        "file_upload",
        "file_download",
        "settings_export",
        "settings_import",
        "group_created",
        "group_modified",
        "permission_changed",
        "api_key_created",
        "api_key_revoked",
        "schedule_created",
        "schedule_modified",
        "backup_created",
        "restore_performed",
        "certificate_installed",
        "certificate_expired",
        "security_scan",
        "remote_access_enabled",
        "remote_access_disabled",
    ]

    user_activities_count = 0
    for user in users:
        # Generate at least 25 activities per user
        for _ in range(25):
            activity_type = random.choice(activity_types)

            if activity_type == "login":
                description = f"User logged in from {random.choice(['web interface', 'mobile app', 'API', 'CLI', 'desktop application'])}"
            elif activity_type == "logout":
                description = "User logged out"
            elif activity_type == "device_config_change":
                device = random.choice(devices)
                setting = random.choice(setting_keys)
                description = f"Changed {setting} on device {device['name']}"
            elif activity_type == "firmware_update":
                device = random.choice(devices)
                old_version = f"5.{random.randint(1, 5)}.{random.randint(1, 10)}"
                new_version = f"5.{random.randint(6, 9)}.{random.randint(1, 20)}"
                description = f"Updated firmware on {device['name']} from {old_version} to {new_version}"
            elif activity_type == "user_added":
                description = f"Added new user {random.choice(first_names).lower()}.{random.choice(last_names).lower()}"
            elif activity_type == "user_removed":
                description = f"Removed user {random.choice(first_names).lower()}.{random.choice(last_names).lower()}"
            elif activity_type == "report_generated":
                report_type = random.choice(
                    [
                        "usage",
                        "security",
                        "connectivity",
                        "performance",
                        "compliance",
                        "audit",
                        "inventory",
                    ]
                )
                description = f"Generated {report_type} report"
            elif activity_type == "alert_acknowledged":
                alert_id = f"ALT-{random.randint(10000, 99999)}"
                description = f"Acknowledged alert {alert_id}"
            elif activity_type == "device_reboot":
                device = random.choice(devices)
                description = f"Initiated reboot of device {device['name']}"
            elif activity_type == "password_change":
                description = "Changed account password"
            elif activity_type == "vpn_connected":
                description = f"Connected to VPN from IP {random.randint(1, 255)}.{random.randint(1, 255)}.{random.randint(1, 255)}.{random.randint(1, 255)}"
            elif activity_type == "vpn_disconnected":
                description = "Disconnected from VPN"
            elif activity_type == "file_upload":
                file_type = random.choice(["configuration", "firmware", "certificate", "log", "backup"])
                description = f"Uploaded {file_type} file"
            elif activity_type == "file_download":
                file_type = random.choice(["configuration", "report", "log", "backup", "template"])
                description = f"Downloaded {file_type} file"
            elif activity_type == "settings_export":
                device = random.choice(devices)
                description = f"Exported settings from device {device['name']}"
            elif activity_type == "settings_import":
                device = random.choice(devices)
                description = f"Imported settings to device {device['name']}"
            elif activity_type == "group_created":
                description = "Created device group '{}'".format(
                    random.choice(
                        [
                            "Production",
                            "Testing",
                            "Development",
                            "Backup",
                            "Remote",
                            "Office",
                            "Warehouse",
                        ]
                    )
                )
            elif activity_type == "group_modified":
                description = f"Modified device group '{random.choice(['Production', 'Testing', 'Development', 'Backup', 'Remote', 'Office', 'Warehouse'])}'"
            elif activity_type == "permission_changed":
                target_user = random.choice(users)
                description = f"Changed permissions for user {target_user['username']}"
            elif activity_type == "api_key_created":
                description = "Created new API key"
            elif activity_type == "api_key_revoked":
                description = "Revoked API key"
            elif activity_type == "schedule_created":
                task = random.choice(
                    [
                        "backup",
                        "reboot",
                        "firmware update",
                        "report generation",
                        "maintenance",
                    ]
                )
                description = f"Created schedule for {task}"
            elif activity_type == "schedule_modified":
                task = random.choice(
                    [
                        "backup",
                        "reboot",
                        "firmware update",
                        "report generation",
                        "maintenance",
                    ]
                )
                description = f"Modified schedule for {task}"
            elif activity_type == "backup_created":
                device = random.choice(devices)
                description = f"Created backup of device {device['name']}"
            elif activity_type == "restore_performed":
                device = random.choice(devices)
                description = f"Restored configuration to device {device['name']}"
            elif activity_type == "certificate_installed":
                device = random.choice(devices)
                cert_type = random.choice(["SSL", "SSH", "CA", "client"])
                description = f"Installed {cert_type} certificate on device {device['name']}"
            elif activity_type == "certificate_expired":
                device = random.choice(devices)
                cert_type = random.choice(["SSL", "SSH", "CA", "client"])
                description = f"{cert_type} certificate expired on device {device['name']}"
            elif activity_type == "security_scan":
                device = random.choice(devices)
                description = f"Performed security scan on device {device['name']}"
            elif activity_type == "remote_access_enabled":
                device = random.choice(devices)
                description = f"Enabled remote access for device {device['name']}"
            elif activity_type == "remote_access_disabled":
                device = random.choice(devices)
                description = f"Disabled remote access for device {device['name']}"
            else:
                description = f"Performed {activity_type}"

            # Generate a timestamp within the last 30 days
            timestamp = datetime.datetime.now() - datetime.timedelta(
                days=random.randint(0, 30),
                hours=random.randint(0, 23),
                minutes=random.randint(0, 59),
            )

            ip_address = (
                f"{random.randint(10, 203)}.{random.randint(0, 255)}.{random.randint(0, 255)}.{random.randint(1, 254)}"
            )

            create_user_activity(user["user_id"], activity_type, description, ip_address, timestamp)
            user_activities_count += 1

    print("Synthetic data generated successfully!")
    print(f"- {len(devices)} devices created")
    print(f"- {len(users)} users created")
    print(f"- {wifi_count} WiFi networks created")
    print(f"- {device_settings_count} device settings created")
    print(f"- {user_activities_count} user activities created")


if __name__ == "__main__":
    generate_synthetic_data()

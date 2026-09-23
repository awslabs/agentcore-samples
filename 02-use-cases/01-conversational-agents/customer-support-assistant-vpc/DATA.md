
## Database Schema

### Amazon Aurora PostgreSQL database

The Aurora PostgreSQL database contains three main tables that store user information, product catalog, and order history. These tables are linked to DynamoDB customer profiles via the `customer_id` field.

<details>
<summary><b>Users Table</b></summary>

| Column Name   | Data Type      | Constraints           | Description                                |
|---------------|----------------|-----------------------|--------------------------------------------|
| id            | SERIAL         | PRIMARY KEY           | Auto-incrementing user ID                  |
| customer_id   | VARCHAR(20)    | UNIQUE, NOT NULL      | Links to DynamoDB customer profiles        |
| username      | VARCHAR(50)    | UNIQUE, NOT NULL      | Unique username                            |
| email         | VARCHAR(100)   | UNIQUE, NOT NULL      | User email address                         |
| first_name    | VARCHAR(50)    |                       | User's first name                          |
| last_name     | VARCHAR(50)    |                       | User's last name                           |
| created_at    | TIMESTAMP      | DEFAULT CURRENT_TIMESTAMP | Account creation timestamp          |

**Sample Data:** 5 users (CUST001-CUST005) linked to DynamoDB profiles

</details>

<details>
<summary><b>Products Table</b></summary>

| Column Name     | Data Type      | Constraints           | Description                                |
|-----------------|----------------|-----------------------|--------------------------------------------|
| id              | SERIAL         | PRIMARY KEY           | Auto-incrementing product ID               |
| name            | VARCHAR(100)   | NOT NULL              | Product name                               |
| description     | TEXT           |                       | Product description                        |
| price           | DECIMAL(10,2)  |                       | Product price                              |
| category        | VARCHAR(50)    |                       | Product category                           |
| stock_quantity  | INTEGER        | DEFAULT 0             | Available inventory count                  |
| created_at      | TIMESTAMP      | DEFAULT CURRENT_TIMESTAMP | Product creation timestamp          |

**Sample Data:** 10 products including electronics (laptop, mouse, keyboard, webcam, USB cable) and office supplies (coffee mug, notebook, desk chair, monitor stand, water bottle)

</details>

<details>
<summary><b>Orders Table</b></summary>

| Column Name   | Data Type      | Constraints           | Description                                |
|---------------|----------------|-----------------------|--------------------------------------------|
| id            | SERIAL         | PRIMARY KEY           | Auto-incrementing order ID                 |
| customer_id   | VARCHAR(20)    | FOREIGN KEY → users(customer_id) | References customer             |
| total_amount  | DECIMAL(10,2)  |                       | Order total amount                         |
| status        | VARCHAR(20)    | DEFAULT 'pending'     | Order status (pending/completed/shipped)   |
| order_date    | TIMESTAMP      | DEFAULT CURRENT_TIMESTAMP | Order creation timestamp            |

**Sample Data:** 5 orders with various statuses across different customers

</details>


### Amazon DynamoDB tables (exposed by [DynamoDB MCP Server](./mcp_dynamodb/))

These DynamoDB tables are accessed through the MCP (Model Context Protocol) Server, providing product catalog and customer review data.

<details>
<summary><b>Reviews Table</b></summary>

**Primary Key:**
- `review_id` (String) - HASH

**Attributes:**

| Attribute Name    | Data Type | Description                              |
|-------------------|-----------|------------------------------------------|
| review_id         | String    | Unique review identifier                 |
| product_id        | Number    | Product being reviewed                   |
| customer_id       | String    | Customer who wrote the review            |
| rating            | Number    | Star rating (1-5)                        |
| title             | String    | Review title                             |
| comment           | String    | Review text                              |
| verified_purchase | Boolean   | Whether purchase was verified            |
| created_at        | String    | Review creation timestamp (ISO 8601)     |

**Sample Data:** 5 reviews for products (laptop, mouse, coffee mug, desk chair) with ratings 3-5 stars

</details>

<details>
<summary><b>Products Table</b></summary>

**Primary Key:**
- `product_id` (Number) - HASH

**Attributes:**

| Attribute Name  | Data Type | Description                              |
|-----------------|-----------|------------------------------------------|
| product_id      | Number    | Unique product identifier                |
| name            | String    | Product name                             |
| description     | String    | Product description                      |
| price           | Number    | Product price (Decimal)                  |
| category_id     | Number    | Category identifier                      |
| stock_quantity  | Number    | Available inventory count                |
| created_at      | String    | Product creation timestamp (ISO 8601)    |

**Sample Data:** 5 products including Laptop Pro ($1299.99), Wireless Mouse ($29.99), Coffee Mug ($12.99), Desk Chair ($299.99), and USB Cable ($19.99)

</details>

### Amazon DynamoDB tables (exposed by [Amazon Bedrock AgentCore Gateway](./cloudformation/gateway-stack.yaml))

These DynamoDB tables are accessed through the Amazon Bedrock AgentCore Gateway via AWS Lambda functions, providing warranty tracking and customer profile management.

<details>
<summary><b>Warranty Table</b></summary>

**Primary Key:**
- `serial_number` (String) - HASH

**Attributes:**

| Attribute Name    | Data Type | Description                              |
|-------------------|-----------|------------------------------------------|
| serial_number     | String    | Unique product serial number             |
| product_name      | String    | Name of the product                      |
| purchase_date     | String    | Date of purchase (YYYY-MM-DD)            |
| warranty_end_date | String    | Warranty expiration date (YYYY-MM-DD)    |
| warranty_type     | String    | Type (Standard/Extended/Premium)         |
| customer_name     | String    | Name of customer who purchased           |
| coverage_details  | String    | Detailed warranty coverage description   |

**Sample Data:** 5 warranty records for various products (laptop, phone, tablet, watch, camera) with different warranty types and expiration dates

</details>

<details>
<summary><b>Customer Profile Table</b></summary>

**Primary Key:**
- `customer_id` (String) - HASH

**Attributes:**

| Attribute Name              | Data Type | Description                              |
|-----------------------------|-----------|------------------------------------------|
| customer_id                 | String    | Unique customer identifier (CUST###)     |
| first_name                  | String    | Customer's first name                    |
| last_name                   | String    | Customer's last name                     |
| email                       | String    | Customer email address                   |
| phone                       | String    | Customer phone number                    |
| address                     | Map       | Address object (street, city, state, zip, country) |
| date_of_birth               | String    | Date of birth (YYYY-MM-DD)               |
| registration_date           | String    | Account registration date (YYYY-MM-DD)   |
| tier                        | String    | Customer tier (Standard/Gold/Premium/VIP)|
| communication_preferences   | Map       | Communication preferences (email, sms, phone) |
| support_cases_count         | Number    | Total number of support cases            |
| total_purchases             | Number    | Total number of purchases                |
| lifetime_value              | Number    | Total customer lifetime value (Decimal)  |
| notes                       | String    | Additional customer notes                |

**Sample Data:** 5 customer profiles (CUST001-CUST005) with tiers ranging from Standard to VIP, linked to Aurora PostgreSQL users table via `customer_id`

</details>

### Cross-System Data Relationships

The system integrates data across Aurora PostgreSQL and DynamoDB tables using consistent identifiers:

**Customer Data Integration:**

- `customer_id` (CUST###) links Aurora `users` table with DynamoDB `Customer Profile` table
- Aurora `orders.customer_id` references `users.customer_id` for purchase history
- DynamoDB `Reviews.customer_id` links back to Aurora users for review attribution
- Enables 360° customer view combining transactional, profile, and feedback data

**Product Data Integration:**

- Product data exists in both Aurora `products` table and DynamoDB `Products` table
- Cross-referenced via `product_id` and name matching
- DynamoDB `Reviews.product_id` links to both Aurora and DynamoDB product records
- Aurora tracks inventory and orders; DynamoDB tracks reviews and catalog metadata

**Warranty & Support Integration:**

- DynamoDB `Warranty` table uses `customer_name` and `product_name` for cross-system validation
- Links warranty records with customer profiles and product information
- Enables comprehensive support case management across all data sources
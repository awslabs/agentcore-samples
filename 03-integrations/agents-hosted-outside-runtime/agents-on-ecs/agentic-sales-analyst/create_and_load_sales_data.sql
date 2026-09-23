-- Create sales_data table
DROP TABLE IF EXISTS sales_data;

CREATE TABLE sales_data (
    ordernumber INTEGER,
    quantityordered INTEGER,
    priceeach DECIMAL(10,2),
    orderlinenumber INTEGER,
    sales DECIMAL(10,2),
    orderdate DATE,
    status VARCHAR(50),
    qtr_id INTEGER,
    month_id INTEGER,
    year_id INTEGER,
    productline VARCHAR(100),
    msrp DECIMAL(10,2),
    productcode VARCHAR(50),
    customername VARCHAR(200),
    phone VARCHAR(50),
    addressline1 VARCHAR(200),
    addressline2 VARCHAR(200),
    city VARCHAR(100),
    state VARCHAR(50),
    postalcode VARCHAR(20),
    country VARCHAR(100),
    territory VARCHAR(50),
    contactlastname VARCHAR(100),
    contactfirstname VARCHAR(100),
    dealsize VARCHAR(20)
);

-- Load CSV data
\COPY sales_data FROM '/docker-entrypoint-initdb.d/sales_data_sample_utf8.csv' WITH DELIMITER ',' CSV HEADER;

-- Verify the data was loaded
SELECT COUNT(*) as total_rows FROM sales_data;

-- Show sample data
SELECT ordernumber, productline, sales, customername, territory 
FROM sales_data 
LIMIT 5;
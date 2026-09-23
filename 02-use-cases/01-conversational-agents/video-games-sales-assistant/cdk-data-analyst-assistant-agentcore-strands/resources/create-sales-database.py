"""
Creates the video game sales database table and loads sample data from S3.

Sets up the Aurora PostgreSQL database by creating the table schema, installing
the aws_s3 extension, and importing CSV data from an S3 bucket via the RDS Data API.

Required environment variables:
  - DATA_SOURCE_BUCKET_NAME: S3 bucket containing the CSV data file
  - AURORA_SERVERLESS_DB_CLUSTER_ARN: Aurora cluster ARN
  - SECRET_ARN: Database admin secret ARN
  - DATABASE_NAME: Target database name (from CDK stack parameter DatabaseName)
"""

import boto3
import os

session = boto3.session.Session()
region = session.region_name

# Environment variables
data_source_bucket_name = os.environ["DATA_SOURCE_BUCKET_NAME"]
aurora_serverless_db_cluster_arn = os.environ["AURORA_SERVERLESS_DB_CLUSTER_ARN"]
secret_arn = os.environ["SECRET_ARN"]
database_name = os.environ["DATABASE_NAME"]

# File path variables
local_file_path = "resources/database/video_games_sales_no_headers.csv"
s3_file_name = "video_games_sales_no_headers.csv"

try:
    # Upload file to S3
    s3_client = boto3.client("s3")
    s3_client.upload_file(
        local_file_path,
        data_source_bucket_name,
        s3_file_name,
    )

    print(f"File '{s3_file_name}' uploaded to bucket '{data_source_bucket_name}'")

    # RDS data client
    client = boto3.client("rds-data")

    # Create table
    query1 = """ CREATE TABLE IF NOT EXISTS video_games_sales_units (
        title TEXT,
        console TEXT,
        genre TEXT,
        publisher TEXT,
        developer TEXT,
        critic_score NUMERIC(3,1),
        total_sales NUMERIC(4,2),
        na_sales NUMERIC(4,2),
        jp_sales NUMERIC(4,2),
        pal_sales NUMERIC(4,2),
        other_sales NUMERIC(4,2),
        release_date DATE
    ); """

    response = client.execute_statement(
        resourceArn=aurora_serverless_db_cluster_arn,
        secretArn=secret_arn,
        sql=query1,
        database=database_name,
    )

    print("Query: " + query1)
    print("Query response: " + str(response))

    # Create AWS S3 extension
    query2 = "CREATE EXTENSION IF NOT EXISTS aws_s3 CASCADE;"

    response = client.execute_statement(
        resourceArn=aurora_serverless_db_cluster_arn,
        secretArn=secret_arn,
        sql=query2,
        database=database_name,
    )

    print("-----------------------------------------")
    print("Query: " + query2)
    print("Query response: " + str(response))

    # Import data from S3 using IAM role credentials
    # Note: This uses the IAM role associated with the Aurora cluster via s3ImportRole
    query3 = f""" 
    SELECT aws_s3.table_import_from_s3(
    'video_games_sales_units',
    'title,console,genre,publisher,developer,critic_score,total_sales,na_sales,jp_sales,pal_sales,other_sales,release_date',
    '(FORMAT csv, DELIMITER ''|'', HEADER false)', 
    aws_commons.create_s3_uri('{data_source_bucket_name}', '{s3_file_name}', '{region}')
    ); """

    response = client.execute_statement(
        resourceArn=aurora_serverless_db_cluster_arn,
        secretArn=secret_arn,
        sql=query3,
        database=database_name,
    )

    print("-----------------------------------------")
    print("Query: " + query3)
    print("Query response: " + str(response))

    # Delete the file from S3
    versions_response = s3_client.list_object_versions(Bucket=data_source_bucket_name, Prefix=s3_file_name)

    # Delete all versions of the object
    delete_markers = []
    if "Versions" in versions_response:
        for version in versions_response["Versions"]:
            delete_markers.append({"Key": s3_file_name, "VersionId": version["VersionId"]})

    # Delete all delete markers
    if "DeleteMarkers" in versions_response:
        for marker in versions_response["DeleteMarkers"]:
            delete_markers.append({"Key": s3_file_name, "VersionId": marker["VersionId"]})

    # Execute the delete operation if there are versions to delete
    if delete_markers:
        s3_client.delete_objects(
            Bucket=data_source_bucket_name,
            Delete={"Objects": delete_markers, "Quiet": False},
        )

    print("-----------------------------------------")
    print("Database created successfully!")

except Exception as e:
    print(f"Error: {e}")

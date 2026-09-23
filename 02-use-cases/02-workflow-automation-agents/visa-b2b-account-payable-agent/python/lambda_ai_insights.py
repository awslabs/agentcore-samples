import json
import boto3
import psycopg2
from datetime import datetime, timedelta

# from decimal import Decimal
import os

# AWS clients
bedrock_client = boto3.client("bedrock-runtime")
secrets_client = boto3.client("secretsmanager")

# Configuration
BEDROCK_MODEL_ID = "us.anthropic.claude-sonnet-4-20250514-v1:0"
DB_SECRET_NAME = os.environ.get("DB_SECRET_NAME", "rtp-overlay/db-credentials")


def get_db_credentials():
    """Retrieve database credentials from AWS Secrets Manager"""
    try:
        response = secrets_client.get_secret_value(SecretId=DB_SECRET_NAME)
        secret = json.loads(response["SecretString"])
        return secret
    except Exception as e:
        print(f"Error retrieving DB credentials: {str(e)}")
        raise


def get_db_connection():
    """Create database connection to RDS PostgreSQL"""
    try:
        creds = get_db_credentials()
        conn = psycopg2.connect(
            host=creds["host"],
            port=creds.get("port", 5432),
            database=creds["dbname"],
            user=creds["username"],
            password=creds["password"],
        )
        return conn
    except Exception as e:
        print(f"Error connecting to database: {str(e)}")
        raise


def invoke_bedrock(prompt):
    """Invoke Claude Sonnet 4.0 with prompt"""
    try:
        payload = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 4000,
            "temperature": 0.3,
            "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}],
        }

        response = bedrock_client.invoke_model(
            modelId=BEDROCK_MODEL_ID,
            contentType="application/json",
            body=json.dumps(payload),
        )

        response_body = json.loads(response["body"].read())
        return response_body["content"][0]["text"]
    except Exception as e:
        print(f"Error invoking Bedrock: {str(e)}")
        raise


def fetch_treasury_data(db_conn, user_id=None):
    """
    Fetch and aggregate treasury data from RDS
    Returns invoice and payment statistics for the last 30 days
    """
    try:
        cursor = db_conn.cursor()

        # Calculate date range (last 30 days)
        end_date = datetime.now()
        start_date = end_date - timedelta(days=30)

        # Fetch invoice data
        cursor.execute(
            """
            SELECT 
                COUNT(*) as total,
                COUNT(CASE WHEN "paymentStatus" = 'pending' THEN 1 END) as pending,
                COUNT(CASE WHEN due_date < CURRENT_DATE AND "paymentStatus" NOT IN ('paid', 'cancelled') THEN 1 END) as overdue,
                COALESCE(SUM(total_amount), 0) as total_amount,
                COALESCE(AVG(EXTRACT(EPOCH FROM (updated_at - created_at))/86400), 0) as avg_processing_days
            FROM invoices
            WHERE created_at >= %s AND created_at <= %s
        """,
            (start_date, end_date),
        )

        invoice_row = cursor.fetchone()

        # Fetch invoice status distribution
        cursor.execute(
            """
            SELECT "paymentStatus" as status, COUNT(*) as count, COALESCE(SUM(total_amount), 0) as amount
            FROM invoices
            WHERE created_at >= %s AND created_at <= %s
            GROUP BY "paymentStatus"
        """,
            (start_date, end_date),
        )

        invoice_status = cursor.fetchall()

        # Fetch invoice trends (last 7 days)
        cursor.execute(
            """
            SELECT 
                DATE(created_at) as date,
                COUNT(*) as count,
                COALESCE(SUM(total_amount), 0) as amount
            FROM invoices
            WHERE created_at >= %s
            GROUP BY DATE(created_at)
            ORDER BY date DESC
            LIMIT 7
        """,
            (end_date - timedelta(days=7),),
        )

        invoice_trends = cursor.fetchall()

        # Fetch payment data (from invoices table)
        cursor.execute(
            """
            SELECT 
                COUNT(*) as total,
                COUNT(CASE WHEN "paymentStatus" = 'generated' THEN 1 END) as ready,
                COUNT(CASE WHEN "paymentStatus" = 'sent' THEN 1 END) as sent,
                COUNT(CASE WHEN "paymentStatus" = 'paid' THEN 1 END) as paid,
                COALESCE(SUM(total_amount), 0) as total_amount
            FROM invoices
            WHERE created_at >= %s AND created_at <= %s
        """,
            (start_date, end_date),
        )

        payment_row = cursor.fetchone()

        # Fetch payment status distribution
        cursor.execute(
            """
            SELECT "paymentStatus" as status, COUNT(*) as count, COALESCE(SUM(total_amount), 0) as amount
            FROM invoices
            WHERE created_at >= %s AND created_at <= %s AND "paymentStatus" IN ('generated', 'sent', 'paid', 'processing')
            GROUP BY "paymentStatus"
        """,
            (start_date, end_date),
        )

        payment_status = cursor.fetchall()

        # Fetch payment trends (last 7 days) - using invoices with payment status
        cursor.execute(
            """
            SELECT 
                DATE(created_at) as date,
                COUNT(*) as count,
                COALESCE(SUM(total_amount), 0) as amount
            FROM invoices
            WHERE created_at >= %s AND "paymentStatus" IN ('generated', 'sent', 'paid')
            GROUP BY DATE(created_at)
            ORDER BY date DESC
            LIMIT 7
        """,
            (end_date - timedelta(days=7),),
        )

        payment_trends = cursor.fetchall()

        # Fetch vendor data
        cursor.execute(
            """
            SELECT COUNT(DISTINCT vendor_id) as total_vendors
            FROM invoices
            WHERE created_at >= %s AND created_at <= %s
        """,
            (start_date, end_date),
        )

        vendor_row = cursor.fetchone()

        cursor.close()

        # Structure the data
        treasury_data = {
            "invoices": {
                "total": int(invoice_row[0]),
                "pending": int(invoice_row[1]),
                "overdue": int(invoice_row[2]),
                "total_amount": float(invoice_row[3]),
                "avg_processing_time": float(invoice_row[4]),
                "by_status": [
                    {"status": row[0], "count": int(row[1]), "amount": float(row[2])} for row in invoice_status
                ],
                "trends": [
                    {"date": str(row[0]), "count": int(row[1]), "amount": float(row[2])} for row in invoice_trends
                ],
            },
            "payments": {
                "total": int(payment_row[0]),
                "ready": int(payment_row[1]),
                "sent": int(payment_row[2]),
                "paid": int(payment_row[3]),
                "total_amount": float(payment_row[4]),
                "by_status": [
                    {"status": row[0], "count": int(row[1]), "amount": float(row[2])} for row in payment_status
                ],
                "trends": [
                    {"date": str(row[0]), "count": int(row[1]), "amount": float(row[2])} for row in payment_trends
                ],
            },
            "vendors": {
                "total": int(vendor_row[0]),
                "active": int(vendor_row[0]),  # Simplified: all vendors with invoices are active
            },
            "time_range": {
                "start": start_date.isoformat(),
                "end": end_date.isoformat(),
            },
        }

        return treasury_data

    except Exception as e:
        print(f"Error fetching treasury data: {str(e)}")
        raise


def anonymize_data(data):
    """
    Anonymize sensitive information before sending to AI
    - Round amounts to nearest $1,000
    - Convert to percentage distributions
    - Calculate trend directions
    """
    try:
        # Helper function to round amounts
        def round_amount(amount):
            return round(amount / 1000) * 1000

        # Helper function to format amount range
        def format_amount_range(amount):
            rounded = round_amount(amount)
            if rounded < 1000:
                return f"${rounded}"
            else:
                return f"${rounded // 1000}K"

        # Helper function to calculate trend
        def calculate_trend(trends):
            if len(trends) < 2:
                return "stable", 0

            recent = sum(t["amount"] for t in trends[:3]) / 3
            older = sum(t["amount"] for t in trends[3:6]) / 3 if len(trends) >= 6 else recent

            if older == 0:
                return "stable", 0

            change = ((recent - older) / older) * 100

            if change > 5:
                return "up", round(change, 1)
            elif change < -5:
                return "down", round(abs(change), 1)
            else:
                return "stable", round(abs(change), 1)

        # Calculate invoice trend
        invoice_trend_direction, invoice_trend_pct = calculate_trend(data["invoices"]["trends"])

        # Calculate payment trend
        payment_trend_direction, payment_trend_pct = calculate_trend(data["payments"]["trends"])

        # Calculate status distributions
        invoice_total = data["invoices"]["total"] or 1
        payment_total = data["payments"]["total"] or 1

        anonymized = {
            "invoices": {
                "total": data["invoices"]["total"],
                "pending": data["invoices"]["pending"],
                "overdue": data["invoices"]["overdue"],
                "total_amount_range": format_amount_range(data["invoices"]["total_amount"]),
                "avg_processing_time": round(data["invoices"]["avg_processing_time"], 1),
                "status_distribution": [
                    {
                        "status": s["status"],
                        "percentage": round((s["count"] / invoice_total) * 100, 1),
                    }
                    for s in data["invoices"]["by_status"]
                ],
                "trend_direction": invoice_trend_direction,
                "trend_percentage": invoice_trend_pct,
            },
            "payments": {
                "total": data["payments"]["total"],
                "ready": data["payments"]["ready"],
                "sent": data["payments"]["sent"],
                "paid": data["payments"]["paid"],
                "total_amount": data["payments"]["total_amount"],  # Keep numeric value for predictions
                "total_amount_range": format_amount_range(data["payments"]["total_amount"]),
                "status_distribution": [
                    {
                        "status": s["status"],
                        "percentage": round((s["count"] / payment_total) * 100, 1),
                    }
                    for s in data["payments"]["by_status"]
                ],
                "trend_direction": payment_trend_direction,
                "trend_percentage": payment_trend_pct,
            },
            "vendors": {
                "total": data["vendors"]["total"],
                "active": data["vendors"]["active"],
            },
        }

        return anonymized

    except Exception as e:
        print(f"Error anonymizing data: {str(e)}")
        raise


def generate_summary(data):
    """Generate AI-powered treasury summary"""
    try:
        prompt = f"""You are a Treasury AI Assistant analyzing financial data for a company's payment operations.

Current Treasury Data:
- Total Invoices: {data["invoices"]["total"]}
- Pending Payments: {data["payments"]["ready"]} ({data["payments"]["total_amount_range"]})
- Overdue Invoices: {data["invoices"]["overdue"]}
- Active Vendors: {data["vendors"]["active"]}
- Average Processing Time: {data["invoices"]["avg_processing_time"]} days
- Payment Trend: {data["payments"]["trend_direction"]} ({data["payments"]["trend_percentage"]}% vs last period)
- Invoice Trend: {data["invoices"]["trend_direction"]} ({data["invoices"]["trend_percentage"]}% vs last period)

Generate a concise, professional summary (2-3 sentences) highlighting:
1. Current operational status
2. Key trends or concerns
3. Overall financial health

Use a confident, analytical tone suitable for treasury management. Focus on actionable insights."""

        response = invoke_bedrock(prompt)
        return response.strip()

    except Exception as e:
        print(f"Error generating summary: {str(e)}")
        # Fallback summary
        return f"Treasury operations show {data['invoices']['total']} invoices with {data['payments']['ready']} payments ready totaling {data['payments']['total_amount_range']}. Payment activity is trending {data['payments']['trend_direction']}."


def predict_cashflow(data):
    """Generate cash flow predictions"""
    try:
        prompt = f"""Analyze the following treasury data to predict cash outflow:

Current Data:
- Pending Payments: {data["payments"]["ready"]} totaling {data["payments"]["total_amount_range"]}
- Payment Trend: {data["payments"]["trend_direction"]} {data["payments"]["trend_percentage"]}%
- Average Processing Time: {data["invoices"]["avg_processing_time"]} days
- Active Vendors: {data["vendors"]["active"]}

Based on this data, predict cash outflow for:
- Next 7 days
- Next 14 days
- Next 30 days

For each prediction, provide:
- Estimated amount range (e.g., "$50K-75K")
- Confidence level (0-100%)
- Key factors influencing the prediction

Return response as JSON with this structure:
{{
  "next7Days": {{
    "amount": "$50K-75K",
    "confidence": 85,
    "factors": ["factor1", "factor2"]
  }},
  "next14Days": {{ ... }},
  "next30Days": {{ ... }},
  "assumptions": ["assumption1", "assumption2"],
  "risks": ["risk1", "risk2"]
}}

Return ONLY valid JSON, no markdown or commentary."""

        response = invoke_bedrock(prompt)

        # Clean response (remove markdown if present)
        cleaned = response.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned.replace("```json", "").replace("```", "").strip()
        elif cleaned.startswith("```"):
            cleaned = cleaned.replace("```", "").strip()

        return json.loads(cleaned)

    except Exception as e:
        print(f"Error predicting cashflow: {str(e)}")
        # Fallback prediction with numeric values
        total_amount = float(data["payments"]["total_amount"])
        return {
            "next7Days": {
                "amount": total_amount,
                "confidence": 50,
                "factors": ["Historical payment patterns"],
            },
            "next14Days": {
                "amount": total_amount,
                "confidence": 40,
                "factors": ["Historical payment patterns"],
            },
            "next30Days": {
                "amount": total_amount,
                "confidence": 30,
                "factors": ["Historical payment patterns"],
            },
            "assumptions": ["Based on current pending payments"],
            "risks": ["Limited historical data"],
        }


def detect_anomalies(data):
    """Detect anomalies in treasury data"""
    try:
        prompt = f"""Analyze this treasury data for anomalies and unusual patterns:

Data Analysis:
- Invoice Volume: {data["invoices"]["total"]} (trend: {data["invoices"]["trend_direction"]} {data["invoices"]["trend_percentage"]}%)
- Payment Volume: {data["payments"]["total"]} (trend: {data["payments"]["trend_direction"]} {data["payments"]["trend_percentage"]}%)
- Processing Time: {data["invoices"]["avg_processing_time"]} days average
- Overdue Rate: {round((data["invoices"]["overdue"] / max(data["invoices"]["total"], 1)) * 100, 1)}%

Look for anomalies in:
1. Volume spikes or drops (>20% change)
2. Processing delays (>normal timeframes)
3. Overdue rate changes
4. Trend reversals

For each anomaly found, return JSON array with:
[{{
  "type": "amount_spike|processing_delay|pattern_change|volume_anomaly",
  "severity": "low|medium|high",
  "description": "Clear description of the anomaly",
  "affectedEntity": "Anonymized entity (e.g., 'Payment Processing', 'Invoice Volume')",
  "impact": "Potential business impact",
  "recommendation": "Specific action to take",
  "confidence": 75
}}]

Return empty array [] if no significant anomalies detected.
Return ONLY valid JSON array, no markdown or commentary."""

        response = invoke_bedrock(prompt)

        # Clean response
        cleaned = response.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned.replace("```json", "").replace("```", "").strip()
        elif cleaned.startswith("```"):
            cleaned = cleaned.replace("```", "").strip()

        anomalies = json.loads(cleaned)

        # Add IDs and timestamps
        for i, anomaly in enumerate(anomalies):
            anomaly["id"] = f"anomaly_{int(datetime.now().timestamp())}_{i}"
            anomaly["detectedAt"] = datetime.now().isoformat()

        return anomalies

    except Exception as e:
        print(f"Error detecting anomalies: {str(e)}")
        return []


def generate_recommendations(data):
    """Generate actionable recommendations"""
    try:
        prompt = f"""Based on this treasury data, generate actionable recommendations:

Current State:
- {data["payments"]["ready"]} payments ready ({data["payments"]["total_amount_range"]})
- {data["invoices"]["overdue"]} overdue invoices
- {data["invoices"]["avg_processing_time"]} days average processing time
- Payment trend: {data["payments"]["trend_direction"]} {data["payments"]["trend_percentage"]}%

Generate 2-4 specific recommendations for:
1. Process optimization
2. Risk mitigation
3. Cost savings
4. Efficiency improvements

Return JSON array with:
[{{
  "type": "optimization|risk_mitigation|process_improvement|cost_saving",
  "priority": "low|medium|high",
  "title": "Short, actionable title",
  "description": "Detailed explanation",
  "expectedBenefit": "Quantified benefit where possible",
  "implementation": ["step1", "step2", "step3"],
  "estimatedEffort": "low|medium|high"
}}]

Focus on practical, implementable recommendations.
Return ONLY valid JSON array, no markdown or commentary."""

        response = invoke_bedrock(prompt)

        # Clean response
        cleaned = response.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned.replace("```json", "").replace("```", "").strip()
        elif cleaned.startswith("```"):
            cleaned = cleaned.replace("```", "").strip()

        recommendations = json.loads(cleaned)

        # Add IDs
        for i, rec in enumerate(recommendations):
            rec["id"] = f"rec_{int(datetime.now().timestamp())}_{i}"

        return recommendations

    except Exception as e:
        print(f"Error generating recommendations: {str(e)}")
        return []


def generate_all_insights(data):
    """Generate all AI insights in one call"""
    try:
        summary = generate_summary(data)
        predictions = predict_cashflow(data)
        anomalies = detect_anomalies(data)
        recommendations = generate_recommendations(data)

        return {
            "summary": summary,
            "predictions": predictions,
            "anomalies": anomalies,
            "recommendations": recommendations,
            "lastUpdated": datetime.now().isoformat(),
        }
    except Exception as e:
        print(f"Error generating all insights: {str(e)}")
        raise


def lambda_handler(event, context):
    """
    Main Lambda handler for AI insights generation

    Event structure:
    {
        "insightType": "summary|predictions|anomalies|recommendations|all",
        "userId": "user-id-optional"
    }
    """
    try:
        print(f"Received event: {json.dumps(event)}")

        insight_type = event.get("insightType", "all")
        user_id = event.get("userId")

        # Get database connection
        db_conn = get_db_connection()

        # Fetch treasury data
        print("Fetching treasury data from RDS...")
        treasury_data = fetch_treasury_data(db_conn, user_id)

        # Close database connection
        db_conn.close()

        # Anonymize data
        print("Anonymizing data...")
        anonymized_data = anonymize_data(treasury_data)

        # Generate requested insights
        print(f"Generating {insight_type} insights...")

        # Add timestamp to all responses
        current_timestamp = datetime.now().isoformat()

        if insight_type == "summary":
            result = {
                "summary": generate_summary(anonymized_data),
                "generatedAt": current_timestamp,
            }
        elif insight_type == "predictions":
            result = {
                "predictions": predict_cashflow(anonymized_data),
                "generatedAt": current_timestamp,
            }
        elif insight_type == "anomalies":
            result = {
                "anomalies": detect_anomalies(anonymized_data),
                "generatedAt": current_timestamp,
            }
        elif insight_type == "recommendations":
            result = {
                "recommendations": generate_recommendations(anonymized_data),
                "generatedAt": current_timestamp,
            }
        else:  # 'all'
            result = generate_all_insights(anonymized_data)
            result["generatedAt"] = current_timestamp

        print(f"Successfully generated {insight_type} insights")

        return {"statusCode": 200, "body": json.dumps(result, default=str)}

    except Exception as e:
        error_msg = f"Error generating AI insights: {str(e)}"
        print(error_msg)

        return {"statusCode": 500, "body": json.dumps({"error": error_msg})}

import io
import json
import os
import time
from datetime import datetime

import boto3
from botocore.exceptions import ClientError
from fpdf import FPDF

s3_client = boto3.client("s3")
bedrock_client = boto3.client("bedrock-runtime")

S3_BUCKET = os.environ.get("TRANSCRIPT_S3_BUCKET", "ai-interview-transcripts-123456789012")
MODEL_ID = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"


def lambda_handler(event, context):
    """Evaluate interview transcript using Claude Sonnet"""

    session_id = event.get("session_id")
    if not session_id:
        return {"statusCode": 400, "body": json.dumps({"error": "session_id required"})}

    print(f"Evaluating interview for session: {session_id}")

    # 1. Read transcript from S3
    try:
        response = s3_client.list_objects_v2(Bucket=S3_BUCKET, Prefix="transcripts/")
        if "Contents" not in response:
            return {
                "statusCode": 404,
                "body": json.dumps({"error": "No transcripts found"}),
            }

        transcript_obj = None
        for obj in response["Contents"]:
            if session_id in obj["Key"]:
                transcript_obj = s3_client.get_object(Bucket=S3_BUCKET, Key=obj["Key"])
                break

        if not transcript_obj:
            return {
                "statusCode": 404,
                "body": json.dumps({"error": "Transcript not found"}),
            }

        transcript_data = json.loads(transcript_obj["Body"].read())
        print(f"Transcript loaded: {len(transcript_data['transcript']['messages'])} messages")

    except Exception as e:
        print(f"Error reading transcript: {e}")
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}

    # 2. Call Claude Sonnet to evaluate (with retry logic)
    messages = transcript_data["transcript"]["messages"]
    conversation = "\n\n".join([f"{msg['role']}: {msg['content']}" for msg in messages])

    prompt = f"""You are an expert interviewer evaluating a candidate's responses to behavioral interview questions.

Interview Transcript:
{conversation}

Please provide a comprehensive evaluation in the following JSON format:

{{
  "overall_assessment": "1-2 paragraph summary of the candidate's performance",
  "leadership_principles": [
    {{
      "principle": "Principle Name",
      "score": 8,
      "strengths": "What the candidate did well",
      "areas_for_improvement": "What could be improved"
    }}
  ],
  "communication_skills": {{
    "score": 8,
    "explanation": "Brief explanation of communication assessment"
  }},
  "technical_depth": {{
    "score": 7,
    "explanation": "Brief explanation of technical assessment"
  }},
  "final_recommendation": {{
    "decision": "Hire/No Hire/Maybe",
    "justification": "Detailed justification for the recommendation"
  }}
}}

Return only valid JSON without any markdown formatting or additional text."""

    # Retry with exponential backoff
    max_retries = 5
    base_delay = 10
    evaluation_text = None

    for attempt in range(max_retries):
        try:
            if attempt > 0:
                delay = base_delay * (2**attempt)
                print(f"Retry attempt {attempt + 1}/{max_retries} after {delay}s delay")
                time.sleep(delay)

            response = bedrock_client.invoke_model(
                modelId="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
                body=json.dumps(
                    {
                        "anthropic_version": "bedrock-2023-05-31",
                        "max_tokens": 4000,
                        "messages": [{"role": "user", "content": prompt}],
                    }
                ),
            )

            result = json.loads(response["body"].read())
            evaluation_text = result["content"][0]["text"]
            print(f"Evaluation generated: {len(evaluation_text)} chars")
            break

        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code == "ServiceUnavailableException" and attempt < max_retries - 1:
                print(f"Connection limit hit, retrying... ({attempt + 1}/{max_retries})")
                continue
            else:
                print(f"Error calling Claude: {e}")
                return {"statusCode": 500, "body": json.dumps({"error": str(e)})}
        except Exception as e:
            print(f"Error calling Claude: {e}")
            return {"statusCode": 500, "body": json.dumps({"error": str(e)})}

    if not evaluation_text:
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "Failed to generate evaluation after retries"}),
        }

    # 3. Save feedback to S3
    try:
        feedback_data = {
            "session_id": session_id,
            "candidate_name": transcript_data["candidate_name"],
            "evaluation_timestamp": datetime.utcnow().isoformat(),
            "transcript_summary": {
                "start_time": transcript_data["start_time"],
                "end_time": transcript_data["end_time"],
                "duration_seconds": transcript_data["duration_seconds"],
                "message_count": len(messages),
            },
            "evaluation": evaluation_text,
            "model_used": MODEL_ID,
        }

        feedback_key = f"feedback/{transcript_data['candidate_name']}_{session_id}_feedback.json"
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=feedback_key,
            Body=json.dumps(feedback_data, indent=2),
            ContentType="application/json",
        )

        print(f"Feedback saved to S3: {feedback_key}")

        # 4. Generate PDF
        try:
            # Parse evaluation JSON
            eval_text = evaluation_text.replace("```json", "").replace("```", "").strip()
            try:
                evaluation_data = json.loads(eval_text)
            except Exception:
                evaluation_data = {
                    "error": "Could not parse evaluation",
                    "raw_text": eval_text,
                }

            pdf = FPDF()
            pdf.add_page()

            # Header
            pdf.set_font("Arial", "B", 20)
            pdf.set_text_color(0, 102, 204)  # AWS Blue
            pdf.cell(0, 15, "Interview Evaluation Report", 0, 1, "C")
            pdf.ln(5)

            # Candidate Info Box
            pdf.set_fill_color(240, 248, 255)  # Light blue background
            pdf.set_text_color(0, 0, 0)
            pdf.set_font("Arial", "B", 12)
            pdf.cell(0, 8, "Candidate Information", 0, 1, "L", True)
            pdf.set_font("Arial", "", 10)
            pdf.cell(0, 6, f"Name: {transcript_data['candidate_name']}", 0, 1)
            pdf.cell(0, 6, f"Interview Date: {transcript_data['start_time'][:10]}", 0, 1)
            pdf.cell(
                0, 6, f"Duration: {int(transcript_data['duration_seconds'] // 60)} minutes", 0, 1
            )
            pdf.cell(0, 6, f"Session ID: {session_id}", 0, 1)
            pdf.ln(8)

            if "error" not in evaluation_data:
                # Overall Assessment
                if (
                    "overall_assessment" in evaluation_data
                    or "Overall Assessment" in evaluation_data
                ):
                    pdf.set_font("Arial", "B", 14)
                    pdf.set_text_color(0, 102, 204)
                    pdf.cell(0, 10, "Overall Assessment", 0, 1)
                    pdf.set_text_color(0, 0, 0)
                    pdf.set_font("Arial", "", 10)
                    assessment = evaluation_data.get(
                        "overall_assessment", evaluation_data.get("Overall Assessment", "")
                    )
                    pdf.multi_cell(0, 6, assessment)
                    pdf.ln(5)

                # Leadership Principles
                principles_key = next(
                    (k for k in evaluation_data if "principle" in k.lower()), None
                )
                if principles_key and isinstance(evaluation_data[principles_key], list):
                    pdf.set_font("Arial", "B", 14)
                    pdf.set_text_color(0, 102, 204)
                    pdf.cell(0, 10, "Competency Evaluation", 0, 1)
                    pdf.set_text_color(0, 0, 0)

                    for principle in evaluation_data[principles_key]:
                        if isinstance(principle, dict):
                            pdf.set_font("Arial", "B", 11)
                            name = principle.get(
                                "principle", principle.get("name", "Unknown Principle")
                            )
                            score = principle.get("score", "N/A")
                            pdf.cell(0, 8, f"{name} - Score: {score}/10", 0, 1)

                            pdf.set_font("Arial", "", 9)
                            if "strengths" in principle:
                                pdf.cell(20, 5, "", 0, 0)  # Indent
                                pdf.cell(0, 5, f"Strengths: {principle['strengths']}", 0, 1)
                            if "areas_for_improvement" in principle:
                                pdf.cell(20, 5, "", 0, 0)  # Indent
                                pdf.cell(
                                    0,
                                    5,
                                    f"Areas for improvement: {principle['areas_for_improvement']}",
                                    0,
                                    1,
                                )
                            pdf.ln(3)

                # Skills Assessment
                pdf.set_font("Arial", "B", 14)
                pdf.set_text_color(0, 102, 204)
                pdf.cell(0, 10, "Skills Assessment", 0, 1)
                pdf.set_text_color(0, 0, 0)
                pdf.set_font("Arial", "", 10)

                comm_score = evaluation_data.get(
                    "communication_skills", evaluation_data.get("Communication Skills", {})
                )
                if isinstance(comm_score, dict):
                    score = comm_score.get("score", "N/A")
                    explanation = comm_score.get("explanation", "")
                    pdf.cell(0, 6, f"Communication Skills: {score}/10", 0, 1)
                    if explanation:
                        pdf.multi_cell(0, 5, f"  {explanation}")
                    pdf.ln(2)

                tech_score = evaluation_data.get(
                    "technical_depth", evaluation_data.get("Technical Depth", {})
                )
                if isinstance(tech_score, dict):
                    score = tech_score.get("score", "N/A")
                    explanation = tech_score.get("explanation", "")
                    pdf.cell(0, 6, f"Technical Depth: {score}/10", 0, 1)
                    if explanation:
                        pdf.multi_cell(0, 5, f"  {explanation}")
                    pdf.ln(5)

                # Final Recommendation
                recommendation = evaluation_data.get(
                    "final_recommendation", evaluation_data.get("Final Recommendation", {})
                )
                if recommendation:
                    pdf.set_font("Arial", "B", 14)
                    pdf.set_text_color(0, 102, 204)
                    pdf.cell(0, 10, "Final Recommendation", 0, 1)
                    pdf.set_text_color(0, 0, 0)

                    if isinstance(recommendation, dict):
                        decision = recommendation.get(
                            "decision", recommendation.get("recommendation", "N/A")
                        )
                        justification = recommendation.get(
                            "justification", recommendation.get("reason", "")
                        )

                        # Color code the recommendation
                        if "hire" in decision.lower() and "no" not in decision.lower():
                            pdf.set_text_color(0, 128, 0)  # Green
                        elif "no hire" in decision.lower():
                            pdf.set_text_color(255, 0, 0)  # Red
                        else:
                            pdf.set_text_color(255, 165, 0)  # Orange

                        pdf.set_font("Arial", "B", 12)
                        pdf.cell(0, 8, f"Decision: {decision}", 0, 1)
                        pdf.set_text_color(0, 0, 0)
                        pdf.set_font("Arial", "", 10)
                        if justification:
                            pdf.multi_cell(0, 6, f"Justification: {justification}")
                    else:
                        pdf.set_font("Arial", "", 10)
                        pdf.multi_cell(0, 6, str(recommendation))
            else:
                # Fallback for unparseable evaluation
                pdf.set_font("Arial", "B", 14)
                pdf.cell(0, 10, "Evaluation Results", 0, 1)
                pdf.set_font("Arial", "", 9)
                pdf.multi_cell(0, 5, evaluation_data.get("raw_text", "No evaluation available"))

            # Footer
            pdf.ln(10)
            pdf.set_font("Arial", "I", 8)
            pdf.set_text_color(128, 128, 128)
            pdf.cell(
                0,
                5,
                f"Generated on {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC",
                0,
                1,
                "C",
            )
            pdf.cell(0, 5, "AI Interview Agent - Powered by Amazon Bedrock", 0, 1, "C")

            pdf_buffer = io.BytesIO(pdf.output())

            # Save PDF to S3
            pdf_key = f"feedback/{transcript_data['candidate_name']}_{session_id}_feedback.pdf"
            s3_client.put_object(
                Bucket=S3_BUCKET,
                Key=pdf_key,
                Body=pdf_buffer.getvalue() if isinstance(pdf_buffer, io.BytesIO) else pdf_buffer,
                ContentType="application/pdf",
            )
            print(f"PDF saved to S3: {pdf_key}")

        except Exception as e:
            print(f"Error generating PDF: {e}")
            pdf_key = None

        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "message": "Evaluation completed",
                    "session_id": session_id,
                    "feedback_key": feedback_key,
                    "pdf_key": pdf_key,
                    "evaluation": evaluation_text,
                }
            ),
        }

    except Exception as e:
        print(f"Error saving feedback: {e}")
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}

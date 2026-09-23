from strands import tool
import boto3
import os
import re


@tool
def send_email(to: str, subject: str, body: str, from_email: str = "noreply@example.com") -> str:
    """
    Send an email using AWS SES (Simple Email Service).

    This tool allows the agent to send emails to users or systems.

    Args:
        to: Recipient email address
        subject: Email subject line
        body: Email body content (plain text)
        from_email: Sender email address (default: noreply@example.com)

    Returns:
        Success message with message ID or error message

    Example:
        result = send_email(
            to="user@example.com",
            subject="Welcome!",
            body="Thank you for signing up."
        )

    Note:
        - Sender email must be verified in AWS SES
        - For production use, move out of SES sandbox
        - Update from_email default to your verified domain
    """
    try:
        # Validate email addresses
        email_pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"

        if not re.match(email_pattern, to):
            return f"Error: Invalid recipient email address: {to}"

        if not re.match(email_pattern, from_email):
            return f"Error: Invalid sender email address: {from_email}"

        # Validate subject and body
        if not subject or not subject.strip():
            return "Error: Email subject cannot be empty"

        if not body or not body.strip():
            return "Error: Email body cannot be empty"

        # Initialize SES client with region from environment
        region = os.environ.get("AWS_REGION", "us-west-2")
        ses_client = boto3.client("ses", region_name=region)

        # Send email
        response = ses_client.send_email(
            Source=from_email,
            Destination={"ToAddresses": [to]},
            Message={
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {"Text": {"Data": body, "Charset": "UTF-8"}},
            },
        )

        message_id = response["MessageId"]
        return f"Email sent successfully!\nTo: {to}\nSubject: {subject}\nMessage ID: {message_id}"

    except ses_client.exceptions.MessageRejected as e:
        return f"Email rejected: {str(e)}\n\nNote: Ensure sender email is verified in AWS SES."
    except ses_client.exceptions.MailFromDomainNotVerifiedException:
        return f"Error: Sender domain not verified in AWS SES. Please verify {from_email} in SES console."
    except ses_client.exceptions.ConfigurationSetDoesNotExistException:
        return "Error: SES configuration set does not exist."
    except Exception as e:
        error_msg = str(e)
        if "Email address is not verified" in error_msg:
            return f"Error: Email address not verified in AWS SES.\n\nTo use this tool:\n1. Go to AWS SES console\n2. Verify {from_email}\n3. For production, move out of SES sandbox"
        else:
            return f"Error sending email: {error_msg}"

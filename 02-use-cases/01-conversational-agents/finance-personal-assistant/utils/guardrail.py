# Configure AWS clients
import boto3

bedrock_client = boto3.client("bedrock")
bedrock_runtime = boto3.client("bedrock-runtime")


def create_guardrail():
    guardrail_name = "guardrail-no-bitcoin-advice"

    # Check if guardrail already exists
    try:
        existing_guardrails = bedrock_client.list_guardrails()
        for guardrail in existing_guardrails.get("guardrails", []):
            if guardrail.get("name") == guardrail_name:
                print(f"Guardrail '{guardrail_name}' already exists. Returning existing guardrail.")
                return (guardrail.get("id"), guardrail.get("arn"))
    except Exception as e:
        print(f"Error checking existing guardrails: {e}")

    # Create new guardrail if it doesn't exist
    print(f"Creating new guardrail '{guardrail_name}'...")
    response = bedrock_client.create_guardrail(
        name=guardrail_name,
        description="Prevents the model from providing Bitcoin investment advice.",
        contentPolicyConfig={
            "filtersConfig": [
                {"type": "SEXUAL", "inputStrength": "HIGH", "outputStrength": "HIGH"},
                {"type": "VIOLENCE", "inputStrength": "HIGH", "outputStrength": "HIGH"},
                {"type": "HATE", "inputStrength": "HIGH", "outputStrength": "HIGH"},
                {"type": "INSULTS", "inputStrength": "HIGH", "outputStrength": "HIGH"},
                {
                    "type": "MISCONDUCT",
                    "inputStrength": "HIGH",
                    "outputStrength": "HIGH",
                },
                {
                    "type": "PROMPT_ATTACK",
                    "inputStrength": "HIGH",
                    "outputStrength": "NONE",
                },
            ]
        },
        wordPolicyConfig={
            "wordsConfig": [
                {"text": "Bitcoin investment advice"},
                {"text": "Bitcoin recommendations"},
                {"text": "cryptocurrency investment"},
                {"text": "Bitcoin strategy"},
                {"text": "Bitcoin portfolio"},
                {"text": "Bitcoin trading advice"},
                {"text": "Bitcoin financial guidance"},
                {"text": "Bitcoin fiduciary advice"},
                {"text": "crypto investment tips"},
                {"text": "Bitcoin"},
            ],
            "managedWordListsConfig": [{"type": "PROFANITY"}],
        },
        blockedInputMessaging="I apologize, but I am not able to provide Bitcoin investment advice. It is best to consult with trusted finance specialists to learn about cryptocurrency investments",
        blockedOutputsMessaging="I apologize, but I am not able to provide Bitcoin investment advice. For your privacy and security, please modify your input and try again without including Bitcoin investment details.",
    )
    return (response.get("guardrailId"), response.get("guardrailArn"))


def delete_guardrail(guardrail_id=None):
    """
    Delete the Bitcoin advice guardrail by ID, or find and delete by name if no ID provided.

    Args:
        guardrail_id: The ID of the guardrail to delete (optional)

    Returns:
        bool: True if deletion was successful, False otherwise
    """
    guardrail_name = "guardrail-no-bitcoin-advice"

    try:
        # If no ID provided, find it by name
        if not guardrail_id:
            existing_guardrails = bedrock_client.list_guardrails()
            for guardrail in existing_guardrails.get("guardrails", []):
                if guardrail.get("name") == guardrail_name:
                    guardrail_id = guardrail.get("id")
                    break

            if not guardrail_id:
                print(f"Guardrail '{guardrail_name}' not found")
                return False

        # Delete the guardrail
        print(f"Deleting guardrail '{guardrail_name}' with ID: {guardrail_id}")
        bedrock_client.delete_guardrail(guardrailIdentifier=guardrail_id)
        print(f"Successfully deleted guardrail: {guardrail_name}")
        return True

    except Exception as e:
        print(f"Error deleting guardrail: {e}")
        return False


def get_guardrail_id():
    """
    Get the guardrail ID for the Bitcoin advice guardrail.

    Returns:
        str or None: The guardrail ID if found, None otherwise
    """
    guardrail_name = "guardrail-no-bitcoin-advice"

    try:
        existing_guardrails = bedrock_client.list_guardrails()
        for guardrail in existing_guardrails.get("guardrails", []):
            if guardrail.get("name") == guardrail_name:
                guardrail_id = guardrail.get("id")
                print(f"Found guardrail '{guardrail_name}' with ID: {guardrail_id}")
                return guardrail_id

        print(f"Guardrail '{guardrail_name}' not found")
        return None

    except Exception as e:
        print(f"Error finding guardrail: {e}")
        return None

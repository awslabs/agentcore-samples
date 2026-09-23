from check_warranty import check_warranty_status
from get_customer_profile import get_customer_profile


def get_named_parameter(event, name):
    if name not in event:
        return None

    return event.get(name)


def lambda_handler(event, context):
    print(f"Event: {event}")
    print(f"Context: {context}")

    extended_tool_name = context.client_context.custom["bedrockAgentCoreToolName"]
    resource = extended_tool_name.split("___")[1]

    print(resource)

    if resource == "get_customer_profile":
        customer_id = get_named_parameter(event=event, name="customer_id")
        email = get_named_parameter(event=event, name="email")
        phone = get_named_parameter(event=event, name="phone")

        if not customer_id:
            return {
                "statusCode": 400,
                "body": "❌ Please provide customer_id",
            }

        try:
            customer_profile = get_customer_profile(customer_id=customer_id, email=email, phone=phone)
        except Exception as e:
            print(e)
            return {
                "statusCode": 400,
                "body": f"❌ {e}",
            }

        return {
            "statusCode": 200,
            "body": f"👤 Customer Profile Information: {customer_profile}",
        }

    elif resource == "check_warranty_status":
        serial_number = get_named_parameter(event=event, name="serial_number")
        customer_email = get_named_parameter(event=event, name="customer_email")

        if not serial_number:
            return {
                "statusCode": 400,
                "body": "❌ Please provide serial_number",
            }

        try:
            warranty_status = check_warranty_status(serial_number=serial_number, customer_email=customer_email)
        except Exception as e:
            print(e)
            return {
                "statusCode": 400,
                "body": f"❌ {e}",
            }

        return {
            "statusCode": 200,
            "body": warranty_status,
        }

    return {
        "statusCode": 400,
        "body": f"❌ Unknown toolname: {resource}",
    }

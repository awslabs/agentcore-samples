from check_warranty import check_warranty_status
from web_search import web_search


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

    if resource == "check_warranty_status":
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

    elif resource == "web_search":
        keywords = get_named_parameter(event=event, name="keywords")
        region = get_named_parameter(event=event, name="region") or "us-en"
        max_results = get_named_parameter(event=event, name="max_results") or 5

        if not keywords:
            return {
                "statusCode": 400,
                "body": "❌ Please provide keywords for search",
            }

        try:
            search_results = web_search(keywords=keywords, region=region, max_results=int(max_results))
        except Exception as e:
            print(e)
            return {
                "statusCode": 400,
                "body": f"❌ {e}",
            }

        return {
            "statusCode": 200,
            "body": f"🔍 Search Results: {search_results}",
        }

    return {
        "statusCode": 400,
        "body": f"❌ Unknown toolname: {resource}",
    }

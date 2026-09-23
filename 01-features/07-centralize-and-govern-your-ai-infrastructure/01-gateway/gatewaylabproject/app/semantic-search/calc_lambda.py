def get_named_parameter(event, name):
    return event[name]


def handle_add(event):
    firstNumber = int(get_named_parameter(event, "firstNumber"))
    secondNumber = int(get_named_parameter(event, "secondNumber"))
    return {"sum": firstNumber + secondNumber}


def handle_multiply(event):
    multiplicand = int(get_named_parameter(event, "multiplicand"))
    multiplier = int(get_named_parameter(event, "multiplier"))
    return {"product": multiplicand * multiplier}


def handle_divide(event):
    divisor = int(get_named_parameter(event, "divisor"))
    dividend = int(get_named_parameter(event, "dividend"))

    if divisor == 0:
        raise Exception("Divisor cannot be 0")

    quotient = dividend / divisor

    return {"quotient": quotient}


def handle_subtract(event):
    minuend = int(get_named_parameter(event, "minuend"))
    subtrahend = int(get_named_parameter(event, "subtrahend"))

    difference = minuend - subtrahend

    return {"difference": difference}


def lambda_handler(event, context):
    print(f"event: {event}")
    print(f"context: {context}")
    print(f"context.client_context: {context.client_context}")

    extended_tool_name = context.client_context.custom["bedrockAgentCoreToolName"]
    tool_name = extended_tool_name.split("___")[1]

    print(f"tool_name: {tool_name}")

    if tool_name == "add_numbers":
        result = handle_add(event)
    elif tool_name == "multiply_numbers":
        result = handle_multiply(event)
    elif tool_name == "divide_numbers":
        result = handle_divide(event)
    elif tool_name == "subtract_numbers":
        result = handle_subtract(event)
    else:
        result = f"Unrecognized tool_name: {tool_name}"

    print(f"result: {result}")
    return result

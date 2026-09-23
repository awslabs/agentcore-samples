"""
Calculation Tool - Mathematical operations for Gateway

This tool performs various mathematical calculations.
"""

import json
import math


def lambda_handler(event, context):
    """
    Lambda handler for calculation tool.

    Expected input:
    {
        "operation": "add" | "subtract" | "multiply" | "divide" | "power" | "sqrt" | "log",
        "operand1": number,
        "operand2": number (optional for sqrt, log)
    }

    Returns calculation result.
    """
    print(f"Calculation tool received event: {json.dumps(event)}")

    # Parse input
    body = event if isinstance(event, dict) else json.loads(event)
    operation = body.get("operation", "").lower()
    operand1 = body.get("operand1")
    operand2 = body.get("operand2")

    # Validate operation
    valid_operations = [
        "add",
        "subtract",
        "multiply",
        "divide",
        "power",
        "sqrt",
        "log",
        "abs",
        "round",
    ]

    if operation not in valid_operations:
        return {
            "statusCode": 400,
            "body": json.dumps(
                {
                    "tool": "calculation_tool",
                    "error": f"Invalid operation: {operation}. Valid operations: {valid_operations}",
                    "success": False,
                }
            ),
        }

    # Validate operands
    if operand1 is None:
        return {
            "statusCode": 400,
            "body": json.dumps(
                {
                    "tool": "calculation_tool",
                    "error": "operand1 is required",
                    "success": False,
                }
            ),
        }

    try:
        # Perform calculation
        result = None
        expression = ""

        if operation == "add":
            if operand2 is None:
                raise ValueError("operand2 is required for addition")
            result = operand1 + operand2
            expression = f"{operand1} + {operand2}"

        elif operation == "subtract":
            if operand2 is None:
                raise ValueError("operand2 is required for subtraction")
            result = operand1 - operand2
            expression = f"{operand1} - {operand2}"

        elif operation == "multiply":
            if operand2 is None:
                raise ValueError("operand2 is required for multiplication")
            result = operand1 * operand2
            expression = f"{operand1} × {operand2}"

        elif operation == "divide":
            if operand2 is None:
                raise ValueError("operand2 is required for division")
            if operand2 == 0:
                raise ValueError("Cannot divide by zero")
            result = operand1 / operand2
            expression = f"{operand1} ÷ {operand2}"

        elif operation == "power":
            if operand2 is None:
                raise ValueError("operand2 is required for exponentiation")
            result = operand1**operand2
            expression = f"{operand1} ^ {operand2}"

        elif operation == "sqrt":
            if operand1 < 0:
                raise ValueError("Cannot take square root of negative number")
            result = math.sqrt(operand1)
            expression = f"√{operand1}"

        elif operation == "log":
            if operand1 <= 0:
                raise ValueError("Logarithm requires positive number")
            base = operand2 if operand2 is not None else math.e
            result = math.log(operand1, base)
            expression = f"log_{base}({operand1})" if operand2 else f"ln({operand1})"

        elif operation == "abs":
            result = abs(operand1)
            expression = f"|{operand1}|"

        elif operation == "round":
            decimals = int(operand2) if operand2 is not None else 0
            result = round(operand1, decimals)
            expression = f"round({operand1}, {decimals})"

        calculation_result = {
            "operation": operation,
            "operand1": operand1,
            "operand2": operand2,
            "result": result,
            "expression": expression,
            "result_type": type(result).__name__,
        }

        response = {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "tool": "calculation_tool",
                    "result": calculation_result,
                    "success": True,
                }
            ),
        }

        print(f"Calculation result: {expression} = {result}")
        return response

    except ValueError as e:
        return {
            "statusCode": 400,
            "body": json.dumps({"tool": "calculation_tool", "error": str(e), "success": False}),
        }
    except Exception as e:
        return {
            "statusCode": 500,
            "body": json.dumps(
                {
                    "tool": "calculation_tool",
                    "error": f"Calculation error: {str(e)}",
                    "success": False,
                }
            ),
        }


# MCP Tool Definition for Gateway registration
TOOL_DEFINITION = {
    "name": "calculation_tool",
    "description": "Perform mathematical calculations. Supports: add, subtract, multiply, divide, power, sqrt, log, abs, round.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "description": "The mathematical operation to perform: 'add', 'subtract', 'multiply', 'divide', 'power', 'sqrt', 'log', 'abs', or 'round'",
            },
            "operand1": {
                "type": "number",
                "description": "First operand (or only operand for unary operations)",
            },
            "operand2": {
                "type": "number",
                "description": "Second operand (required for binary operations, optional for log to specify base)",
            },
        },
        "required": ["operation", "operand1"],
    },
}


if __name__ == "__main__":
    # Test the tool locally
    test_cases = [
        {"operation": "add", "operand1": 10, "operand2": 5},
        {"operation": "multiply", "operand1": 7, "operand2": 8},
        {"operation": "divide", "operand1": 100, "operand2": 4},
        {"operation": "sqrt", "operand1": 64},
        {"operation": "power", "operand1": 2, "operand2": 10},
        {"operation": "log", "operand1": 100, "operand2": 10},
    ]

    for i, test_event in enumerate(test_cases, 1):
        print(f"\n{'=' * 80}")
        print(f"Test Case {i}: {test_event}")
        print(f"{'=' * 80}")
        result = lambda_handler(test_event, None)
        print(f"{json.dumps(result, indent=2)}")

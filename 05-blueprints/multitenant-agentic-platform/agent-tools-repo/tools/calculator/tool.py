from strands import tool
import math
import ast
import operator


@tool
def calculator(expression: str) -> str:
    """
    Perform mathematical calculations.

    This tool evaluates mathematical expressions safely, supporting
    basic arithmetic and common mathematical functions.

    Args:
        expression: Mathematical expression to evaluate (e.g., "2 + 2", "sqrt(16)", "sin(pi/2)")

    Returns:
        Calculation result as string

    Example:
        result = calculator("2 * (3 + 4)")
        result = calculator("sqrt(144)")
    """
    try:
        # Safe evaluation using AST parsing
        allowed_operators = {
            ast.Add: operator.add,
            ast.Sub: operator.sub,
            ast.Mult: operator.mul,
            ast.Div: operator.truediv,
            ast.Pow: operator.pow,
            ast.USub: operator.neg,
            ast.UAdd: operator.pos,
        }

        allowed_functions = {
            "abs": abs,
            "round": round,
            "min": min,
            "max": max,
            "pow": pow,
            "sqrt": math.sqrt,
            "sin": math.sin,
            "cos": math.cos,
            "tan": math.tan,
            "log": math.log,
            "log10": math.log10,
            "exp": math.exp,
            "floor": math.floor,
            "ceil": math.ceil,
        }

        allowed_constants = {"pi": math.pi, "e": math.e}

        def safe_eval(node):
            if isinstance(node, ast.Constant):  # Numbers
                return node.value
            elif isinstance(node, ast.BinOp):  # Binary operations
                op_type = type(node.op)
                if op_type not in allowed_operators:
                    raise ValueError(f"Operator {op_type.__name__} not allowed")
                left = safe_eval(node.left)
                right = safe_eval(node.right)
                return allowed_operators[op_type](left, right)
            elif isinstance(node, ast.UnaryOp):  # Unary operations
                op_type = type(node.op)
                if op_type not in allowed_operators:
                    raise ValueError(f"Operator {op_type.__name__} not allowed")
                operand = safe_eval(node.operand)
                return allowed_operators[op_type](operand)
            elif isinstance(node, ast.Call):  # Function calls
                if not isinstance(node.func, ast.Name):
                    raise ValueError("Only simple function calls are allowed")
                func_name = node.func.id
                if func_name not in allowed_functions:
                    raise ValueError(f"Function '{func_name}' not allowed")
                args = [safe_eval(arg) for arg in node.args]
                return allowed_functions[func_name](*args)
            elif isinstance(node, ast.Name):  # Constants like pi, e
                if node.id not in allowed_constants:
                    raise ValueError(f"Name '{node.id}' not allowed")
                return allowed_constants[node.id]
            else:
                raise ValueError(f"Unsupported expression type: {type(node).__name__}")

        # Parse the expression
        tree = ast.parse(expression, mode="eval")
        result = safe_eval(tree.body)

        # Format result
        if isinstance(result, float):
            # Round to reasonable precision
            if result.is_integer():
                return f"Result: {int(result)}"
            else:
                return f"Result: {round(result, 10)}"
        else:
            return f"Result: {result}"

    except ZeroDivisionError:
        return "Error: Division by zero"
    except SyntaxError:
        return f"Error: Invalid syntax in expression: '{expression}'"
    except ValueError as e:
        return f"Error: {str(e)}"
    except Exception as e:
        return f"Error calculating expression: {str(e)}"

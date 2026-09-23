from flask import Flask, request, jsonify
import logging
import time

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)


@app.route("/ping", methods=["GET"])
def ping():
    """
    Health check endpoint
    Returns 200 OK if the service is healthy
    """
    logger.info("Received ping request")
    return jsonify({"status": "healthy", "message": "Service is running"}), 200


@app.route("/invocations", methods=["POST"])
def invocations():
    """
    Main invocation endpoint for processing requests
    Accepts JSON payload and returns processed response
    """
    try:
        logger.info("Received invocations request")

        # Get the JSON payload from the request
        payload = request.get_json()

        if not payload:
            logger.warning("Empty payload received")
            return jsonify({"status": "error", "message": "No payload provided"}), 400

        logger.info(f"Processing payload: {payload}")

        # Process the request (placeholder logic)
        # In a real implementation, this would call your agent/model
        response = {
            "status": "success",
            "message": "Request processed successfully",
            "data": {"received": payload, "processed_by": "vpc-fargate-agent"},
            "timestamp": time.time(),
        }

        logger.info("Request processed successfully")
        return jsonify(response), 200

    except Exception as e:
        logger.error(f"Error processing request: {str(e)}")
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "An error has occurred when processing the request",
                }
            ),
            500,
        )


if __name__ == "__main__":
    # Run on port 8080
    logger.info("Starting Flask application on port 8080")
    app.run(host="0.0.0.0", port=8080, debug=False)  # nosec  nosemgrep

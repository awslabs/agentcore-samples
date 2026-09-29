"""Run the same FastAPI app on AWS Lambda.

Mangum adapts ASGI to the Lambda event model, so `main.py` is unchanged between the
local run and the deployed one. Configuration arrives as Lambda environment
variables rather than a .env file.

The gateway reaches this through an API Gateway HTTP API. A Lambda Function URL would
be simpler, but this account's SCP blocks them -- both NONE and AWS_IAM auth types --
which surfaces as a bare 403 that looks like an auth bug.
"""

from main import app
from mangum import Mangum

handler = Mangum(app, lifespan="off")

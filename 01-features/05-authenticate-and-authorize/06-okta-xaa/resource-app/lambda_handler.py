"""AWS Lambda entrypoint for the resource app (Function URL / API Gateway).

Wraps the FastAPI app with Mangum so the same code runs locally (uvicorn) and on
Lambda. Set RESOURCE_AS_ISSUER and RESOURCE_API as Lambda environment variables.
"""

from main import app
from mangum import Mangum

handler = Mangum(app)

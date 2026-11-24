"""Real pinned botocore serializers/parsers, local wire fixtures; never an endpoint."""

import json

from botocore.parsers import create_parser
from botocore.serialize import create_serializer


def parsed_response(model, operation, body, params=None):
    op = model.operation_model(operation)
    if params is not None:
        create_serializer(model.metadata["protocol"]).serialize_to_request(params, op)
    return create_parser(model.metadata["protocol"]).parse(
        {
            "status_code": 200,
            "headers": {"content-type": "application/json"},
            "body": json.dumps(body).encode(),
        },
        op.output_shape,
    )

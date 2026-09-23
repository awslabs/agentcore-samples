#!/bin/bash

# Generate OpenAPI spec with dynamic stub API URL
# This script gets the Visa stub API URL from CloudFormation and creates the OpenAPI spec

set -e

echo "Generating OpenAPI spec for AgentCore Gateway..."

# Get the Visa stub API URL from CloudFormation
STUB_API_URL=$(aws cloudformation describe-stacks \
  --stack-name VisaStubStack \
  --region us-east-1 \
  --query 'Stacks[0].Outputs[?OutputKey==`VisaStubApiUrl`].OutputValue' \
  --output text)

if [ -z "$STUB_API_URL" ]; then
    echo "❌ Error: Could not get Visa stub API URL from CloudFormation"
    echo "Make sure VisaStubStack is deployed: ./deploy-visa-stub.sh"
    exit 1
fi

echo "✓ Found Visa stub API URL: $STUB_API_URL"

# Create directory if it doesn't exist
mkdir -p ../visa-b2b-spec/gateway

# Create the minimal OpenAPI spec with the dynamic URL
cat > ../visa-b2b-spec/gateway/visa-b2b-stub-openapi.json <<EOF
{
  "openapi": "3.0.1",
  "info": {
    "title": "Visa B2B Virtual Account Payment - Stub API (MVP)",
    "description": "Minimal OpenAPI spec for 4 core Visa B2B payment operations including GetSecurityCode",
    "version": "1.1"
  },
  "servers": [
    {
      "url": "${STUB_API_URL}",
      "description": "Visa B2B Stub API Gateway"
    }
  ],
  "paths": {
    "/vpa/v1/accountManagement/VirtualCardRequisition": {
      "post": {
        "summary": "Virtual Card Requisition",
        "description": "Request a virtual card for payment",
        "operationId": "VirtualCardRequisition",
        "requestBody": {
          "required": true,
          "content": {
            "application/json": {
              "schema": {
                "type": "object",
                "required": ["messageId", "buyerId"],
                "properties": {
                  "messageId": {
                    "type": "string",
                    "description": "Unique message identifier"
                  },
                  "buyerId": {
                    "type": "integer",
                    "description": "Buyer identifier"
                  },
                  "amount": {
                    "type": "number",
                    "description": "Payment amount"
                  },
                  "currency": {
                    "type": "string",
                    "description": "Currency code (e.g., USD)"
                  }
                }
              }
            }
          }
        },
        "responses": {
          "200": {
            "description": "Successful response",
            "content": {
              "application/json": {
                "schema": {
                  "type": "object"
                }
              }
            }
          }
        }
      }
    },
    "/vpa/v1/payment/ProcessPayments": {
      "post": {
        "summary": "Process Payment",
        "description": "Process a payment using virtual card",
        "operationId": "ProcessPayments",
        "requestBody": {
          "required": true,
          "content": {
            "application/json": {
              "schema": {
                "type": "object",
                "required": ["messageId", "buyerId"],
                "properties": {
                  "messageId": {
                    "type": "string"
                  },
                  "buyerId": {
                    "type": "integer"
                  },
                  "virtualCardId": {
                    "type": "string"
                  },
                  "amount": {
                    "type": "number"
                  }
                }
              }
            }
          }
        },
        "responses": {
          "200": {
            "description": "Successful response",
            "content": {
              "application/json": {
                "schema": {
                  "type": "object"
                }
              }
            }
          }
        }
      }
    },
    "/vpa/v1/payment/GetPaymentDetails": {
      "post": {
        "summary": "Get Payment Details",
        "description": "Retrieve payment status and details",
        "operationId": "GetPaymentDetails",
        "requestBody": {
          "required": true,
          "content": {
            "application/json": {
              "schema": {
                "type": "object",
                "required": ["messageId", "buyerId", "trackingNumber"],
                "properties": {
                  "messageId": {
                    "type": "string"
                  },
                  "buyerId": {
                    "type": "integer"
                  },
                  "trackingNumber": {
                    "type": "integer"
                  }
                }
              }
            }
          }
        },
        "responses": {
          "200": {
            "description": "Successful response",
            "content": {
              "application/json": {
                "schema": {
                  "type": "object"
                }
              }
            }
          }
        }
      }
    },
    "/vpa/v1/accountManagement/GetSecurityCode": {
      "post": {
        "summary": "Get Security Code (CVV2)",
        "description": "Retrieve CVV2 for a virtual card. NOTE: In production, CVV2 is delivered via secure email. This API is for demo/POC purposes only.",
        "operationId": "GetSecurityCode",
        "requestBody": {
          "required": true,
          "content": {
            "application/json": {
              "schema": {
                "type": "object",
                "required": ["messageId", "accountNumber", "expirationDate"],
                "properties": {
                  "messageId": {
                    "type": "string",
                    "description": "Unique message identifier"
                  },
                  "accountNumber": {
                    "type": "string",
                    "description": "Virtual card account number"
                  },
                  "expirationDate": {
                    "type": "string",
                    "description": "Card expiration date"
                  }
                }
              }
            }
          }
        },
        "responses": {
          "200": {
            "description": "Successful response with CVV2",
            "content": {
              "application/json": {
                "schema": {
                  "type": "object",
                  "properties": {
                    "GetSecurityCodeResponse": {
                      "type": "object",
                      "properties": {
                        "messageId": {
                          "type": "string"
                        },
                        "statusCode": {
                          "type": "string"
                        },
                        "statusDesc": {
                          "type": "string"
                        },
                        "cvv2": {
                          "type": "string",
                          "description": "3-digit CVV2 security code"
                        }
                      }
                    }
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}
EOF

echo "✓ Generated OpenAPI spec at: ../visa-b2b-spec/gateway/visa-b2b-stub-openapi.json"
echo "  Server URL: $STUB_API_URL"

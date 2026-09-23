# =============================================================================
# API Gateway (REST API with Cognito authorizer)
# =============================================================================

resource "aws_api_gateway_rest_api" "this" {
  name        = "Posts API ${local.suffix}"
  description = "Posts API with Cognito JWT authentication"

  body = jsonencode({
    openapi = "3.0.1"
    info = {
      title       = "Posts API ${local.suffix}"
      version     = "1.0.0"
      description = "Posts API authenticated via Cognito JWT"
    }
    components = {
      securitySchemes = {
        CognitoAuth = {
          type                              = "apiKey"
          name                              = "Authorization"
          in                                = "header"
          "x-amazon-apigateway-authtype"    = "cognito_user_pools"
          "x-amazon-apigateway-authorizer"  = {
            type         = "cognito_user_pools"
            providerARNs = [aws_cognito_user_pool.this.arn]
          }
        }
      }
      schemas = local.schemas
    }
    paths = {
      "/posts" = {
        post = {
          summary     = "Create a new post"
          operationId = "createPost"
          security    = [{ CognitoAuth = [
            "${local.resource_server_id}/read",
            "${local.resource_server_id}/write",
          ] }]
          requestBody = local.create_post_request_body
          responses   = local.create_post_responses
          "x-amazon-apigateway-integration" = {
            type = "mock"
            requestTemplates = {
              "application/json" = "{\"statusCode\": 201}"
            }
            responses = {
              default = {
                statusCode = "201"
                responseTemplates = {
                  "application/json" = jsonencode({
                    id     = 42
                    title  = "$input.path('$.title')"
                    body   = "$input.path('$.body')"
                    userId = "$input.path('$.userId')"
                  })
                }
              }
            }
          }
        }
      }
    }
  })

  depends_on = [aws_cognito_user_pool_domain.this]
}

# --- Deployment ---
resource "aws_api_gateway_deployment" "this" {
  rest_api_id = aws_api_gateway_rest_api.this.id

  triggers = {
    redeployment = sha1(jsonencode(aws_api_gateway_rest_api.this.body))
  }

  lifecycle {
    create_before_destroy = true
  }
}

# --- Stage ---
resource "aws_api_gateway_stage" "prod" {
  deployment_id = aws_api_gateway_deployment.this.id
  rest_api_id   = aws_api_gateway_rest_api.this.id
  stage_name    = "prod"
  description   = "Production deployment - ${local.suffix}"
}

# =============================================================================
# Shared schema fragments (used by the AgentCore target OpenAPI spec)
# =============================================================================
locals {
  api_gateway_url = "https://${aws_api_gateway_rest_api.this.id}.execute-api.${local.region}.amazonaws.com/${aws_api_gateway_stage.prod.stage_name}"

  schemas = {
    CreatePostRequest = {
      type     = "object"
      required = ["title", "body"]
      properties = {
        title  = { type = "string", description = "Title of the post" }
        body   = { type = "string", description = "Body text of the post" }
        userId = { type = "integer", description = "ID of the authoring user" }
      }
    }
    Post = {
      type = "object"
      properties = {
        id     = { type = "integer" }
        title  = { type = "string" }
        body   = { type = "string" }
        userId = { type = "integer" }
      }
    }
  }

  create_post_request_body = {
    required = true
    content = {
      "application/json" = {
        schema = { "$ref" = "#/components/schemas/CreatePostRequest" }
      }
    }
  }

  create_post_responses = {
    "201" = {
      description = "Post created"
      content = {
        "application/json" = {
          schema = { "$ref" = "#/components/schemas/Post" }
        }
      }
    }
    "401" = {
      description = "Unauthorized - invalid or missing JWT"
    }
  }
}

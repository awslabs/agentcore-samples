#!/bin/bash
set -e

GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${BLUE}======================================${NC}"
echo -e "${BLUE}  Weather Workflow Test${NC}"
echo -e "${BLUE}======================================${NC}"
echo ""

# Check deployment
if [ ! -f "deployment_info.json" ]; then
  echo -e "${RED}❌ deployment_info.json not found${NC}"
  echo "Run ./deploy.sh first"
  exit 1
fi

STATE_MACHINE_ARN=$(jq -r '.stateMachineArn' deployment_info.json)
TABLE_NAME=$(jq -r '.dynamoTableName' deployment_info.json)
REGION=$(jq -r '.region' deployment_info.json)

# How long to wait for an execution before giving up. The state machine allows
# InvokeHarness 300s per attempt and retries transient errors up to 3 times with
# exponential backoff, so a worst-case run is a little over 1200s. Wait past
# that, otherwise a slow-but-healthy run gets reported as a timeout.
POLL_INTERVAL=2
POLL_ATTEMPTS=650   # 650 x 2s = 1300s

echo "Region: $REGION"
# A state machine ARN is colon-delimited, so `basename` left it untouched and
# this line printed the full ARN. Take the field after the last colon.
echo "State Machine: ${STATE_MACHINE_ARN##*:}"
echo "DynamoDB Table: $TABLE_NAME"
echo ""

# Check for --use-samples flag
if [ "$1" == "--use-samples" ]; then
  echo -e "${BLUE}Using sample data from example_inputs.json${NC}"
  echo ""

  # Read samples. Emit one compact JSON object per line and iterate with
  # `while read`, NOT `for SAMPLE in $SAMPLES`: the inputs contain spaces
  # (e.g. {"city":"New York",...}), so word-splitting an unquoted $SAMPLES
  # would break each object apart and feed jq a fragment — the classic
  # "jq: parse error: Unfinished string at EOF" that aborted this whole test.
  COUNT=0

  while IFS= read -r SAMPLE; do
    [ -z "$SAMPLE" ] && continue
    COUNT=$((COUNT+1))
    CITY=$(echo "$SAMPLE" | jq -r '.city')
    DATE=$(echo "$SAMPLE" | jq -r '.date')

    echo -e "${GREEN}[Test $COUNT]${NC} Executing: $CITY on $DATE"

    EXEC_ARN=$(aws stepfunctions start-execution \
      --state-machine-arn "$STATE_MACHINE_ARN" \
      --input "$SAMPLE" \
      --region $REGION \
      --query 'executionArn' \
      --output text)

    echo "  Execution ARN: $EXEC_ARN"
    echo "  Waiting for completion..."

    # Wait for result. Handle every terminal status, and treat running-out
    # of attempts as a timeout instead of silently falling through to the
    # results section as if the run had succeeded.
    SETTLED=""
    for _ in $(seq 1 $POLL_ATTEMPTS); do
      STATUS=$(aws stepfunctions describe-execution \
        --execution-arn "$EXEC_ARN" \
        --region $REGION \
        --query 'status' \
        --output text)

      if [ "$STATUS" == "SUCCEEDED" ]; then
        echo -e "  ${GREEN}✓ Success${NC}"
        SETTLED="$STATUS"
        break
      elif [ "$STATUS" == "FAILED" ] || [ "$STATUS" == "TIMED_OUT" ] || [ "$STATUS" == "ABORTED" ]; then
        echo -e "  ${RED}✗ $STATUS${NC}"
        aws stepfunctions describe-execution \
          --execution-arn "$EXEC_ARN" \
          --region $REGION \
          --query '[error, cause]' \
          --output text
        exit 1
      fi
      sleep $POLL_INTERVAL
    done

    if [ -z "$SETTLED" ]; then
      echo -e "  ${RED}✗ Gave up after $((POLL_ATTEMPTS * POLL_INTERVAL))s waiting for the execution to finish${NC}"
      exit 1
    fi
    echo ""
  done < <(jq -c '.examples[0:3] | .[] | .input' example_inputs.json)

else
  # Interactive mode
  echo -e "${BLUE}Interactive Mode${NC}"
  echo "Enter weather query details (or press Ctrl+C to cancel)"
  echo ""

  read -p "City (e.g., Tokyo, New York): " CITY
  if [ -z "$CITY" ]; then
    echo -e "${RED}City is required${NC}"
    exit 1
  fi

  read -p "Date (e.g., today, tomorrow, 2024-12-25): " DATE
  if [ -z "$DATE" ]; then
    echo -e "${RED}Date is required${NC}"
    exit 1
  fi

  echo ""
  echo -e "${GREEN}Executing workflow...${NC}"

  INPUT="{\"city\":\"$CITY\",\"date\":\"$DATE\"}"

  EXEC_ARN=$(aws stepfunctions start-execution \
    --state-machine-arn "$STATE_MACHINE_ARN" \
    --input "$INPUT" \
    --region $REGION \
    --query 'executionArn' \
    --output text)

  echo "Execution ARN: $EXEC_ARN"
  echo ""
  echo "Monitoring execution..."

  SETTLED=""
  for _ in $(seq 1 $POLL_ATTEMPTS); do
    STATUS=$(aws stepfunctions describe-execution \
      --execution-arn "$EXEC_ARN" \
      --region $REGION \
      --query 'status' \
      --output text)

    if [ "$STATUS" == "SUCCEEDED" ]; then
      echo ""
      echo -e "${GREEN}✓ Execution succeeded!${NC}"
      SETTLED="$STATUS"
      break
    elif [ "$STATUS" == "FAILED" ] || [ "$STATUS" == "TIMED_OUT" ] || [ "$STATUS" == "ABORTED" ]; then
      echo ""
      echo -e "${RED}✗ Execution $STATUS${NC}"
      ERROR=$(aws stepfunctions describe-execution \
        --execution-arn "$EXEC_ARN" \
        --region $REGION \
        --query 'error' \
        --output text)
      CAUSE=$(aws stepfunctions describe-execution \
        --execution-arn "$EXEC_ARN" \
        --region $REGION \
        --query 'cause' \
        --output text)
      echo "Error: $ERROR"
      echo "Cause: $CAUSE"
      exit 1
    fi
    # Any non-terminal status (RUNNING, PENDING_REDRIVE): keep waiting.
    echo -n "."
    sleep $POLL_INTERVAL
  done

  if [ -z "$SETTLED" ]; then
    echo ""
    echo -e "${RED}✗ Gave up after $((POLL_ATTEMPTS * POLL_INTERVAL))s waiting for the execution to finish${NC}"
    exit 1
  fi

  echo ""
fi

# Show results
echo -e "${BLUE}======================================${NC}"
echo -e "${GREEN}Results from DynamoDB:${NC}"
echo -e "${BLUE}======================================${NC}"
echo ""

ITEMS=$(aws dynamodb scan \
  --table-name "$TABLE_NAME" \
  --region $REGION \
  --limit 5 \
  --output json)

ITEM_COUNT=$(echo "$ITEMS" | jq -r '.Items | length')

if [ "$ITEM_COUNT" -eq 0 ]; then
  echo "No results yet. Wait a moment and try:"
  echo "  aws dynamodb scan --table-name $TABLE_NAME"
else
  echo "$ITEMS" | jq -r '.Items[] |
    "🌤️  City: \(.city.S)
     Date: \(.date.S // "N/A")
     Temperature: \(.temperature_c.N)°C / \(.temperature_f.N)°F
     Conditions: \(.conditions.S // "N/A")
     Timestamp: \(.timestamp.S)
    "'
fi

echo ""
echo -e "${GREEN}✓ Test complete!${NC}"
echo ""
echo "To query specific city:"
echo "  aws dynamodb query --table-name $TABLE_NAME \\"
echo "    --index-name city-index \\"
echo "    --key-condition-expression \"city = :city\" \\"
echo "    --expression-attribute-values '{\":city\":{\"S\":\"YOUR_CITY\"}}'"
echo ""

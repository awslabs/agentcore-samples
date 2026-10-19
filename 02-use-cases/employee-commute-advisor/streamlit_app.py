"""
Employee Commute Advisor - Streamlit Frontend
Interactive UI for selecting employees and analyzing their commute to the Dublin office
"""

import streamlit as st
import pandas as pd
import boto3
import json
from datetime import datetime

# Page configuration
st.set_page_config(
    page_title="Employee Commute Advisor",
    page_icon="🚗",
    layout="wide"
)

def detect_deployment_region():
    """Detect which region the solution was deployed to"""
    import os
    
    # Priority 1: Check environment variable (allows manual override)
    env_region = os.environ.get('AWS_REGION') or os.environ.get('AWS_DEFAULT_REGION')
    if env_region and env_region in ['us-east-1', 'us-west-2', 'eu-west-1']:
        print(f"Using region from environment: {env_region}")
        return env_region
    
    # Priority 2: Try to find Runtime by checking all regions
    # This is more reliable than SSM parameter which might be stale
    for region in ['eu-west-1', 'us-east-1', 'us-west-2']:  # Check EU first since it's newer
        try:
            ssm = boto3.Session(profile_name='default', region_name=region).client('ssm')
            # Try to get runtime ID to confirm deployment in this region
            response = ssm.get_parameter(Name='/app/employee-commute-advisor/agentcore/runtime_id')
            if response['Parameter']['Value']:
                print(f"Detected deployment in region: {region}")
                return region
        except Exception:
            continue
    
    # Priority 3: Fall back to checking region parameter
    for region in ['eu-west-1', 'us-east-1', 'us-west-2']:
        try:
            ssm = boto3.Session(profile_name='default', region_name=region).client('ssm')
            response = ssm.get_parameter(Name='/app/employee-commute-advisor/config/region')
            detected_region = response['Parameter']['Value']
            print(f"Found region in SSM config: {detected_region}")
            return detected_region
        except Exception:
            continue
    
    # Default to us-west-2 if not found
    print("Could not detect region, defaulting to us-west-2")
    return 'us-west-2'

# Detect deployment region
DEPLOYMENT_REGION = detect_deployment_region()

# Initialize AWS session with default profile and Lambda client
session = boto3.Session(profile_name='default', region_name=DEPLOYMENT_REGION)
lambda_client = session.client('lambda')

# Lambda function name
LAMBDA_FUNCTION_NAME = 'employee-commute-advisor-invoker'

def load_employees():
    """Load employee data from CSV file"""
    try:
        df = pd.read_csv('employees.csv')
        return df
    except FileNotFoundError:
        st.error("employees.csv file not found. Please ensure it exists in the same directory.")
        return None

def invoke_commute_analysis(from_address, to_address):
    """Invoke the Lambda function to analyze commute"""
    try:
        payload = {
            'from_address': from_address,
            'to_address': to_address
        }
        
        response = lambda_client.invoke(
            FunctionName=LAMBDA_FUNCTION_NAME,
            InvocationType='RequestResponse',
            Payload=json.dumps(payload)
        )
        
        result = json.loads(response['Payload'].read())
        return result
    except Exception as e:
        return {'error': str(e)}

# Title and description
st.title("🚗 Employee Commute Advisor")
st.markdown("**Analyze employee commute times to Grafton Street, Dublin office**")
st.markdown("---")

# Load employee data
employees_df = load_employees()

if employees_df is not None:
    # Create two columns for the layout
    col1, col2 = st.columns([1, 2])
    
    with col1:
        st.subheader("📋 Select Employee")
        
        # Employee selection dropdown
        employee_names = employees_df['name'].tolist()
        selected_employee_name = st.selectbox(
            "Choose an employee:",
            employee_names,
            key="employee_selector"
        )
        
        # Get selected employee details
        selected_employee = employees_df[employees_df['name'] == selected_employee_name].iloc[0]
        
        # Display employee details
        st.markdown("### Employee Details")
        st.info(f"**Name:** {selected_employee['name']}")
        st.info(f"**Home Address:** {selected_employee['home_address']}")
        st.info(f"**Company Address:** {selected_employee['company_address']}")
        st.info(f"**Email:** {selected_employee['email']}")
        
        # Run analysis button
        analyze_button = st.button("🚀 Run Commute Analysis", type="primary", use_container_width=True)
    
    with col2:
        st.subheader("📊 Commute Analysis Results")
        
        if analyze_button:
            with st.spinner("Analyzing commute... This may take a moment..."):
                # Invoke Lambda function
                result = invoke_commute_analysis(
                    selected_employee['home_address'],
                    selected_employee['company_address']
                )
                
                if 'error' in result:
                    st.error(f"Error: {result['error']}")
                else:
                    # Parse the response
                    try:
                        if result.get('statusCode') == 200:
                            body = json.loads(result.get('body', '{}'))
                            
                            # Display success message
                            st.success("✅ Analysis Complete!")
                            
                            # Display SNS Message ID
                            if 'sns_message_id' in body:
                                st.info(f"📧 Email notification sent! Message ID: `{body['sns_message_id']}`")
                            
                            # Display agent response
                            if 'agent_response' in body:
                                st.markdown("### 🤖 Agent Analysis")
                                st.markdown(body['agent_response'])
                            
                            # Display timestamp
                            st.caption(f"Analysis completed at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                        else:
                            st.error(f"Error: Status code {result.get('statusCode')}")
                            st.json(result)
                    except Exception as e:
                        st.error(f"Error parsing response: {str(e)}")
                        st.json(result)
        else:
            st.info("👈 Select an employee and click 'Run Commute Analysis' to begin")
            
            # Show sample data table
            st.markdown("### 📈 All Employees")
            st.dataframe(
                employees_df,
                use_container_width=True,
                hide_index=True
            )
    
    # Footer
    st.markdown("---")
    st.markdown("""
    **About this application:**
    - Fetches real-time traffic data from TomTom APIs
    - Uses AWS AgentCore with Claude 3.7 Sonnet for intelligent analysis
    - Sends detailed email notifications via Amazon SNS
    - Provides commute recommendations based on current conditions
    """)
else:
    st.error("Unable to load employee data. Please check that employees.csv exists.")

# Add sidebar with information
with st.sidebar:
    st.header("ℹ️ Information")
    st.markdown("""
    **How to use:**
    1. Select an employee from the dropdown
    2. Review their details
    3. Click 'Run Commute Analysis'
    4. View the results and email confirmation
    
    **Features:**
    - Real-time traffic analysis
    - AI-powered recommendations
    - Email notifications to employees
    - Historical commute patterns
    
    **Technical Stack:**
    - Streamlit (Frontend)
    - AWS Lambda (Backend)
    - Amazon Bedrock AgentCore
    - TomTom Traffic API
    - Amazon SNS (Notifications)
    """)
    
    st.markdown("---")
    st.markdown(f"**AWS Region:** {DEPLOYMENT_REGION}")
    st.markdown(f"**Lambda Function:** `{LAMBDA_FUNCTION_NAME}`")

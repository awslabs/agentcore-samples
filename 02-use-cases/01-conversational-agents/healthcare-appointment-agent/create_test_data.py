from dotenv import load_dotenv
import utils
import os
import json
import requests
from requests_auth_aws_sigv4 import AWSSigV4

load_dotenv()

# create boto3 session and client
(boto_session, agentcore_client) = utils.create_agentcore_client()

dataStoreEndpoint = os.getenv("healthlake_endpoint")


def ingest_data(patientDataFile, immunizationDataFile):
    resourcePath = "Patient"
    fhirEndpoint = dataStoreEndpoint + resourcePath + "/"

    auth = AWSSigV4("healthlake", session=boto_session)

    with open(patientDataFile) as json_body:
        patientJson = json.load(json_body)

    for entry in patientJson["entry"]:
        print(f"FHIR Endpoint: {fhirEndpoint}, Patient Id: {entry['resource']['id']}")
        requests.put(fhirEndpoint + entry["resource"]["id"], json=entry["resource"], auth=auth)

    resourcePath = "Immunization"
    fhirEndpoint = dataStoreEndpoint + resourcePath + "/"

    with open(immunizationDataFile) as json_body:
        immunizationJson = json.load(json_body)

    print("FHIR Endpoint: ", fhirEndpoint)
    for entry in immunizationJson["entry"]:
        print(f"Ingesting Immunization id: {entry['resource']['id']}")
        requests.post(fhirEndpoint, json=entry["resource"], auth=auth)


if __name__ == "__main__":
    ingest_data(
        patientDataFile="./test_data/patient.json",
        immunizationDataFile="./test_data/immunization.json",
    )

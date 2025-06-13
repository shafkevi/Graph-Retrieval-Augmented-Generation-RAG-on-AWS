import os
import boto3
import json

import uvicorn
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import asyncio

import jwt
import hashlib
import urllib.parse
from graphrag_toolkit.lexical_graph.storage import VectorStoreFactory, GraphStoreFactory
from graphrag_toolkit.lexical_graph import LexicalGraphQueryEngine
from graphrag_toolkit.lexical_graph.storage.vector import to_embedded_query

# Set up environment variables
aws_region = os.environ.get('AWS_REGION', 'us-east-1')
NEPTUNE_GRAPH_ID = os.environ.get('NEPTUNE_GRAPH_ID')
NEPTUNE_GRAPH_ENDPOINT = os.environ.get('NEPTUNE_GRAPH_ENDPOINT')
stackName = os.environ.get('stackName')
embeddingModel = os.environ.get('EMBEDDING_MODEL')
USER_POOL_ID = os.environ.get('USER_POOL_ID')
IDENTITY_POOL_ID = os.environ.get('IDENTITY_POOL_ID')
RESPONSE_MODEL = os.environ.get('RESPONSE_MODEL', 'us.anthropic.claude-3-7-sonnet-20250219-v1:0')  # Default model for GraphRAG
BEDROCK_MODEL = os.environ.get("BEDROCK_MODEL", "anthropic.claude-3-haiku-20240307-v1:0")
# Initialize AWS clients
cognito_identity = boto3.client('cognito-identity', region_name=aws_region)

# Initialize GraphRAG components
NEPTUNE_CONNECTION_INFO = "neptune-graph://"+ NEPTUNE_GRAPH_ID


SYSTEM = os.environ.get("SYSTEM", "You are a helpful assistant.")

app = FastAPI()
bedrock = boto3.Session().client("bedrock-runtime")


def get_vector_and_graph_stores():
    """Initialize vector and graph stores"""
    vector_store = VectorStoreFactory.for_vector_store(NEPTUNE_CONNECTION_INFO)
    graph_store = GraphStoreFactory.for_graph_store(NEPTUNE_CONNECTION_INFO)
    
    return vector_store, graph_store

def get_tenant_id(cognito_sub):
    normalized_sub = urllib.parse.unquote(cognito_sub)
    tenant_id = hashlib.md5(normalized_sub.encode()).hexdigest()[:10].lower()
    return tenant_id


def get_query_engine(cognito_sub):
    """Get query engine with tenant_id for user segregation"""
    vector_store, graph_store = get_vector_and_graph_stores()
    tenant_id = get_tenant_id(cognito_sub)
    print(f"Original cognito_sub: {cognito_sub}")
    print(f"Generated tenant_id for query: {tenant_id}")
    # Use traversal_based_search with tenant_id for multitenancy
    query_engine = LexicalGraphQueryEngine.for_traversal_based_search(
        graph_store, 
        vector_store,
        tenant_id=tenant_id,  # Use cognito_sub as tenant_id for multitenancy
        #response_model=RESPONSE_MODEL  # Use the configured response model
    )
    
    return query_engine

def parse_id_token(id_token):
    """Parse and validate the ID token from the request"""
    try:
        
        if not id_token:
            return {
                'statusCode': 400,
                'body': json.dumps({'message': 'ID token is missing'}),
            }
        
        print(f"GraphRAG User token: {id_token}")
        
        if not IDENTITY_POOL_ID or not USER_POOL_ID:
            return {
                'statusCode': 500,
                'body': json.dumps({'message': 'Environment variables for IdentityPoolId or UserPoolId are missing'}),
            }
        
        # Decode the ID token to extract claims
        decoded_token = jwt.decode(id_token, options={"verify_signature": False})
        
        if not decoded_token:
            raise Exception('Invalid ID token')
        
        sub = decoded_token.get('sub')
        print(f'GraphRAG Decoded token sub: {sub}')
        
        # Populate the Logins property
        logins = {
            f'cognito-idp.{aws_region}.amazonaws.com/{USER_POOL_ID}': id_token
        }
        
        # Get Identity ID using the ID token
        response = cognito_identity.get_id(
            IdentityPoolId=IDENTITY_POOL_ID,
            Logins=logins
        )
        
        identity_id = response['IdentityId']
        print(f'GraphRAG Identity ID: {identity_id}')
        
        return {
            'statusCode': 200,
            'message': 'Successfully retrieved identityId for GraphRAG',
            'identityId': identity_id
        }
        
    except Exception as e:
        print(f'Error getting identityId for GraphRAG: {e}')
        return {
            'statusCode': 500,
            'body': json.dumps({
                'message': 'Failed to get identityId for GraphRAG',
                'error': str(e),
            }),
        }


#  Streaming stuff
async def converse_bedrock_stream(conversation, model='us.anthropic.claude-3-7-sonnet-20250219-v1:0'):
    print('converse_bedrock_stream.STARTING!')
    response = bedrock.converse_stream(
        modelId=model,
        messages=conversation,
        inferenceConfig={ "maxTokens": 1000, "temperature": 0.0, "topP": 0.9 },
    )
    print('converse_bedrock_stream.response',response)
    stream = response.get('stream')
    if stream:
        print('converse_bedrock_stream.stream', stream)
        for event in stream:
            print('converse_bedrock_stream.event', event)
            if "contentBlockDelta" in event:
                yield event["contentBlockDelta"]["delta"]["text"] or ""
                await asyncio.sleep(0.01)
            if "messageStop" in event:
                yield "\n"
                await asyncio.sleep(0.01)






# @app.get("/")
# @app.post("/")
# async def api_stream(request: QueryRequest):
#     if not request.query:
#         return None

#     return StreamingResponse(
#         bedrock_stream(request.query),
#         media_type="text/event-stream",
#         headers={
#             "Cache-Control": "no-cache",
#             "Connection": "keep-alive",
#         },
#     )


# Define the request model
class QueryRequest(BaseModel):
    query: str
    idToken: str
    history: list = []
    model: str = BEDROCK_MODEL
    promptOverride: dict = {}
    strategy: str = "graphrag"


@app.get("/")
@app.post("/")
# async def lambda_handler(body, context):
async def lambda_handler(request: QueryRequest):
    print('FASTAPI.lambda_handler')
    id_token_result = parse_id_token(request.idToken)
    
    if id_token_result['statusCode'] != 200:
        return {
            'statusCode': id_token_result['statusCode'],
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token',
                'Access-Control-Allow-Methods': 'OPTIONS,POST,GET'
            },
            'body': id_token_result['body']
        }
    identity_id = id_token_result['identityId']
    print(f"GraphRAG run on behalf of: {identity_id}")
    tenant_id = get_tenant_id(identity_id)
        
    try:
        conversation = []
        
        # Extract parameter
        history = request.history or []
        conversation = [h for h in history]
        conversation.append({
            "role": "user",
            "content": [{"text":request.query}]
        })
        print('conversation',conversation)
        print('request.model', request.model)
        return StreamingResponse(
            converse_bedrock_stream(
                conversation, 
                model=request.model
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            }
        )

    except Exception as e:
        import traceback
        print('Exception!', e)
        print(traceback.format_exc())



async def invoke_bedrock_stream(query: str):
    instruction = f"""
    You are a helpful assistant. Please provide an answer to the user's query
    <query>{query}</query>.
    """
    body = json.dumps(
        {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1024,
            "system": SYSTEM,
            "temperature": 0.1,
            "top_k": 10,
            "messages": [
                {
                    "role": "user",
                    "content": instruction,
                }
            ],
        }
    )

    response = bedrock.invoke_model_with_response_stream(
        modelId=BEDROCK_MODEL, body=body
    )

    stream = response.get("body")
    if stream:
        for event in stream:
            chunk = event.get("chunk")
            if chunk:
                message = json.loads(chunk.get("bytes").decode())
                if message["type"] == "content_block_delta":
                    yield message["delta"]["text"] or ""
                    await asyncio.sleep(0.01)
                elif message["type"] == "message_stop":
                    yield "\n"
                    await asyncio.sleep(0.01)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))

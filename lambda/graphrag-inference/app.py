import os
import json
import jwt
import hashlib
import urllib.parse
import boto3
from graphrag_toolkit.lexical_graph.storage import VectorStoreFactory, GraphStoreFactory
from graphrag_toolkit.lexical_graph import LexicalGraphQueryEngine
from graphrag_toolkit.lexical_graph.storage.vector import to_embedded_query
#import nest_asyncio
import uvicorn
from fastapi import FastAPI
from fastapi.responses import RedirectResponse, StreamingResponse
app = FastAPI()

# Apply nest_asyncio for Lambda environment
#nest_asyncio.apply()

# Set logging configuration

# Set up environment variables
aws_region = os.environ.get('AWS_REGION', 'us-east-1')
NEPTUNE_GRAPH_ID = os.environ.get('NEPTUNE_GRAPH_ID')
NEPTUNE_GRAPH_ENDPOINT = os.environ.get('NEPTUNE_GRAPH_ENDPOINT')
stackName = os.environ.get('stackName')
embeddingModel = os.environ.get('EMBEDDING_MODEL')
USER_POOL_ID = os.environ.get('USER_POOL_ID')
IDENTITY_POOL_ID = os.environ.get('IDENTITY_POOL_ID')
RESPONSE_MODEL = os.environ.get('RESPONSE_MODEL', 'us.anthropic.claude-3-7-sonnet-20250219-v1:0')  # Default model for GraphRAG

# Initialize AWS clients
cognito_identity = boto3.client('cognito-identity', region_name=aws_region)

# Initialize Bedrock:
bedrock = boto3.client('bedrock-runtime')

# Initialize GraphRAG components
NEPTUNE_CONNECTION_INFO = "neptune-graph://"+ NEPTUNE_GRAPH_ID

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

def parse_id_token(event):
    """Parse and validate the ID token from the request"""
    try:
        body = json.loads(event['body']) if isinstance(event['body'], str) else event['body']
        id_token = body.get('idToken')
        
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


def bedrock_sync(conversation, model='us.anthropic.claude-3-7-sonnet-20250219-v1:0'):
    response = bedrock.converse(
        modelId=model,
        messages=conversation,
        inferenceConfig={ "maxTokens": 1000, "temperature": 0.0, "topP": 0.9 },
    )
    print('bedrock_sync.response', response)
    response_body = response["output"]["message"]["content"][0]["text"]
    print('bedrock_sync.response_body', response_body)
    return response_body or ""


async def bedrock_stream(conversation, model='us.anthropic.claude-3-7-sonnet-20250219-v1:0'):
    response = bedrock.converse_stream(
        modelId=model,
        messages=conversation,
        inferenceConfig={ "maxTokens": 1000, "temperature": 0.0, "topP": 0.9 },
    )
    stream = response.get('body')
    if stream:
        for event in stream:
            chunk = event.get('chunk')
            if chunk:
                message = json.loads(chunk.get("bytes").decode())
                if message['type'] == "content_block_delta":
                    yield message['delta']['text'] or ""
                elif message['type'] == "message_stop":
                    yield "\n"



@app.post("/")
def lambda_handler(event, context):
    conversation = []
    if event.get('isBase64Encoded'):
        import base64
        body = json.loads(base64.b64decode(event['body']).decode('utf-8'))
    else:
        body = json.loads(event['body']) if isinstance(event['body'], str) else event['body']
    
    # Extract parameters
    query = body.get('query', '')
    history = body.get('history', [])
    conversation = [h for h in history]
    conversation.append({
        "role": "user",
        "content": query
    })
    return StreamingResponse(bedrock_stream(conversation), media_type="text/html")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))



# def lambda_handler(event, context):
#     """Main Lambda handler for GraphRAG inference - Streaming Response"""
    
#     print(f'GraphRAG Lambda Event: {json.dumps(event)}')
    
#     # Parse and validate ID token
#     id_token_result = parse_id_token(event)
    
#     if id_token_result['statusCode'] != 200:
#         return {
#             'statusCode': id_token_result['statusCode'],
#             'headers': {
#                 'Content-Type': 'application/json',
#                 'Access-Control-Allow-Origin': '*',
#                 'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token',
#                 'Access-Control-Allow-Methods': 'OPTIONS,POST,GET'
#             },
#             'body': id_token_result['body']
#         }
    
#     identity_id = id_token_result['identityId']
#     print(f"GraphRAG run on behalf of: {identity_id}")
    
#     # Parse request body
#     try:
#         if event.get('isBase64Encoded'):
#             import base64
#             body = json.loads(base64.b64decode(event['body']).decode('utf-8'))
#         else:
#             body = json.loads(event['body']) if isinstance(event['body'], str) else event['body']
        
#         # Extract parameters
#         query = body.get('query', '')
#         tenant_id=get_tenant_id(identity_id)
#         if not query:
#             return {
#                 'statusCode': 400,
#                 'headers': {
#                     'Content-Type': 'application/json',
#                     'Access-Control-Allow-Origin': '*',
#                     'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token',
#                     'Access-Control-Allow-Methods': 'OPTIONS,POST,GET'
#                 },
#                 'body': json.dumps({'error': 'Query is required'})
#             }
        
#         print(f'GraphRAG Query: {query}')
#         print(f'GraphRAG Identity ID: {identity_id}')
        
#         # Execute GraphRAG query - this is the complete workflow
#         try:
#             query_engine = get_query_engine(identity_id)
            
#             print(f"Executing GraphRAG query: {query}")
#             # This performs the complete GraphRAG workflow:
#             # 1. Semantic search in knowledge graph
#             # 2. Context retrieval 
#             # 3. LLM inference with retrieved context
#             # 4. Final response generation
#             # response = query_engine.query(query)
            
#             # Break apart what the _query_ method does in the query_engine
#             results = query_engine.retrieve(query)
#             # print('query_engine.retrieve',results)
#             # r = results[0]
#             # print('query_engine.retrieve.Node0', r)
#             # print('query_engine.retrieve.Node0.get_content', r.get_content())
#             # print('query_engine.retrieve.Node0.get_score', r.get_score())
#             # print('query_engine.retrieve.Node0.get_text', r.get_text())
#             # print('query_engine.retrieve.Node0.metadata', r.metadata)
            
#             # json_formatted_context = query_engine._format_context(
#             #     search_results=results,
#             #     context_format='json'
#             # )
#             # print('query_engine.retrieve.json_formatted_context', json_formatted_context)
            
            
#             # bedrock_xml_formatted_context = query_engine._format_context(
#             #     search_results=results,
#             #     context_format='bedrock_xml'
#             # )
#             # print('query_engine.retrieve.bedrock_xml_formatted_context', bedrock_xml_formatted_context)
            
#             text_formatted_context = query_engine._format_context(
#                 search_results=results,
#                 context_format='text'
#             )
#             print('query_engine.retrieve.text_formatted_context', text_formatted_context)
            
            
#             history = body.get('history', [])
#             conversation = [h for h in history]
#             conversation.append({
#                 "role": "user",
#                 "content": [{"text": 
#                     f"""Use the following context to answer the query at the end: 
#                     <context>{text_formatted_context}<context>
#                     <query>{query}</query>"""
#                 }]
#             })
#             print('conversation',conversation)
#             response = bedrock_sync(conversation)
#             print('lambda_handler.bedrock_sync.response', response)
            
#             return response
            
#             response = query_engine.query(query)
#             # Extract the response text
#             response_text = str(response)
#             print(f'query_engine.query.response_text: {response_text}')
#             print(f'query_engine.query.get_formatted_sources: {response.get_formatted_sources()}')
            

            
#             # Create document metadata indicating this came from GraphRAG
#             document_metadata = [{
#                 'content': 'Response generated using GraphRAG knowledge graph traversal and vector search',
#                 'metadata': {
#                     'source': 'knowledge_graph',
#                     'type': 'graphrag',
#                     'query': query,
#                     'tenant_id': tenant_id,
#                     'model': RESPONSE_MODEL,
#                     'method': 'traversal_based_search'
#                 }
#             }]
            
#             # For streaming response, we need to format it properly
#             # Send metadata first, then the response
#             metadata_prefix = f"_~_{json.dumps(document_metadata)}_~_\n\n"
#             full_response = metadata_prefix + response_text
            
#             return full_response
            
#         except Exception as e:
#             import traceback
#             print(traceback.format_exc())
#             error_msg = f'Error executing GraphRAG query: {str(e)}'
#             print(error_msg)
#             return {
#                 'statusCode': 500,
#                 'body': json.dumps({'error': error_msg})
#             }
        
        
#     except Exception as e:
#         print(f'Error processing GraphRAG request: {e}')
#         return {
#             'statusCode': 500,
#             'body': json.dumps({'error': f'Failed to process GraphRAG request: {str(e)}'})
#         }
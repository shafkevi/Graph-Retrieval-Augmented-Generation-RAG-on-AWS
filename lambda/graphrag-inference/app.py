import os
import json
import jwt
import hashlib
import urllib.parse
import boto3
from graphrag_toolkit.lexical_graph.storage import VectorStoreFactory, GraphStoreFactory
from graphrag_toolkit.lexical_graph import LexicalGraphQueryEngine
from aws_lambda_powertools.utilities.streaming import ResponseStream
#import nest_asyncio

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

class GraphRAGStream(ResponseStream):
    def __init__(self, streaming_format='default'):
        super().__init__()
        self.streaming_format = streaming_format
        
    def transform(self, chunk: str) -> str:
        # Format based on the streaming format parameter
        if self.streaming_format == 'fetch-event-source':
            return f"event: message\ndata: {chunk}\n\n"
        return chunk

def lambda_handler(event, context):
    """Main Lambda handler for GraphRAG inference - Streaming Response"""
    
    print(f'GraphRAG Lambda Event: {json.dumps(event)}')
    
    # Parse and validate ID token
    id_token_result = parse_id_token(event)
    
    if id_token_result['statusCode'] != 200:
        return {
            'statusCode': id_token_result['statusCode'],
            'body': id_token_result.get('body', json.dumps({'error': 'Authentication error'}))
        }
    
    identity_id = id_token_result['identityId']
    print(f"GraphRAG run on behalf of: {identity_id}")
    
    # Parse request body
    try:
        if event.get('isBase64Encoded'):
            import base64
            body = json.loads(base64.b64decode(event['body']).decode('utf-8'))
        else:
            body = json.loads(event['body']) if isinstance(event['body'], str) else event['body']
        
        # Extract parameters
        query = body.get('query', '')
        streaming_format = body.get('streamingFormat', 'default')
        tenant_id = get_tenant_id(identity_id)
        
        if not query:
            return {
                'statusCode': 400,
                'body': json.dumps({'error': 'Query is required'})
            }
        
        # Initialize the response stream
        stream = GraphRAGStream(streaming_format=streaming_format)
        
        try:
            # Execute GraphRAG query
            query_engine = get_query_engine(identity_id)
            
            # Create document metadata
            document_metadata = [{
                'content': 'Response generated using GraphRAG knowledge graph traversal and vector search',
                'metadata': {
                    'source': 'knowledge_graph',
                    'type': 'graphrag',
                    'query': query,
                    'tenant_id': tenant_id,
                    'model': RESPONSE_MODEL,
                    'method': 'traversal_based_search'
                }
            }]
            
            # Send metadata first (this doesn't go through the transform method)
            stream.write(f"_~_{json.dumps(document_metadata)}_~_\n\n")
            
            # Get response and stream it
            response = query_engine.query(query)
            response_text = str(response)
            
            # Write the response to the stream
            stream.write(response_text)
            
            # Return the streaming response
            return stream.response()
            
        except Exception as e:
            error_msg = f'Error executing GraphRAG query: {str(e)}'
            print(error_msg)
            return {
                'statusCode': 500,
                'body': json.dumps({'error': error_msg})
            }
        
    except Exception as e:
        print(f'Error processing GraphRAG request: {e}')
        return {
            'statusCode': 500,
            'body': json.dumps({'error': f'Failed to process GraphRAG request: {str(e)}'})
        }


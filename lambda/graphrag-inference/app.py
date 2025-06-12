import os
import json
import jwt
import hashlib
import urllib.parse
import boto3
import logging
logger = logging.getLogger(__name__)
from graphrag_toolkit.lexical_graph.storage import VectorStoreFactory, GraphStoreFactory
from graphrag_toolkit.lexical_graph import LexicalGraphQueryEngine

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

import time
from graphrag_toolkit.lexical_graph.config import GraphRAGConfig
from graphrag_toolkit.lexical_graph.storage.vector import to_embedded_query
from llama_index.core.base.response.schema import Response, RESPONSE_TYPE
from llama_index.core.schema import QueryBundle
class QueryEngine(LexicalGraphQueryEngine):
    print('Init Custom QueryEngine')
    @classmethod
    def special_query(self, query_bundle: QueryBundle) -> RESPONSE_TYPE:
        """
        Executes a query against the system and processes the results to generate a
        final response. The method applies embedding on the query, retrieves relevant
        data, processes the data through registered post-processors, formats the
        context, and generates a response.

        Args:
            query_bundle: An instance of QueryBundle containing the query string and
                additional data required for the query.

        Returns:
            Response: An instance of the Response class. It contains the generated
                response, the source nodes used for building the response, and
                metadata such as timing details and applied configurations.

        Raises:
            Exception: If any error occurs during query processing, it is logged and
                re-raised.
        """
        try:

            start = time.time()
            print('CustomQueryEngine._query.to_embedded_query')
            query_bundle = to_embedded_query(query_bundle, GraphRAGConfig.embed_model)
            
            print('CustomQueryEngine._query.retrieve')
            results = super().retriever.retrieve(query_bundle)

            end_retrieve = time.time()

            for post_processor in super().post_processors:
                results = post_processor.postprocess_nodes(results, query_bundle)

            end_postprocessing = time.time()

            print('CustomQueryEngine._query._format_context')
            context = super()._format_context(results, super().context_format)
            print('CustomQueryEngine._query._generate_response')
            answer = super()._generate_response(query_bundle, context)

            end = time.time()

            retrieve_ms = (end_retrieve - start) * 1000
            postprocess_ms = (end_postprocessing - end_retrieve) * 1000
            answer_ms = (end - end_retrieve) * 1000
            total_ms = (end - start) * 1000

            metadata = {
                'retrieve_ms': retrieve_ms,
                'postprocessing_ms': postprocess_ms,
                'answer_ms': answer_ms,
                'total_ms': total_ms,
                'context_format': super().context_format,
                'retriever': f'{type(super().retriever).__name__}: {super().retriever.__dict__}',
                'query': query_bundle.query_str,
                'postprocessors': [type(p).__name__ for p in super().post_processors],
                'context': context,
                'num_source_nodes': len(results)
            }

            return Response(
                response=answer,
                source_nodes=results,
                metadata=metadata
            )
        except Exception as e:
            logger.exception('Error in query processing')
            raise

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
    query_engine = QueryEngine.for_traversal_based_search(
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

def lambda_handler(event, context):
    """Main Lambda handler for GraphRAG inference - Streaming Response"""
    
    print(f'GraphRAG Lambda Event: {json.dumps(event)}')
    
    # Parse and validate ID token
    id_token_result = parse_id_token(event)
    
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
    
    # Parse request body
    try:
        if event.get('isBase64Encoded'):
            import base64
            body = json.loads(base64.b64decode(event['body']).decode('utf-8'))
        else:
            body = json.loads(event['body']) if isinstance(event['body'], str) else event['body']
        
        # Extract parameters
        query = body.get('query', '')
        tenant_id=get_tenant_id(identity_id)
        if not query:
            return {
                'statusCode': 400,
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*',
                    'Access-Control-Allow-Headers': 'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token',
                    'Access-Control-Allow-Methods': 'OPTIONS,POST,GET'
                },
                'body': json.dumps({'error': 'Query is required'})
            }
        
        print(f'GraphRAG Query: {query}')
        print(f'GraphRAG Identity ID: {identity_id}')
        
        # Execute GraphRAG query - this is the complete workflow
        try:
            query_engine = get_query_engine(identity_id)
            
            print(f"Executing GraphRAG query: {query}")
            # This performs the complete GraphRAG workflow:
            # 1. Semantic search in knowledge graph
            # 2. Context retrieval 
            # 3. LLM inference with retrieved context
            # 4. Final response generation
            response = query_engine.special_query(query)
            
            # Break apart what the _query_ method does in the query_engine
            # query_bundle = to_embedded_query(query_bundle, GraphRAGConfig.embed_model)
            
            # Extract the response text
            response_text = str(response)
            print(f'GraphRAG response: {response_text}')
            
            # Create document metadata indicating this came from GraphRAG
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
            
            # For streaming response, we need to format it properly
            # Send metadata first, then the response
            metadata_prefix = f"_~_{json.dumps(document_metadata)}_~_\n\n"
            full_response = metadata_prefix + response_text
            
            return full_response
            
        except Exception as e:
            import traceback
            print(traceback.format_exc())
            
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